"""
SCA (Smart Causal Agent) Node — the main distributed node process.

Wires together:
  - gRPC server (receives RPCs from peers)
  - gRPC client (sends RPCs to peers)
  - Raft consensus (leader election, log replication)
  - Causal Watchdog (action validation)
  - State Manager (state tracking)
  - MLP Generator (action generation — optional)

Each SCA node represents one room in the smart-home scenario.
"""

import grpc
import time
import os
import json
import logging
import sys
import threading
from concurrent import futures

# ── Path setup ──
_base = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _base)  # nodes directory (for state_manager)
sys.path.insert(0, os.path.join(_base, '..', 'proto'))
sys.path.insert(0, os.path.join(_base, '..', 'models'))
sys.path.insert(0, os.path.join(_base, '..', 'consensus'))
sys.path.insert(0, os.path.join(_base, '..', 'watchdog'))

import communication_pb2
import communication_pb2_grpc
from causal_gate import CausalWatchdog
from raft_server import RaftNode
from state_manager import NodeState, NodeAction

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s  %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# ── Smart-Home Constants ──
STATE_LABELS = ["Presence", "Lights", "AC", "Window"]
NUM_STATES = len(STATE_LABELS)

# Causal adjacency: A[i][j]=1 means state i is a causal parent of state j
# Presence -> Lights (must have presence to turn on lights)
# All others are root states (can be activated independently)
CAUSAL_MATRIX = [
    [1, 1, 0, 0],  # Presence causes itself, and Lights
    [0, 1, 0, 0],  # Lights (self)
    [0, 0, 1, 0],  # AC (self / root)
    [0, 0, 0, 1],  # Window (self / root)
]

# Conflicts: AC and Window are mutually exclusive
CONFLICT_PAIRS = [(2, 3)]  # AC <-> Window


class NodeCommunicationServicer(communication_pb2_grpc.NodeCommunicationServicer):
    """gRPC service handler — receives RPCs from peer nodes."""

    def __init__(self, sca_node):
        self.node = sca_node

    def ProposeAction(self, request, context):
        """Handle an incoming action proposal from another node."""
        logger.info(
            f"[gRPC] Received ProposeAction from {request.source_room}: "
            f"{request.action_type}"
        )

        action_vector = list(request.action_vector)
        current_state = self.node.state_manager.get_state()

        # Validate through Causal Watchdog
        is_valid, reasons = self.node.watchdog.validate_action_detailed(
            current_state, action_vector
        )

        if is_valid:
            # Propose to local Raft for consensus
            command_data = {
                'action_vector': action_vector,
                'source_room': request.source_room,
                'target_room': request.target_room,
                'description': request.action_type,
            }

            if self.node.raft.is_leader():
                success, msg = self.node.raft.propose_entry("APPLY_ACTION", command_data)
                if success:
                    # Apply immediately on leader (will be replicated via AppendEntries)
                    self.node.state_manager.apply_action(
                        action_vector,
                        f"Remote: {request.action_type} (from {request.source_room})",
                    )
                    resulting_state = self.node.state_manager.get_state()
                    return communication_pb2.ActionResponse(
                        success=True,
                        message=f"Action committed on {self.node.room_name}",
                        causally_valid=True,
                        resulting_state=resulting_state,
                    )
                else:
                    return communication_pb2.ActionResponse(
                        success=False,
                        message=msg,
                        causally_valid=True,
                    )
            else:
                # Not leader — apply locally anyway for the smart-home demo
                # (in production, you'd forward to the leader)
                self.node.state_manager.apply_action(
                    action_vector,
                    f"Remote: {request.action_type} (from {request.source_room})",
                )
                resulting_state = self.node.state_manager.get_state()
                return communication_pb2.ActionResponse(
                    success=True,
                    message=f"Action applied on {self.node.room_name} (non-leader)",
                    causally_valid=True,
                    resulting_state=resulting_state,
                )
        else:
            reason_text = "; ".join(reasons)
            logger.warning(f"[gRPC] Action BLOCKED on {self.node.room_name}: {reason_text}")
            return communication_pb2.ActionResponse(
                success=False,
                message=f"Causal Watchdog BLOCKED: {reason_text}",
                causally_valid=False,
            )

    def Heartbeat(self, request, context):
        """Handle heartbeat from a peer."""
        return communication_pb2.HeartbeatResponse(acknowledged=True)

    def RequestVote(self, request, context):
        """Handle Raft RequestVote RPC."""
        return self.node.raft.handle_request_vote(request)

    def AppendEntries(self, request, context):
        """Handle Raft AppendEntries RPC."""
        return self.node.raft.handle_append_entries(request)


class SCANode:
    """
    Smart Causal Agent Node — a distributed node for causal dependency maintenance.

    In the smart-home domain, each SCA node represents a room with 4 states:
    Presence, Lights, AC, Window.
    """

    def __init__(self, node_id=None, room_name=None, host='0.0.0.0', port=50051, peers=None):
        """
        Args:
            node_id:    Unique ID (defaults to NODE_ID env var or "node1").
            room_name:  Human-readable room name (defaults to ROOM_NAME env var).
            host:       gRPC server bind address.
            port:       gRPC server port.
            peers:      Dict of {peer_id: "host:port"} or None (reads PEERS env var).
        """
        self.node_id = node_id or os.getenv('NODE_ID', 'node1')
        self.room_name = room_name or os.getenv('ROOM_NAME', self.node_id)
        self.host = host
        self.port = int(os.getenv('PORT', port))
        self.server = None

        # Parse peers from environment or argument
        if peers is not None:
            self.peers = peers
        else:
            self.peers = self._parse_peers_env()

        # ── Core Components ──
        self.state_manager = NodeState(NUM_STATES, STATE_LABELS)
        self.watchdog = CausalWatchdog(
            CAUSAL_MATRIX,
            state_labels=STATE_LABELS,
            conflict_pairs=CONFLICT_PAIRS,
        )
        self.raft = RaftNode(
            node_id=self.node_id,
            peers=self.peers,
            on_commit=self._on_raft_commit,
        )

        logger.info(
            f"[SCA {self.room_name}] Initialised | id={self.node_id} | "
            f"port={self.port} | peers={list(self.peers.keys())}"
        )

    def _parse_peers_env(self):
        """
        Parse PEERS env var. Format: "peer_id1=host1:port1,peer_id2=host2:port2"
        """
        peers_str = os.getenv('PEERS', '')
        peers = {}
        if peers_str:
            for entry in peers_str.split(','):
                entry = entry.strip()
                if '=' in entry:
                    peer_id, address = entry.split('=', 1)
                    peers[peer_id.strip()] = address.strip()
        return peers

    def _on_raft_commit(self, log_entry):
        """Callback when Raft commits a log entry — apply to state machine."""
        if log_entry.command_type == "APPLY_ACTION":
            data = log_entry.command_data
            action_vector = data.get('action_vector', [])
            description = data.get('description', 'Raft commit')
            self.state_manager.apply_action(action_vector, description)
            logger.info(
                f"[SCA {self.room_name}] Raft COMMITTED: {description} -> "
                f"{self.state_manager}"
            )

    # ═══════════════════════════════════════════
    #  Server Lifecycle
    # ═══════════════════════════════════════════

    def start_server(self):
        """Start the gRPC server and Raft consensus."""
        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        communication_pb2_grpc.add_NodeCommunicationServicer_to_server(
            NodeCommunicationServicer(self), self.server
        )
        self.server.add_insecure_port(f'{self.host}:{self.port}')
        self.server.start()
        self.raft.start()
        logger.info(
            f"[SCA {self.room_name}] Server started on {self.host}:{self.port}"
        )

    def stop_server(self):
        """Stop the gRPC server and Raft consensus."""
        self.raft.stop()
        if self.server:
            self.server.stop(0)
        logger.info(f"[SCA {self.room_name}] Server stopped.")

    # ═══════════════════════════════════════════
    #  Local Actions (used by simulation scripts)
    # ═══════════════════════════════════════════

    def attempt_local_action(self, action_name, action_vector):
        """
        Attempt to apply an action locally on this node.
        Validates causally, then applies if valid.

        Returns:
            (success: bool, message: str)
        """
        current_state = self.state_manager.get_state()
        is_valid, reasons = self.watchdog.validate_action_detailed(
            current_state, action_vector
        )

        if not is_valid:
            reason_text = "; ".join(reasons)
            logger.warning(
                f"[SCA {self.room_name}] BLOCKED: {action_name} — {reason_text}"
            )
            return False, reason_text

        # Apply the action
        self.state_manager.apply_action(action_vector, action_name)
        logger.info(
            f"[SCA {self.room_name}] Applied: {action_name} -> {self.state_manager}"
        )

        # If we're the Raft leader, propose to log for replication
        if self.raft.is_leader():
            command_data = {
                'action_vector': action_vector,
                'source_room': self.room_name,
                'target_room': self.room_name,
                'description': action_name,
            }
            self.raft.propose_entry("APPLY_ACTION", command_data)

        return True, "Action applied"

    def send_action_to_peer(self, target_peer_id, action_name, action_vector):
        """
        Send an action proposal to a peer node via gRPC.

        Args:
            target_peer_id: The peer's node ID (must be in self.peers).
            action_name:    Human-readable description.
            action_vector:  The action vector (1=activate, -1=deactivate, 0=no change).

        Returns:
            (success: bool, message: str)
        """
        if target_peer_id not in self.peers:
            return False, f"Unknown peer: {target_peer_id}"

        address = self.peers[target_peer_id]
        logger.info(
            f"[SCA {self.room_name}] Sending action to {target_peer_id} at {address}: "
            f"{action_name}"
        )

        try:
            channel = grpc.insecure_channel(address)
            stub = communication_pb2_grpc.NodeCommunicationStub(channel)

            request = communication_pb2.ActionRequest(
                node_id=self.node_id,
                action_type=action_name,
                state_vector=self.state_manager.get_state(),
                action_vector=[float(v) for v in action_vector],
                source_room=self.room_name,
                target_room=target_peer_id,
                timestamp=int(time.time()),
            )

            response = stub.ProposeAction(request, timeout=5.0)
            channel.close()

            if response.success:
                logger.info(
                    f"[SCA {self.room_name}] Action accepted by {target_peer_id}: "
                    f"{response.message}"
                )
            else:
                logger.warning(
                    f"[SCA {self.room_name}] Action rejected by {target_peer_id}: "
                    f"{response.message}"
                )

            return response.success, response.message

        except Exception as e:
            logger.error(f"[SCA {self.room_name}] gRPC error to {target_peer_id}: {e}")
            return False, str(e)

    # ═══════════════════════════════════════════
    #  Status / Introspection
    # ═══════════════════════════════════════════

    def get_status(self):
        """Return a comprehensive status dict."""
        return {
            'node_id': self.node_id,
            'room_name': self.room_name,
            'state': self.state_manager._format_state(),
            'state_vector': self.state_manager.get_state(),
            'raft': self.raft.get_status(),
            'history_count': len(self.state_manager.history),
        }

    def print_state(self):
        """Print current state in a formatted way."""
        state = self.state_manager.get_state()
        labels = self.state_manager.state_labels
        parts = [f"{labels[i]}: {'ON' if state[i] else 'OFF'}" for i in range(len(state))]
        print(f"  [{self.room_name}] {', '.join(parts)}")


# ═══════════════════════════════════════════════
#  Main Entry Point (for Docker containers)
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    node = SCANode()
    node.start_server()
    try:
        print(f"\n{'='*60}")
        print(f"  SCA Node '{node.room_name}' running on port {node.port}")
        print(f"  Raft peers: {list(node.peers.keys())}")
        print(f"{'='*60}\n")
        while True:
            time.sleep(10)
            status = node.get_status()
            logger.info(
                f"[Status] {status['room_name']} | Raft: {status['raft']['state']} "
                f"(term {status['raft']['term']}) | State: {status['state']}"
            )
    except KeyboardInterrupt:
        node.stop_server()
