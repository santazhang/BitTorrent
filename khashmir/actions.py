# The contents of this file are subject to the BitTorrent Open Source License
# Version 1.0 (the License).  You may not copy or use this file, in either
# source code or executable form, except in compliance with the License.  You
# may obtain a copy of the License at http://www.bittorrent.com/license/.
#
# Software distributed under the License is distributed on an AS IS basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.  See the License
# for the specific language governing rights and limitations under the
# License.

from BitTorrent.platform import bttime as time

import const

from khash import intify
from ktable import KTable, K
from util import unpackNodes
from krpc import KRPCProtocolError, KRPCSelfNodeError

class ActionBase:
    """ base class for some long running asynchronous proccesses like finding nodes or values """
    def __init__(self, table, target, callback, callLater):
        self.table = table
        self.target = target
        self.callLater = callLater
        self.num = intify(target)
        self.found = {}
        self.queried = {}
        self.queriedip = {}
        self.answered = {}
        self.callback = callback
        self.outstanding = 0
        self.finished = 0
    
        def sort(a, b, num=self.num):
            """ this function is for sorting nodes relative to the ID we are looking for """
            if (not a.invalid) and b.invalid:
                return 1
            elif a.invalid and not b.invalid:
                return -1
            x, y = num ^ a.num, num ^ b.num
            if x > y:
                return 1
            elif x < y:
                return -1
            return 0
        self.sort = sort

    def shouldQuery(self, node):
        if node.id == self.table.node.id:
            return False
        elif (node.host, node.port) not in self.queriedip and node.id not in self.queried:
            self.queriedip[(node.host, node.port)] = 1
            self.queried[node.id] = 1
            return True
        return False
    
    def goWithNodes(self, t):
        pass
    
    

FIND_NODE_TIMEOUT = 15

class FindNode(ActionBase):
    """ find node action merits it's own class as it is a long running stateful process """
    def handleGotNodes(self, dict):
        _krpc_sender = dict['_krpc_sender']
        dict = dict['rsp']
        l = unpackNodes(dict["nodes"])
        sender = {'id' : dict["id"]}
        sender['port'] = _krpc_sender[1]        
        sender['host'] = _krpc_sender[0]        
        sender = self.table.Node().initWithDict(sender)
        
        if self.finished or self.answered.has_key(sender.id):
            # a day late and a dollar short
            return
        self.outstanding = self.outstanding - 1
        self.answered[sender.id] = 1
        for node in l:
            n = self.table.Node().initWithDict(node)
            if not self.found.has_key(n.id):
                self.found[n.id] = n
                self.table.insertNode(n, contacted=0)
        self.schedule()
        
    def schedule(self):
        """
            send messages to new peers, if necessary
        """
        if self.finished:
            return
        l = self.found.values()
        l.sort(self.sort)
        for node in l[:K]:
            if node.id == self.target:
                self.finished=1
                return self.callback([node])
            if self.shouldQuery(node):
                #xxxx t.timeout = time.time() + FIND_NODE_TIMEOUT
                try:
                    df = node.findNode(self.target, self.table.node.id)
                except KRPCSelfNodeError:
                    pass
                else:
                    df.addCallbacks(self.handleGotNodes, self.makeMsgFailed(node))
                    self.outstanding = self.outstanding + 1
            if self.outstanding >= const.CONCURRENT_REQS:
                break
        assert(self.outstanding) >=0
        if self.outstanding == 0:
            ## all done!!
            self.finished=1
            self.callLater(self.callback, 0, (l[:K],))
    
    def makeMsgFailed(self, node):
        def defaultGotNodes(err, self=self, node=node):
            self.outstanding = self.outstanding - 1
            self.schedule()
        return defaultGotNodes
    
    def goWithNodes(self, nodes):
        """
            this starts the process, our argument is a transaction with t.extras being our list of nodes
            it's a transaction since we got called from the dispatcher
        """
        for node in nodes:
            if node.id == self.table.node.id:
                continue
            else:
                self.found[node.id] = node
        
        self.schedule()
    

get_value_timeout = 15
class GetValue(FindNode):
    def __init__(self, table, target, callback, callLater, find="findValue"):
        FindNode.__init__(self, table, target, callback, callLater)
        self.findValue = find
            
    """ get value task """
    def handleGotNodes(self, dict):
        _krpc_sender = dict['_krpc_sender']
        dict = dict['rsp']
        sender = {'id' : dict["id"]}
        sender['port'] = _krpc_sender[1]
        sender['host'] = _krpc_sender[0]                
        sender = self.table.Node().initWithDict(sender)
        
        if self.finished or self.answered.has_key(sender.id):
            # a day late and a dollar short
            return
        self.outstanding = self.outstanding - 1
        self.answered[sender.id] = 1
        # go through nodes
        # if we have any closer than what we already got, query them
        if dict.has_key('nodes'):
            for node in unpackNodes(dict['nodes']):
                n = self.table.Node().initWithDict(node)
                if not self.found.has_key(n.id):
                    self.table.insertNode(n)
                    self.found[n.id] = n
        elif dict.has_key('values'):
            def x(y, z=self.results):
                if not z.has_key(y):
                    z[y] = 1
                    return y
                else:
                    return None
            z = len(dict['values'])
            v = filter(None, map(x, dict['values']))
            if(len(v)):
                self.callLater(self.callback, 0, (v,))
        self.schedule()
        
    ## get value
    def schedule(self):
        if self.finished:
            return
        l = self.found.values()
        l.sort(self.sort)
        for node in l[:K]:
            if self.shouldQuery(node):
                #xxx t.timeout = time.time() + GET_VALUE_TIMEOUT
                try:
                    f = getattr(node, self.findValue)
                except AttributeError:
                    print ">>> findValue %s doesn't have a %s method!" % (node, self.findValue)
                else:
                    try:
                        df = f(self.target, self.table.node.id)
                        df.addCallback(self.handleGotNodes)
                        df.addErrback(self.makeMsgFailed(node))
                        self.outstanding = self.outstanding + 1
                        self.queried[node.id] = 1
                    except KRPCSelfNodeError:
                        pass
            if self.outstanding >= const.CONCURRENT_REQS:
                break
        assert(self.outstanding) >=0
        if self.outstanding == 0:
            ## all done, didn't find it!!
            self.finished=1
            self.callLater(self.callback,0, ([],))

    ## get value
    def goWithNodes(self, nodes, found=None):
        self.results = {}
        if found:
            for n in found:
                self.results[n] = 1
        for node in nodes:
            if node.id == self.table.node.id:
                continue
            else:
                self.found[node.id] = node
            
        self.schedule()


class StoreValue(ActionBase):
    def __init__(self, table, target, value, callback, callLater, store="storeValue"):
        ActionBase.__init__(self, table, target, callback, callLater)
        self.value = value
        self.stored = []
        self.store = store
        
    def storedValue(self, t, node):
        self.outstanding -= 1
        if self.finished:
            return
        self.stored.append(t)
        if len(self.stored) >= const.STORE_REDUNDANCY:
            self.finished=1
            self.callback(self.stored)
        else:
            if not len(self.stored) + self.outstanding >= const.STORE_REDUNDANCY:
                self.schedule()
        return t
    
    def storeFailed(self, t, node):
        self.outstanding -= 1
        if self.finished:
            return t
        self.schedule()
        return t
    
    def schedule(self):
        if self.finished:
            return
        num = const.CONCURRENT_REQS - self.outstanding
        if num > const.STORE_REDUNDANCY - len(self.stored):
            num = const.STORE_REDUNDANCY - len(self.stored)
        if num == 0 and not self.finished:
            self.finished=1
            self.callback(self.stored)
        while num > 0:
            try:
                node = self.nodes.pop()
            except IndexError:
                if self.outstanding == 0:
                    self.finished = 1
                    self.callback(self.stored)
                return
            else:
                if not node.id == self.table.node.id:
                    try:
                        f = getattr(node, self.store)
                    except AttributeError:
                        print ">>> %s doesn't have a %s method!" % (node, self.store)
                    else:
                        try:
                            df = f(self.target, self.value, self.table.node.id)
                        except KRPCProtocolError:
                            self.table.table.invalidateNode(node)
                        except KRPCSelfNodeError:
                            pass
                        else:
                            df.addCallback(self.storedValue,(),{'node':node})
                            df.addErrback(self.storeFailed, (), {'node':node})
                            self.outstanding += 1
                            num -= 1
                        
    def goWithNodes(self, nodes):
        self.nodes = nodes
        self.nodes.sort(self.sort)
        self.schedule()


class KeyExpirer:
    def __init__(self, store, callLater):
        self.store = store
        self.callLater = callLater
        self.callLater(self.doExpire, const.KEINITIAL_DELAY)
    
    def doExpire(self):
        self.cut = time() - const.KE_AGE
        self.store.expire(self.cut)
        self.callLater(self.doExpire, const.KE_DELAY)
