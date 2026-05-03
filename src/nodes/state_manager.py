"""
State Manager for SCA Nodes.

Tracks the full state vector of a node with:
  - Human-readable state labels
  - State history / transition log
  - Serialisation to/from bytes (for gRPC payloads)
  - Conflict-aware state application
"""

import json
import time
import logging
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class NodeState:
    """
    Manages the state vector for a single SCA node (e.g., a smart-home room).

    State vector is a list of floats (0.0 or 1.0) representing whether
    each state dimension is active or inactive.
    """

    def __init__(self, num_states, state_labels=None):
        """
        Args:
            num_states:   Number of state dimensions.
            state_labels: Optional list of human-readable names per dimension.
        """
        self.num_states = num_states
        self.state_vector = [0.0] * num_states
        self.state_labels = state_labels or [f"State_{i}" for i in range(num_states)]
        self.history = []  # List of (timestamp, action_description, old_state, new_state)
        
        # CURRENT STATE Tracking
        self.phase_start_time = time.time()
        self.ns_queue = 0
        self.ew_queue = 0
        self.ns_throughput = 0
        self.ew_throughput = 0

    def get_phase_duration(self):
        """Return how long the current phase has been active in seconds."""
        return time.time() - self.phase_start_time

    def update_metrics(self, ns_q, ew_q, ns_th, ew_th):
        """Update traffic metrics (queues and throughput)."""
        self.ns_queue = ns_q
        self.ew_queue = ew_q
        self.ns_throughput = ns_th
        self.ew_throughput = ew_th

    def get_state(self):
        """Return a copy of the current state vector."""
        return list(self.state_vector)

    def update_state(self, index, value):
        """Update a single state dimension."""
        if 0 <= index < self.num_states:
            old = self.state_vector[index]
            self.state_vector[index] = float(value)
            if old != float(value):
                label = self.state_labels[index]
                action_desc = f"{label}: {'ON' if value else 'OFF'}"
                self._record_history(action_desc)
        else:
            logger.warning(f"[State] Invalid index {index} for {self.num_states}-state vector")

    def apply_action(self, action_vector, description=""):
        """
        Apply an action vector to the current state.

        Args:
            action_vector: List where 1=activate, -1=deactivate, 0=no change.
            description:   Human-readable description for the log.

        Returns:
            List of the new state vector.
        """
        old_state = list(self.state_vector)
        phase_changed = False

        for i in range(min(len(action_vector), self.num_states)):
            if action_vector[i] == 1:
                if self.state_vector[i] != 1.0: phase_changed = True
                self.state_vector[i] = 1.0
            elif action_vector[i] == -1:
                if self.state_vector[i] != 0.0: phase_changed = True
                self.state_vector[i] = 0.0
            # 0 = no change

        if phase_changed:
            self.phase_start_time = time.time()

        self._record_history(description or f"Action: {action_vector}")
        logger.info(f"[State] {description}: {self._format_state(old_state)} -> {self._format_state()}")
        return list(self.state_vector)

    def set_state(self, state_vector, description=""):
        """Set the entire state vector directly."""
        old_state = list(self.state_vector)
        self.state_vector = [float(v) for v in state_vector[:self.num_states]]
        self._record_history(description or "Direct state set")
        return list(self.state_vector)

    def _record_history(self, description):
        """Record a state transition in the history log."""
        self.history.append({
            'timestamp': time.time(),
            'description': description,
            'state': list(self.state_vector),
        })

    def get_history(self, last_n=None):
        """
        Get state transition history.

        Args:
            last_n: If provided, return only the last N entries.
        """
        if last_n:
            return self.history[-last_n:]
        return list(self.history)

    def _format_state(self, state=None):
        """Format state as a human-readable string."""
        s = state if state is not None else self.state_vector
        parts = []
        for i in range(self.num_states):
            label = self.state_labels[i]
            value = "ON" if s[i] == 1.0 else "OFF"
            parts.append(f"{label}={value}")
        return "{" + ", ".join(parts) + "}"

    def to_dict(self):
        """Serialise state to a dict (for JSON/gRPC payload)."""
        return {
            'state_vector': list(self.state_vector),
            'state_labels': self.state_labels,
            'num_states': self.num_states,
            'phase_duration': self.get_phase_duration(),
            'ns_queue': self.ns_queue,
            'ew_queue': self.ew_queue,
            'ns_throughput': self.ns_throughput,
            'ew_throughput': self.ew_throughput,
        }

    def to_bytes(self):
        """Serialise state to bytes (for gRPC payload field)."""
        return json.dumps(self.to_dict()).encode('utf-8')

    @classmethod
    def from_bytes(cls, data):
        """Deserialise state from bytes."""
        d = json.loads(data.decode('utf-8'))
        state = cls(d['num_states'], d.get('state_labels'))
        state.state_vector = [float(v) for v in d['state_vector']]
        return state

    @classmethod
    def from_dict(cls, d):
        """Deserialise state from a dict."""
        state = cls(d['num_states'], d.get('state_labels'))
        state.state_vector = [float(v) for v in d['state_vector']]
        return state

    def __repr__(self):
        return self._format_state()


class NodeAction:
    """Represents an action proposed by or to a node."""

    def __init__(self, action_type, action_vector, source_room="", target_room="", description=""):
        self.action_type = action_type
        self.action_vector = action_vector
        self.source_room = source_room
        self.target_room = target_room
        self.description = description
        self.timestamp = time.time()

    def to_dict(self):
        return {
            'action_type': self.action_type,
            'action_vector': self.action_vector,
            'source_room': self.source_room,
            'target_room': self.target_room,
            'description': self.description,
            'timestamp': self.timestamp,
        }

    @classmethod
    def from_dict(cls, d):
        action = cls(
            action_type=d['action_type'],
            action_vector=d['action_vector'],
            source_room=d.get('source_room', ''),
            target_room=d.get('target_room', ''),
            description=d.get('description', ''),
        )
        action.timestamp = d.get('timestamp', time.time())
        return action

    def __repr__(self):
        return f"NodeAction({self.action_type}: {self.description})"
