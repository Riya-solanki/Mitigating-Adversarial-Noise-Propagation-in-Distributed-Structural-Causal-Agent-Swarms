"""
Raft Consensus Implementation for Multi-Agent Causal Dependency Maintenance.

Implements the core Raft protocol:
  - Leader Election (RequestVote RPC)
  - Log Replication (AppendEntries RPC)
  - Heartbeats (empty AppendEntries)
  - Commit tracking and state-machine application

Reference: Ongaro & Ousterhout, "In Search of an Understandable Consensus Algorithm" (2014)
"""

import threading
import time
import random
import logging
import json
import grpc
import sys
import os

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'proto'))
import communication_pb2
import communication_pb2_grpc

logger = logging.getLogger(__name__)


# ─── Raft States ───
FOLLOWER = 'FOLLOWER'
CANDIDATE = 'CANDIDATE'
LEADER = 'LEADER'

# ─── Timing (seconds) ───
ELECTION_TIMEOUT_MIN = 1.5
ELECTION_TIMEOUT_MAX = 3.0
HEARTBEAT_INTERVAL = 0.5
RPC_TIMEOUT = 1.0


class LogEntry:
    """A single entry in the Raft replicated log."""

    def __init__(self, term, index, command_type, command_data, node_id=""):
        self.term = term
        self.index = index
        self.command_type = command_type      # e.g. "APPLY_ACTION"
        self.command_data = command_data      # dict with action details
        self.node_id = node_id               # originating node

    def to_proto(self):
        """Convert to protobuf LogEntry message."""
        return communication_pb2.LogEntry(
            term=self.term,
            index=self.index,
            command_type=self.command_type,
            command_data=json.dumps(self.command_data).encode('utf-8'),
            node_id=self.node_id,
        )

    @classmethod
    def from_proto(cls, proto_entry):
        """Create LogEntry from a protobuf LogEntry message."""
        return cls(
            term=proto_entry.term,
            index=proto_entry.index,
            command_type=proto_entry.command_type,
            command_data=json.loads(proto_entry.command_data.decode('utf-8')),
            node_id=proto_entry.node_id,
        )

    def __repr__(self):
        return f"LogEntry(term={self.term}, idx={self.index}, cmd={self.command_type})"


class RaftNode:
    """
    A Raft consensus node that participates in leader election and log replication.

    This node communicates with peers via gRPC. It can be used standalone or
    embedded inside an SCA node for distributed causal-dependency maintenance.
    """

    def __init__(self, node_id, peers, on_commit=None):
        """
        Args:
            node_id:   Unique identifier for this node (e.g. "node1").
            peers:     Dict mapping peer_id -> "host:port" address strings.
            on_commit: Callback(log_entry) invoked when a log entry is committed.
        """
        self.node_id = node_id
        self.peers = peers                       # {peer_id: "host:port"}
        self.on_commit = on_commit               # callback for committed entries

        # ── Persistent State (on all servers) ──
        self.current_term = 0
        self.voted_for = None
        self.log = []                            # List[LogEntry]

        # ── Volatile State (on all servers) ──
        self.state = FOLLOWER
        self.commit_index = 0                    # highest log entry known to be committed
        self.last_applied = 0                    # highest log entry applied to state machine

        # ── Volatile State (on leaders, reinitialised after election) ──
        self.next_index = {}                     # {peer_id: int}
        self.match_index = {}                    # {peer_id: int}

        # ── Internal ──
        self.lock = threading.RLock()
        self.running = False
        self.election_timer = None
        self.heartbeat_timer = None
        self.leader_id = None
        self._reset_election_timeout()

    # ═══════════════════════════════════════════
    #  Lifecycle
    # ═══════════════════════════════════════════

    def start(self):
        """Start the Raft node (begins election timer)."""
        self.running = True
        logger.info(f"[Raft {self.node_id}] Starting as {self.state} | peers={list(self.peers.keys())}")
        self._start_election_timer()

    def stop(self):
        """Stop the Raft node gracefully."""
        self.running = False
        self._cancel_election_timer()
        self._cancel_heartbeat_timer()
        logger.info(f"[Raft {self.node_id}] Stopped.")

    # ═══════════════════════════════════════════
    #  Election Timer
    # ═══════════════════════════════════════════

    def _reset_election_timeout(self):
        self.election_timeout = random.uniform(ELECTION_TIMEOUT_MIN, ELECTION_TIMEOUT_MAX)

    def _start_election_timer(self):
        """Start (or restart) the election timeout timer."""
        self._cancel_election_timer()
        if not self.running:
            return
        self._reset_election_timeout()
        self.election_timer = threading.Timer(self.election_timeout, self._election_timeout_fired)
        self.election_timer.daemon = True
        self.election_timer.start()

    def _cancel_election_timer(self):
        if self.election_timer:
            self.election_timer.cancel()
            self.election_timer = None

    def _election_timeout_fired(self):
        """Called when the election timer expires — start an election."""
        if not self.running:
            return
        with self.lock:
            if self.state == LEADER:
                return
            logger.info(f"[Raft {self.node_id}] Election timeout! Starting election for term {self.current_term + 1}")
            self._start_election()

    # ═══════════════════════════════════════════
    #  Heartbeat Timer (Leader only)
    # ═══════════════════════════════════════════

    def _start_heartbeat_timer(self):
        self._cancel_heartbeat_timer()
        if not self.running or self.state != LEADER:
            return
        self.heartbeat_timer = threading.Timer(HEARTBEAT_INTERVAL, self._send_heartbeats)
        self.heartbeat_timer.daemon = True
        self.heartbeat_timer.start()

    def _cancel_heartbeat_timer(self):
        if self.heartbeat_timer:
            self.heartbeat_timer.cancel()
            self.heartbeat_timer = None

    # ═══════════════════════════════════════════
    #  Leader Election
    # ═══════════════════════════════════════════

    def _start_election(self):
        """Transition to CANDIDATE, increment term, vote for self, request votes."""
        self.state = CANDIDATE
        self.current_term += 1
        self.voted_for = self.node_id
        self.leader_id = None
        votes_received = 1  # vote for self

        last_log_index = len(self.log)
        last_log_term = self.log[-1].term if self.log else 0

        logger.info(f"[Raft {self.node_id}] CANDIDATE for term {self.current_term}")

        # Request votes from all peers (in parallel)
        vote_threads = []
        vote_results = []

        def request_vote_from_peer(peer_id, address):
            try:
                channel = grpc.insecure_channel(address)
                stub = communication_pb2_grpc.NodeCommunicationStub(channel)
                request = communication_pb2.VoteRequest(
                    term=self.current_term,
                    candidate_id=self.node_id,
                    last_log_index=last_log_index,
                    last_log_term=last_log_term,
                )
                response = stub.RequestVote(request, timeout=RPC_TIMEOUT)
                vote_results.append((peer_id, response))
                channel.close()
            except Exception as e:
                logger.debug(f"[Raft {self.node_id}] Vote request to {peer_id} failed: {e}")

        for peer_id, address in self.peers.items():
            t = threading.Thread(target=request_vote_from_peer, args=(peer_id, address))
            t.daemon = True
            t.start()
            vote_threads.append(t)

        # Wait for responses (with timeout)
        for t in vote_threads:
            t.join(timeout=RPC_TIMEOUT + 0.5)

        # Count votes
        with self.lock:
            if self.state != CANDIDATE or not self.running:
                return  # state changed while waiting

            for peer_id, response in vote_results:
                if response.term > self.current_term:
                    # Discovered higher term — step down
                    self._step_down(response.term)
                    return
                if response.vote_granted:
                    votes_received += 1

            majority = (len(self.peers) + 1) // 2 + 1
            if votes_received >= majority:
                self._become_leader()
            else:
                logger.info(f"[Raft {self.node_id}] Election failed ({votes_received}/{majority} votes). Restarting timer.")
                self.state = FOLLOWER
                self._start_election_timer()

    def _become_leader(self):
        """Transition to LEADER state."""
        self.state = LEADER
        self.leader_id = self.node_id

        # Initialise leader-specific volatile state
        last_log_index = len(self.log)
        for peer_id in self.peers:
            self.next_index[peer_id] = last_log_index + 1
            self.match_index[peer_id] = 0

        logger.info(f"[Raft {self.node_id}] ** Became LEADER for term {self.current_term}")

        # Cancel election timer, start heartbeats
        self._cancel_election_timer()
        self._send_heartbeats()

    def _step_down(self, new_term):
        """Step down to FOLLOWER when a higher term is discovered."""
        logger.info(f"[Raft {self.node_id}] Stepping down. term {self.current_term} -> {new_term}")
        self.state = FOLLOWER
        self.current_term = new_term
        self.voted_for = None
        self._cancel_heartbeat_timer()
        self._start_election_timer()

    # ═══════════════════════════════════════════
    #  AppendEntries / Heartbeats (Leader -> Followers)
    # ═══════════════════════════════════════════

    def _send_heartbeats(self):
        """Leader sends AppendEntries (heartbeat/replication) to all peers."""
        if not self.running or self.state != LEADER:
            return

        with self.lock:
            for peer_id, address in self.peers.items():
                t = threading.Thread(
                    target=self._send_append_entries_to_peer,
                    args=(peer_id, address),
                )
                t.daemon = True
                t.start()

        # Schedule next heartbeat
        self._start_heartbeat_timer()

    def _send_append_entries_to_peer(self, peer_id, address):
        """Send AppendEntries RPC to a single peer."""
        with self.lock:
            if self.state != LEADER:
                return

            next_idx = self.next_index.get(peer_id, 1)
            prev_log_index = next_idx - 1
            prev_log_term = 0
            if prev_log_index > 0 and prev_log_index <= len(self.log):
                prev_log_term = self.log[prev_log_index - 1].term

            # Entries to replicate
            entries_to_send = self.log[next_idx - 1:]
            proto_entries = [e.to_proto() for e in entries_to_send]

            request = communication_pb2.AppendEntriesRequest(
                term=self.current_term,
                leader_id=self.node_id,
                prev_log_index=prev_log_index,
                prev_log_term=prev_log_term,
                entries=proto_entries,
                leader_commit=self.commit_index,
            )

        try:
            channel = grpc.insecure_channel(address)
            stub = communication_pb2_grpc.NodeCommunicationStub(channel)
            response = stub.AppendEntries(request, timeout=RPC_TIMEOUT)
            channel.close()

            with self.lock:
                if response.term > self.current_term:
                    self._step_down(response.term)
                    return

                if response.success:
                    # Update next_index and match_index for this peer
                    if entries_to_send:
                        self.next_index[peer_id] = entries_to_send[-1].index + 1
                        self.match_index[peer_id] = entries_to_send[-1].index
                        self._try_advance_commit_index()
                else:
                    # Decrement next_index and retry (log inconsistency)
                    self.next_index[peer_id] = max(1, next_idx - 1)

        except Exception as e:
            logger.debug(f"[Raft {self.node_id}] AppendEntries to {peer_id} failed: {e}")

    def _try_advance_commit_index(self):
        """
        Leader checks if there exists an N > commit_index such that
        a majority of match_index[i] >= N and log[N].term == current_term.
        """
        for n in range(len(self.log), self.commit_index, -1):
            if self.log[n - 1].term != self.current_term:
                continue
            # Count replicas (self + peers with match_index >= n)
            count = 1  # self
            for peer_id in self.peers:
                if self.match_index.get(peer_id, 0) >= n:
                    count += 1
            majority = (len(self.peers) + 1) // 2 + 1
            if count >= majority:
                old_commit = self.commit_index
                self.commit_index = n
                logger.info(f"[Raft {self.node_id}] Commit index advanced: {old_commit} -> {n}")
                self._apply_committed_entries()
                break

    # ═══════════════════════════════════════════
    #  State Machine Application
    # ═══════════════════════════════════════════

    def _apply_committed_entries(self):
        """Apply all committed but not yet applied log entries."""
        while self.last_applied < self.commit_index:
            self.last_applied += 1
            entry = self.log[self.last_applied - 1]
            logger.info(f"[Raft {self.node_id}] Applying {entry}")
            if self.on_commit:
                try:
                    self.on_commit(entry)
                except Exception as e:
                    logger.error(f"[Raft {self.node_id}] Error applying entry: {e}")

    # ═══════════════════════════════════════════
    #  RPC Handlers (called by gRPC servicer)
    # ═══════════════════════════════════════════

    def handle_request_vote(self, request):
        """
        Handle incoming RequestVote RPC.
        Returns VoteResponse.
        """
        with self.lock:
            vote_granted = False

            # If the candidate's term is higher, step down
            if request.term > self.current_term:
                self._step_down(request.term)

            # Grant vote if:
            #   1. Candidate's term >= our term
            #   2. We haven't voted for anyone else this term
            #   3. Candidate's log is at least as up-to-date as ours
            if request.term >= self.current_term:
                if self.voted_for is None or self.voted_for == request.candidate_id:
                    # Log completeness check
                    last_log_index = len(self.log)
                    last_log_term = self.log[-1].term if self.log else 0

                    if (request.last_log_term > last_log_term or
                            (request.last_log_term == last_log_term and
                             request.last_log_index >= last_log_index)):
                        vote_granted = True
                        self.voted_for = request.candidate_id
                        self._start_election_timer()  # Reset election timer on vote grant
                        logger.info(f"[Raft {self.node_id}] Voted for {request.candidate_id} in term {request.term}")

            return communication_pb2.VoteResponse(
                term=self.current_term,
                vote_granted=vote_granted,
            )

    def handle_append_entries(self, request):
        """
        Handle incoming AppendEntries RPC (heartbeat or log replication).
        Returns AppendEntriesResponse.
        """
        with self.lock:
            success = False

            # If leader's term is higher, step down
            if request.term > self.current_term:
                self._step_down(request.term)

            if request.term < self.current_term:
                # Reject — stale leader
                return communication_pb2.AppendEntriesResponse(
                    term=self.current_term,
                    success=False,
                )

            # Valid AppendEntries from current leader
            self.state = FOLLOWER
            self.leader_id = request.leader_id
            self._start_election_timer()  # Reset election timer

            # Log consistency check
            if request.prev_log_index > 0:
                if request.prev_log_index > len(self.log):
                    # Missing entries
                    return communication_pb2.AppendEntriesResponse(
                        term=self.current_term,
                        success=False,
                    )
                if self.log[request.prev_log_index - 1].term != request.prev_log_term:
                    # Conflicting entry — delete it and all following
                    self.log = self.log[:request.prev_log_index - 1]
                    return communication_pb2.AppendEntriesResponse(
                        term=self.current_term,
                        success=False,
                    )

            # Append new entries (if any)
            for proto_entry in request.entries:
                entry = LogEntry.from_proto(proto_entry)
                if entry.index <= len(self.log):
                    # Already have this entry — check for conflict
                    if self.log[entry.index - 1].term != entry.term:
                        self.log = self.log[:entry.index - 1]
                        self.log.append(entry)
                else:
                    self.log.append(entry)

            # Update commit index
            if request.leader_commit > self.commit_index:
                self.commit_index = min(request.leader_commit, len(self.log))
                self._apply_committed_entries()

            success = True
            return communication_pb2.AppendEntriesResponse(
                term=self.current_term,
                success=success,
            )

    # ═══════════════════════════════════════════
    #  Client API (for SCA Node to propose entries)
    # ═══════════════════════════════════════════

    def propose_entry(self, command_type, command_data):
        """
        Propose a new log entry (called by the SCA node).
        Only succeeds if this node is the leader.

        Returns:
            (success: bool, message: str)
        """
        with self.lock:
            if self.state != LEADER:
                return False, f"Not leader. Current leader: {self.leader_id}"

            new_index = len(self.log) + 1
            entry = LogEntry(
                term=self.current_term,
                index=new_index,
                command_type=command_type,
                command_data=command_data,
                node_id=self.node_id,
            )
            self.log.append(entry)
            # Update own match_index
            self.match_index[self.node_id] = new_index
            logger.info(f"[Raft {self.node_id}] Proposed entry: {entry}")
            return True, f"Entry proposed at index {new_index}"

    def is_leader(self):
        """Check if this node is the current leader."""
        return self.state == LEADER

    def get_leader_id(self):
        """Get the ID of the current leader (may be None)."""
        return self.leader_id

    def get_status(self):
        """Return a dict summarising the node's Raft state."""
        with self.lock:
            return {
                'node_id': self.node_id,
                'state': self.state,
                'term': self.current_term,
                'leader': self.leader_id,
                'log_length': len(self.log),
                'commit_index': self.commit_index,
                'last_applied': self.last_applied,
            }
