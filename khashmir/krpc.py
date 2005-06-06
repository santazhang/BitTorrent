# The contents of this file are subject to the BitTorrent Open Source License
# Version 1.0 (the License).  You may not copy or use this file, in either
# source code or executable form, except in compliance with the License.  You
# may obtain a copy of the License at http://www.bittorrent.com/license/.
#
# Software distributed under the License is distributed on an AS IS basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.  See the License
# for the specific language governing rights and limitations under the
# License.

from defer import Deferred
from BitTorrent.bencode import bencode, bdecode
import socket
from BitTorrent.RawServer import RawServer
from BitTorrent.platform import bttime
import time

import sys
from traceback import print_exc

import khash as hash
from cache import Cache
from KRateLimiter import KRateLimiter

from const import *

# commands
TID = 't'
REQ = 'q'
RSP = 'r'
TYP = 'y'
ARG = 'a'
ERR = 'e'

class KRPCFailSilently(Exception):
    pass

class KRPCProtocolError(Exception):
    pass

class KRPCServerError(Exception):
    pass

class KRPCSelfNodeError(Exception):
    pass

class hostbroker(object):       
    def __init__(self, server, addr, transport, call_later, max_ul_rate):
        self.server = server
        self.addr = addr
        self.transport = KRateLimiter(transport, max_ul_rate, call_later)
        self.call_later = call_later
        self.connections = Cache(touch_on_access=True)
        self.expire_connections(loop=True)
        
    def expire_connections(self, loop=False):
        self.connections.expire(bttime() - KRPC_CONNECTION_CACHE_TIME)
        if loop:
            self.call_later(self.expire_connections, KRPC_CONNECTION_CACHE_TIME, (True,))

    def data_came_in(self, addr, datagram):
        #if addr != self.addr:
        c = self.connectionForAddr(addr)
        c.datagramReceived(datagram, addr)

    def connection_lost(self, socket):
        ## this is like, bad
        print ">>> connection lost!", socket

    def connectionForAddr(self, addr):
        if addr == self.addr:
            raise KRPCSelfNodeError()
        if not self.connections.has_key(addr):
            conn = KRPC(addr, self.server, self.transport, self.call_later)
            self.connections[addr] = conn
        else:
            conn = self.connections[addr]
        return conn


## connection
class KRPC:
    noisy = 0
    def __init__(self, addr, server, transport, call_later):
        self.call_later = call_later
        self.transport = transport
        self.factory = server
        self.addr = addr
        self.tids = {}
        self.mtid = 0
        self.pinging = False
        
    def sendErr(self, addr, tid, msg):
        ## send error
        out = bencode({TID:tid, TYP:ERR, ERR :msg})
        olen = len(out)
        self.transport.sendto(out, 0, addr)
        return olen                 

    def datagramReceived(self, str, addr):
        # bdecode
        try:
            msg = bdecode(str)
        except Exception, e:
            if self.noisy:
                print "response decode error: " + `e`, `str`
        else:
            #if self.noisy:
            #    print msg
            # look at msg type
            if msg[TYP]  == REQ:
                ilen = len(str)
                # if request
                #	tell factory to handle
                f = getattr(self.factory ,"krpc_" + msg[REQ], None)
                msg[ARG]['_krpc_sender'] =  self.addr
                if f and callable(f):
                    try:
                        ret = apply(f, (), msg[ARG])
                    except KRPCFailSilently:
                        pass
                    except KRPCServerError, e:
                        olen = self.sendErr(addr, msg[TID], "Server Error: %s" % e.args[0])
                    except KRPCProtocolError, e:
                        olen = self.sendErr(addr, msg[TID], "Protocol Error: %s" % e.args[0])                        
                    except Exception, e:
                        print_exc(20)
                        olen = self.sendErr(addr, msg[TID], "Server Error")
                    else:
                        if ret:
                            #	make response
                            out = bencode({TID : msg[TID], TYP : RSP, RSP : ret})
                        else:
                            out = bencode({TID : msg[TID], TYP : RSP, RSP : {}})
                        #	send response
                        olen = len(out)
                        self.transport.sendto(out, 0, addr)

                else:
                    if self.noisy:
                        print "don't know about method %s" % msg[REQ]
                    # unknown method
                    out = bencode({TID:msg[TID], TYP:ERR, ERR : KRPC_ERROR_METHOD_UNKNOWN})
                    olen = len(out)
                    self.transport.sendto(out, 0, addr)
                if self.noisy:
                    print "%s %s >>> %s - %s %s %s" % (time.asctime(), addr, self.factory.node.port, 
                                                    ilen, msg[REQ], olen)
            elif msg[TYP] == RSP:
                # if response
                # 	lookup tid
                if self.tids.has_key(msg[TID]):
                    df = self.tids[msg[TID]]
                    # 	callback
                    del(self.tids[msg[TID]])
                    df.callback({'rsp' : msg[RSP], '_krpc_sender': addr})
                else:
                    # no tid, this transaction timed out already...
                    pass
                
            elif msg[TYP] == ERR:
                # if error
                # 	lookup tid
                if self.tids.has_key(msg[TID]):
                    df = self.tids[msg[TID]]
                    # 	callback
                    df.errback(msg[ERR])
                    del(self.tids[msg[TID]])
                else:
                    # day late and dollar short
                    pass
            else:
                print "unknown message type " + `msg`
                # unknown message type
                df = self.tids[msg[TID]]
                # 	callback
                df.errback(KRPC_ERROR_RECEIVED_UNKNOWN)
                del(self.tids[msg[TID]])
                
    def sendRequest(self, method, args):
        # make message
        # send it
        msg = {TID : chr(self.mtid), TYP : REQ,  REQ : method, ARG : args}
        self.mtid = (self.mtid + 1) % 256
        s = bencode(msg)
        d = Deferred()
        self.tids[msg[TID]] = d
        def timeOut(tids = self.tids, id = msg[TID]):
            if tids.has_key(id):
                df = tids[id]
                del(tids[id])
                df.errback(KRPC_ERROR_TIMEOUT)
        self.call_later(timeOut, KRPC_TIMEOUT)
        self.call_later(self._send, 0, (s, d))
        return d
 
    def _send(self, s, d):
        try:
            self.transport.sendto(s, 0, self.addr)
        except socket.error:
            d.errback(KRPC_SOCKET_ERROR)
            
