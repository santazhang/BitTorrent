# The contents of this file are subject to the BitTorrent Open Source License
# Version 1.1 (the License).  You may not copy or use this file, in either
# source code or executable form, except in compliance with the License.  You
# may obtain a copy of the License at http://www.bittorrent.com/license/.
#
# Software distributed under the License is distributed on an AS IS basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied.  See the License
# for the specific language governing rights and limitations under the
# License.

# Written by Bram Cohen, Uoti Urpala

from __future__ import division

class TorrentStats(object):

    def __init__(self, choker, upfunc, downfunc, uptotal, downtotal,
                 remainingfunc, pcfunc, piece_states, finflag,
                 downloader, file_priorities, files, ever_got_incoming, rerequester):
        self.downloader = downloader
        self.file_priorities = file_priorities
        self.picker = downloader.picker
        self.storage = downloader.storage
        self.choker = choker
        self.upfunc = upfunc
        self.downfunc = downfunc
        self.uptotal = uptotal
        self.downtotal = downtotal
        self.remainingfunc = remainingfunc
        self.pcfunc = pcfunc
        self.piece_states = piece_states
        self.finflag = finflag
        self.files = files
        self.ever_got_incoming = ever_got_incoming
        self.rerequester = rerequester

    def collect_spew(self):
        l = [ ]
        for c in self.choker.connections:
            rec = {}
            rec['id'] = c.id
            rec["ip"] = c.ip
            rec["is_optimistic_unchoke"] = (c is self.choker.connections[0])
            if c.locally_initiated:
                if c._decrypt:
                    rec["initiation"] = "L+"
                else:
                    rec["initiation"] = "L"
            else:
                if c._decrypt:
                    rec["initiation"] = "R+"
                else:
                    rec["initiation"] = "R"
            u = c.upload
            rec["upload"] = (u.measure.get_total(), int(u.measure.get_rate()),
                             u.interested, u.choked)

            d = c.download
            rec["download"] = (d.measure.get_total(),int(d.measure.get_rate()),
                               d.interested, d.choked, d.is_snubbed())
            rec['completed'] = 1 - d.have.numfalse / len(d.have)
            rec['speed'] = d.connection.download.peermeasure.get_rate()
            l.append(rec)
        return l

    def get_swarm_speed(self):
        speeds = []
        for c in self.choker.connections:
            d = c.download
            speeds.append(d.connection.download.peermeasure.get_rate())
        speed = sum(speeds)
        return speed

    def get_statistics(self, spewflag=False, fileflag=False):
        status = {}

        numSeeds = 0
        numPeers = 0
        for d in self.downloader.downloads:
            numPeers += 1
            if d.have.numfalse == 0:
                numSeeds += 1
        status['numSeeds'] = numSeeds
        status['numPeers'] = numPeers

        if self.rerequester:
            status['trackerSeeds'] = self.rerequester.tracker_num_seeds
            status['trackerPeers'] = self.rerequester.tracker_num_peers
            if status['trackerSeeds'] is not None:
                if status['trackerPeers'] is not None:
                    status['trackerPeers'] += status['trackerSeeds']
                else:
                    status['trackerPeers'] = status['trackerSeeds']

            status['announceTime'] = self.rerequester.get_next_announce_time_est()
        else:
            status['trackerSeeds'] = None
            status['trackerPeers'] = None
            status['announceTime'] = None
        status['upRate'] = self.upfunc()
        status['upTotal'] = self.uptotal()
        status['ever_got_incoming'] = self.ever_got_incoming()

        status['distributed_copies'] = self.downloader.get_adjusted_distributed_copies()

        status['discarded'] = self.downloader.discarded_bytes

        status['swarm_speed'] = self.get_swarm_speed()

        status['pieceStates'] = self.piece_states()

        if spewflag:
            status['spew'] = self.collect_spew()
            status['bad_peers'] = self.downloader.bad_peers
        if fileflag:
            undl = self.storage.storage.undownloaded
            status['files_left'] = [undl[fname] for fname in self.files]
            status['file_priorities'] = dict(self.file_priorities())
        if self.finflag.isSet():
            status['downRate'] = 0
            status['downTotal'] = self.downtotal()
            status['fractionDone'] = 1
            return status
        timeEst = self.remainingfunc()
        status['timeEst'] = timeEst

        fractionDone = self.pcfunc()
        status.update({
            "fractionDone" : fractionDone,
            "downRate" : self.downfunc(),
            "downTotal" : self.downtotal()
            })
        return status