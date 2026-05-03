"""
SCA (Smart Causal Agent) Traffic Node — the main distributed node for traffic management.

Wires together:
  - gRPC server (receives RPCs from peers)
  - gRPC client (sends RPCs to peers)
  - Raft consensus (leader election, log replication)
  - Causal Watchdog (action validation)
  - State Manager (state tracking)

Each SCA Traffic node represents one intersection in the traffic network.

Reuses the EXACT same core architecture as the smart-home SCA node.
Only the domain constants (state labels, causal matrix, conflict pairs) differ.
"""

import grpc
import time
import os
import json
import logging
import sys
import threading
from concurrent import futures
from openai import OpenAI

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

# ── OpenRouter / Qwen LLM Client ──
_OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
_OPENROUTER_MODEL   = os.getenv('OPENROUTER_MODEL', 'qwen/qwen-2.5-coder-32b-instruct:free')
_llm_client = OpenAI(
    api_key=_OPENROUTER_API_KEY,
    base_url='https://openrouter.ai/api/v1',
) if _OPENROUTER_API_KEY else None


def _ask_llm(intersection_name: str, current_state: list, action_vector: list,
             state_labels: list, action_description: str) -> tuple:
    """
    Ask the Qwen LLM via OpenRouter whether to approve a traffic action.

    Returns:
        (approved: bool, reasoning: str)
    """
    if not _llm_client:
        logger.warning("[LLM] No API key set — skipping LLM reasoning.")
        return True, "No LLM configured (fallback: approve)"

    # Build a human-readable current-state summary
    active = [state_labels[i] for i, v in enumerate(current_state) if v > 0]
    proposed_on  = [state_labels[i] for i, v in enumerate(action_vector) if v == 1]
    proposed_off = [state_labels[i] for i, v in enumerate(action_vector) if v == -1]

    prompt = f"""You are a traffic signal controller AI for intersection '{intersection_name}'.

Current active signal phases: {active if active else ['None (all red)']}
Proposed action: {action_description}
  - Phases to ACTIVATE: {proposed_on if proposed_on else ['none']}
  - Phases to DEACTIVATE: {proposed_off if proposed_off else ['none']}

Traffic safety rules:
- NS_Through and EW_Through must NEVER be active at the same time.
- Left-turn phases require their corresponding through phase to be active.
- Pedestrian crossings require their corresponding direction through phase.
- Emergency vehicle preemption overrides all normal signals.

Respond with exactly one line:
APPROVE: <one-sentence reason>
  OR
REJECT: <one-sentence reason>"""

    try:
        response = _llm_client.chat.completions.create(
            model=_OPENROUTER_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=80,
            timeout=25,
        )
        reply = response.choices[0].message.content.strip()
        logger.info(f"[LLM] Qwen response for {intersection_name}: {reply}")
        approved = reply.upper().startswith('APPROVE')
        return approved, reply
    except Exception as exc:
        logger.warning(f"[LLM] API error ({exc}) — falling back to Watchdog-only.")
        return True, f"LLM unavailable: {exc}"

# ═══════════════════════════════════════════════════════
#  Traffic Domain Constants
# ═══════════════════════════════════════════════════════

STATE_LABELS = [
    "NS_Through",      # 0: North-South through traffic (green)
    "NS_Left",         # 1: North-South left-turn arrow
    "EW_Through",      # 2: East-West through traffic (green)
    "EW_Left",         # 3: East-West left-turn arrow
    "Emergency",       # 4: Emergency vehicle preemption
    "Pedestrian_NS",   # 5: Pedestrian crossing NS direction
    "Pedestrian_EW",   # 6: Pedestrian crossing EW direction
]
NUM_STATES = len(STATE_LABELS)

# Causal adjacency: A[i][j]=1 means state i is a causal parent of state j
#   - NS_Left requires NS_Through (left-turn needs through-phase)
#   - EW_Left requires EW_Through (left-turn needs through-phase)
#   - Pedestrian_NS requires NS_Through (pedestrian crossing needs direction green)
#   - Pedestrian_EW requires EW_Through (pedestrian crossing needs direction green)
#   - Emergency is a root state (always allowed — override priority)
CAUSAL_MATRIX = [
    [1, 1, 0, 0, 0, 1, 0],  # NS_Through → NS_Left, Pedestrian_NS
    [0, 1, 0, 0, 0, 0, 0],  # NS_Left (needs parent NS_Through)
    [0, 0, 1, 1, 0, 0, 1],  # EW_Through → EW_Left, Pedestrian_EW
    [0, 0, 0, 1, 0, 0, 0],  # EW_Left (needs parent EW_Through)
    [0, 0, 0, 0, 1, 0, 0],  # Emergency (root — always allowed)
    [0, 0, 0, 0, 0, 1, 0],  # Pedestrian_NS (needs NS_Through)
    [0, 0, 0, 0, 0, 0, 1],  # Pedestrian_EW (needs EW_Through)
]

# Conflicts: opposing directions can't both be green
CONFLICT_PAIRS = [
    (0, 2),  # NS_Through ↔ EW_Through (NEVER both green)
    (0, 3),  # NS_Through ↔ EW_Left
    (1, 2),  # NS_Left ↔ EW_Through
    (1, 3),  # NS_Left ↔ EW_Left
    (5, 6),  # Pedestrian_NS ↔ Pedestrian_EW
]


class TrafficNodeCommunicationServicer(communication_pb2_grpc.NodeCommunicationServicer):
    """gRPC service handler — receives RPCs from peer intersection nodes."""

    def __init__(self, sca_node):
        self.node = sca_node

    def ProposeAction(self, request, context):
        """Handle an incoming action proposal from another intersection."""
        logger.info(
            f"[gRPC] Received ProposeAction from {request.source_room}: "
            f"{request.action_type}"
        )

        action_vector = list(request.action_vector)
        current_state = self.node.state_manager.get_state()

        # ── Step 1: Ask the LLM for intelligent reasoning ──
        llm_approved, llm_reason = _ask_llm(
            intersection_name=self.node.room_name,
            current_state=current_state,
            action_vector=action_vector,
            state_labels=STATE_LABELS,
            action_description=request.action_type,
        )
        if not llm_approved:
            logger.warning(f"[LLM] Action REJECTED by Qwen: {llm_reason}")
            self.node.last_llm_decision = f"REJECT | {request.action_type}: {llm_reason}"
            return communication_pb2.ActionResponse(
                success=False,
                message=f"LLM REJECTED: {llm_reason}",
                causally_valid=False,
            )
        logger.info(f"[LLM] Action APPROVED by Qwen: {llm_reason}")
        self.node.last_llm_decision = f"APPROVE | {request.action_type}: {llm_reason}"

        # ── Step 2: Validate through Causal Watchdog (safety net) ──
        # Check minimum green time rule (15s) unless Emergency
        duration = self.node.state_manager.get_phase_duration()
        is_emergency = "EMERGENCY" in request.action_type.upper()
        if duration < 15.0 and not is_emergency:
            reason = f"Blocked: 15s minimum green rule (current duration {duration:.1f}s)"
            logger.warning(f"[gRPC] Action BLOCKED on {self.node.room_name}: {reason}")
            return communication_pb2.ActionResponse(
                success=False, message=reason, causally_valid=False
            )

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
                # Not leader — apply locally anyway for the traffic demo
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

    def GetNodeStatus(self, request, context):
        """Return full node status for the dashboard."""
        raft = self.node.raft.get_status()
        state = self.node.state_manager.get_state()
        return communication_pb2.NodeStatusResponse(
            node_id=self.node.node_id,
            room_name=self.node.room_name,
            state_vector=list(float(v) for v in state),
            state_labels=STATE_LABELS,
            raft_state=raft.get('state', 'UNKNOWN'),
            raft_term=int(raft.get('term', 0)),
            leader_id=str(raft.get('leader_id', '')),
            log_length=int(raft.get('log_length', 0)),
            last_llm_decision=getattr(self.node, 'last_llm_decision', 'No decisions yet'),
            timestamp=int(time.time()),
        )

    def RequestVote(self, request, context):
        """Handle Raft RequestVote RPC."""
        return self.node.raft.handle_request_vote(request)

    def AppendEntries(self, request, context):
        """Handle Raft AppendEntries RPC."""
        return self.node.raft.handle_append_entries(request)


class SCATrafficNode:
    """
    Smart Causal Agent Traffic Node — a distributed node for traffic signal
    causal dependency maintenance.

    Each SCA Traffic node represents an intersection with 7 phase states:
    NS_Through, NS_Left, EW_Through, EW_Left, Emergency, Pedestrian_NS, Pedestrian_EW.

    Reuses the identical Raft, gRPC, CausalWatchdog, and StateManager infrastructure
    from the smart-home SCA node. Only the domain constants differ.
    """

    def __init__(self, node_id=None, room_name=None, host='0.0.0.0', port=50051, peers=None):
        """
        Args:
            node_id:    Unique ID (defaults to NODE_ID env var or "intA").
            room_name:  Human-readable intersection name (defaults to ROOM_NAME env var).
            host:       gRPC server bind address.
            port:       gRPC server port.
            peers:      Dict of {peer_id: "host:port"} or None (reads PEERS env var).
        """
        self.node_id = node_id or os.getenv('NODE_ID', 'intA')
        self.room_name = room_name or os.getenv('ROOM_NAME', self.node_id)
        self.host = host
        self.port = int(os.getenv('PORT', port))
        self.server = None

        # Parse peers from environment or argument
        if peers is not None:
            self.peers = peers
        else:
            self.peers = self._parse_peers_env()

        # ── Core Components (IDENTICAL to smart-home, just different constants) ──
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
        self.last_llm_decision = "No decisions yet"

        logger.info(
            f"[SCA-Traffic {self.room_name}] Initialised | id={self.node_id} | "
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
                f"[SCA-Traffic {self.room_name}] Raft COMMITTED: {description} -> "
                f"{self.state_manager}"
            )

    # ═══════════════════════════════════════════
    #  Server Lifecycle
    # ═══════════════════════════════════════════

    def start_server(self):
        """Start the gRPC server and Raft consensus."""
        self.server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        communication_pb2_grpc.add_NodeCommunicationServicer_to_server(
            TrafficNodeCommunicationServicer(self), self.server
        )
        self.server.add_insecure_port(f'{self.host}:{self.port}')
        self.server.start()
        self.raft.start()
        logger.info(
            f"[SCA-Traffic {self.room_name}] Server started on {self.host}:{self.port}"
        )

    def stop_server(self):
        """Stop the gRPC server and Raft consensus."""
        self.raft.stop()
        if self.server:
            self.server.stop(0)
        logger.info(f"[SCA-Traffic {self.room_name}] Server stopped.")

    # ═══════════════════════════════════════════
    #  Local Actions (used by simulation scripts)
    # ═══════════════════════════════════════════

    def attempt_local_action(self, action_name, action_vector):
        """
        Attempt to apply an action locally on this intersection node.
        Validates causally, then applies if valid.

        Returns:
            (success: bool, message: str)
        """
        current_state = self.state_manager.get_state()
        # Check minimum green time rule (15s) unless Emergency
        duration = self.state_manager.get_phase_duration()
        is_emergency = "EMERGENCY" in action_name.upper()
        if duration < 15.0 and not is_emergency:
            reason_text = f"Blocked by 15s minimum green rule (duration {duration:.1f}s)"
            logger.warning(
                f"[SCA-Traffic {self.room_name}] BLOCKED: {action_name} — {reason_text}"
            )
            return False, reason_text

        is_valid, reasons = self.watchdog.validate_action_detailed(
            current_state, action_vector
        )

        if not is_valid:
            reason_text = "; ".join(reasons)
            logger.warning(
                f"[SCA-Traffic {self.room_name}] BLOCKED: {action_name} — {reason_text}"
            )
            return False, reason_text

        # Apply the action
        self.state_manager.apply_action(action_vector, action_name)
        logger.info(
            f"[SCA-Traffic {self.room_name}] Applied: {action_name} -> {self.state_manager}"
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
        Send an action proposal to a peer intersection node via gRPC.

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
            f"[SCA-Traffic {self.room_name}] Sending action to {target_peer_id} at {address}: "
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

            response = stub.ProposeAction(request, timeout=30.0)
            channel.close()

            if response.success:
                logger.info(
                    f"[SCA-Traffic {self.room_name}] Action accepted by {target_peer_id}: "
                    f"{response.message}"
                )
            else:
                logger.warning(
                    f"[SCA-Traffic {self.room_name}] Action rejected by {target_peer_id}: "
                    f"{response.message}"
                )

            return response.success, response.message

        except Exception as e:
            logger.error(f"[SCA-Traffic {self.room_name}] gRPC error to {target_peer_id}: {e}")
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
        """Print current phase state in a formatted way."""
        state = self.state_manager.get_state()
        labels = self.state_manager.state_labels
        parts = [f"{labels[i]}: {'ON' if state[i] else 'OFF'}" for i in range(len(state))]
        print(f"  [{self.room_name}] {', '.join(parts)}")


# ═══════════════════════════════════════════════
#  Main Entry Point (for Docker containers)
# ═══════════════════════════════════════════════

if __name__ == '__main__':
    node = SCATrafficNode()
    node.start_server()
    try:
        print(f"\n{'='*60}")
        print(f"  SCA Traffic Node '{node.room_name}' running on port {node.port}")
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
