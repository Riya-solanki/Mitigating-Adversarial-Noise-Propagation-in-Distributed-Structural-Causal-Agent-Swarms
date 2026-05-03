"""
Tests for the Raft Consensus Module.
Run: pytest test_raft.py -v
"""

import sys
import os
import time
import pytest
import threading

sys.path.insert(0, os.path.abspath('src'))
from consensus.raft_server import RaftNode, LogEntry, FOLLOWER, CANDIDATE, LEADER


class TestRaftInitialisation:
    """Test RaftNode initialisation."""

    def test_initial_state(self):
        node = RaftNode("node1", {"node2": "localhost:50052"})
        assert node.state == FOLLOWER
        assert node.current_term == 0
        assert node.voted_for is None
        assert node.log == []
        assert node.commit_index == 0
        assert node.last_applied == 0

    def test_node_id(self):
        node = RaftNode("my_node", {})
        assert node.node_id == "my_node"

    def test_peers_stored(self):
        peers = {"n2": "host2:50051", "n3": "host3:50051"}
        node = RaftNode("n1", peers)
        assert node.peers == peers


class TestLogEntry:
    """Test LogEntry serialisation."""

    def test_to_proto_and_back(self):
        entry = LogEntry(
            term=2,
            index=5,
            command_type="APPLY_ACTION",
            command_data={"action_vector": [1, 0, 0, 0]},
            node_id="node1",
        )
        proto = entry.to_proto()
        restored = LogEntry.from_proto(proto)

        assert restored.term == 2
        assert restored.index == 5
        assert restored.command_type == "APPLY_ACTION"
        assert restored.command_data == {"action_vector": [1, 0, 0, 0]}
        assert restored.node_id == "node1"

    def test_repr(self):
        entry = LogEntry(term=1, index=1, command_type="SET", command_data={})
        assert "term=1" in repr(entry)
        assert "SET" in repr(entry)


class TestRaftElection:
    """Test Raft leader election with in-process nodes."""

    def test_single_node_becomes_leader(self):
        """A single node (no peers) should become leader after election timeout."""
        committed = []
        node = RaftNode("solo", {}, on_commit=lambda e: committed.append(e))

        # Manually trigger election (no peers → wins with self-vote only)
        # With 0 peers, majority = 1, and the node votes for itself
        node.current_term = 0
        node.state = CANDIDATE
        node.current_term += 1
        node.voted_for = node.node_id
        # With no peers, votes_received = 1, majority = 1 → becomes leader
        node._become_leader()

        assert node.state == LEADER
        assert node.leader_id == "solo"
        assert node.current_term == 1
        node.stop()

    def test_step_down_on_higher_term(self):
        """Node should step down when it sees a higher term."""
        node = RaftNode("n1", {})
        node.state = LEADER
        node.current_term = 3

        node._step_down(5)

        assert node.state == FOLLOWER
        assert node.current_term == 5
        assert node.voted_for is None
        node.stop()


class TestRaftProposal:
    """Test proposing log entries."""

    def test_propose_entry_as_leader(self):
        node = RaftNode("leader", {})
        node.state = LEADER
        node.current_term = 1

        success, msg = node.propose_entry("APPLY_ACTION", {"key": "value"})
        assert success is True
        assert len(node.log) == 1
        assert node.log[0].term == 1
        assert node.log[0].command_type == "APPLY_ACTION"
        node.stop()

    def test_propose_entry_as_follower_fails(self):
        node = RaftNode("follower", {})
        node.state = FOLLOWER

        success, msg = node.propose_entry("APPLY_ACTION", {"key": "value"})
        assert success is False
        assert "Not leader" in msg
        node.stop()

    def test_multiple_proposals(self):
        node = RaftNode("leader", {})
        node.state = LEADER
        node.current_term = 1

        node.propose_entry("CMD1", {"a": 1})
        node.propose_entry("CMD2", {"b": 2})
        node.propose_entry("CMD3", {"c": 3})

        assert len(node.log) == 3
        assert node.log[0].index == 1
        assert node.log[1].index == 2
        assert node.log[2].index == 3
        node.stop()


class TestRaftVoteHandler:
    """Test the RequestVote RPC handler."""

    def test_grant_vote_for_higher_term(self):
        import communication_pb2

        node = RaftNode("n1", {})
        node.current_term = 1

        request = communication_pb2.VoteRequest(
            term=2,
            candidate_id="n2",
            last_log_index=0,
            last_log_term=0,
        )
        response = node.handle_request_vote(request)
        assert response.vote_granted is True
        assert node.voted_for == "n2"
        assert node.current_term == 2
        node.stop()

    def test_reject_vote_for_lower_term(self):
        import communication_pb2

        node = RaftNode("n1", {})
        node.current_term = 5

        request = communication_pb2.VoteRequest(
            term=3,
            candidate_id="n2",
            last_log_index=0,
            last_log_term=0,
        )
        response = node.handle_request_vote(request)
        assert response.vote_granted is False
        node.stop()

    def test_reject_vote_already_voted(self):
        import communication_pb2

        node = RaftNode("n1", {})
        node.current_term = 2
        node.voted_for = "n3"

        request = communication_pb2.VoteRequest(
            term=2,
            candidate_id="n2",
            last_log_index=0,
            last_log_term=0,
        )
        response = node.handle_request_vote(request)
        assert response.vote_granted is False
        node.stop()


class TestRaftAppendEntriesHandler:
    """Test the AppendEntries RPC handler."""

    def test_heartbeat_resets_timer(self):
        import communication_pb2

        node = RaftNode("n1", {})
        node.current_term = 1
        node.running = True

        request = communication_pb2.AppendEntriesRequest(
            term=1,
            leader_id="n2",
            prev_log_index=0,
            prev_log_term=0,
            entries=[],
            leader_commit=0,
        )
        response = node.handle_append_entries(request)
        assert response.success is True
        assert node.leader_id == "n2"
        assert node.state == FOLLOWER
        node.stop()

    def test_reject_stale_leader(self):
        import communication_pb2

        node = RaftNode("n1", {})
        node.current_term = 5

        request = communication_pb2.AppendEntriesRequest(
            term=3,
            leader_id="old_leader",
            prev_log_index=0,
            prev_log_term=0,
            entries=[],
            leader_commit=0,
        )
        response = node.handle_append_entries(request)
        assert response.success is False
        assert response.term == 5
        node.stop()

    def test_append_new_entry(self):
        import communication_pb2

        node = RaftNode("n1", {})
        node.current_term = 1
        node.running = True

        entry = LogEntry(term=1, index=1, command_type="SET", command_data={"a": 1}, node_id="leader")

        request = communication_pb2.AppendEntriesRequest(
            term=1,
            leader_id="leader",
            prev_log_index=0,
            prev_log_term=0,
            entries=[entry.to_proto()],
            leader_commit=0,
        )
        response = node.handle_append_entries(request)
        assert response.success is True
        assert len(node.log) == 1
        assert node.log[0].command_type == "SET"
        node.stop()


class TestRaftStatus:
    """Test the status introspection."""

    def test_get_status(self):
        node = RaftNode("n1", {"n2": "host:port"})
        status = node.get_status()
        assert status['node_id'] == "n1"
        assert status['state'] == FOLLOWER
        assert status['term'] == 0
        assert status['log_length'] == 0
        node.stop()

    def test_is_leader(self):
        node = RaftNode("n1", {})
        assert node.is_leader() is False
        node.state = LEADER
        assert node.is_leader() is True
        node.stop()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
