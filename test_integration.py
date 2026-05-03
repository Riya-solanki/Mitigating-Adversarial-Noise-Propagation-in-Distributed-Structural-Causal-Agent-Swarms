"""
Integration Tests — Multi-node end-to-end scenarios.

Tests the full flow: SCA Node → Causal Watchdog → State Manager
without Docker (all in-process).

Run: pytest test_integration.py -v
"""

import sys
import os
import time
import pytest

_src = os.path.abspath('src')
sys.path.insert(0, os.path.join(_src, 'nodes'))
sys.path.insert(0, os.path.join(_src, 'watchdog'))
sys.path.insert(0, os.path.join(_src, 'consensus'))
sys.path.insert(0, os.path.join(_src, 'proto'))
from sca_node import SCANode
from state_manager import NodeState, NodeAction
from causal_gate import CausalWatchdog


class TestSCANodeLocal:
    """Test SCA node local actions (no gRPC)."""

    @pytest.fixture
    def room(self):
        node = SCANode(node_id="test_node", room_name="Test Room", port=50099, peers={})
        yield node

    def test_initial_state_all_off(self, room):
        state = room.state_manager.get_state()
        assert state == [0.0, 0.0, 0.0, 0.0]

    def test_activate_presence(self, room):
        ok, msg = room.attempt_local_action("Detect Presence", [1, 0, 0, 0])
        assert ok is True
        assert room.state_manager.get_state()[0] == 1.0

    def test_lights_blocked_without_presence(self, room):
        ok, msg = room.attempt_local_action("Turn on Lights", [0, 1, 0, 0])
        assert ok is False
        assert room.state_manager.get_state()[1] == 0.0

    def test_lights_allowed_with_presence(self, room):
        room.attempt_local_action("Detect Presence", [1, 0, 0, 0])
        ok, msg = room.attempt_local_action("Turn on Lights", [0, 1, 0, 0])
        assert ok is True
        assert room.state_manager.get_state()[1] == 1.0

    def test_ac_window_conflict(self, room):
        room.attempt_local_action("Turn on AC", [0, 0, 1, 0])
        assert room.state_manager.get_state()[2] == 1.0

        ok, msg = room.attempt_local_action("Open Window", [0, 0, 0, 1])
        assert ok is False
        assert room.state_manager.get_state()[3] == 0.0

    def test_turn_off_ac_then_open_window(self, room):
        room.attempt_local_action("Turn on AC", [0, 0, 1, 0])
        room.attempt_local_action("Turn off AC", [0, 0, -1, 0])
        assert room.state_manager.get_state()[2] == 0.0

        ok, msg = room.attempt_local_action("Open Window", [0, 0, 0, 1])
        assert ok is True
        assert room.state_manager.get_state()[3] == 1.0

    def test_combined_turn_off_ac_open_window(self, room):
        room.attempt_local_action("Turn on AC", [0, 0, 1, 0])
        ok, msg = room.attempt_local_action("Off AC + Open Window", [0, 0, -1, 1])
        assert ok is True
        assert room.state_manager.get_state()[2] == 0.0
        assert room.state_manager.get_state()[3] == 1.0


class TestMultiRoomScenario:
    """Test multi-room scenarios using in-process nodes."""

    @pytest.fixture
    def rooms(self):
        living_room = SCANode(node_id="node1", room_name="Living Room", port=50081, peers={})
        kitchen = SCANode(node_id="node2", room_name="Kitchen", port=50082, peers={})
        bedroom = SCANode(node_id="node3", room_name="Bedroom", port=50083, peers={})
        return {
            "living_room": living_room,
            "kitchen": kitchen,
            "bedroom": bedroom,
        }

    def test_central_ac_scenario(self, rooms):
        """All rooms turn on AC independently."""
        for name, room in rooms.items():
            ok, _ = room.attempt_local_action("Turn on AC", [0, 0, 1, 0])
            assert ok is True
            assert room.state_manager.get_state()[2] == 1.0

    def test_bedtime_scenario(self, rooms):
        """User goes to bedroom, lights go off everywhere."""
        lr = rooms["living_room"]
        kt = rooms["kitchen"]
        br = rooms["bedroom"]

        # Set up: presence and lights on in living room
        lr.attempt_local_action("Presence", [1, 0, 0, 0])
        lr.attempt_local_action("Lights on", [0, 1, 0, 0])
        assert lr.state_manager.get_state()[1] == 1.0

        # Bedtime: turn off lights in living room
        ok, _ = lr.attempt_local_action("Lights off", [0, -1, 0, 0])
        assert ok is True
        assert lr.state_manager.get_state()[1] == 0.0

        # Bedroom: presence detected
        ok, _ = br.attempt_local_action("Presence", [1, 0, 0, 0])
        assert ok is True
        assert br.state_manager.get_state()[0] == 1.0

    def test_kitchen_emergency_scenario(self, rooms):
        """Smoke in kitchen — open window, can't turn on AC while window is open."""
        kt = rooms["kitchen"]
        lr = rooms["living_room"]

        # Kitchen has AC on
        kt.attempt_local_action("AC on", [0, 0, 1, 0])
        assert kt.state_manager.get_state()[2] == 1.0

        # Smoke: turn off AC, open window
        ok, _ = kt.attempt_local_action("Smoke — off AC + open window", [0, 0, -1, 1])
        assert ok is True
        assert kt.state_manager.get_state()[2] == 0.0
        assert kt.state_manager.get_state()[3] == 1.0

        # Living room also opens window
        ok, _ = lr.attempt_local_action("Ventilation — open window", [0, 0, 0, 1])
        assert ok is True

        # Living room tries to turn on AC — should FAIL (window is open)
        ok, msg = lr.attempt_local_action("Resume AC", [0, 0, 1, 0])
        assert ok is False
        assert "CONFLICT" in msg

    def test_recovery_after_emergency(self, rooms):
        """After emergency, close windows, then AC works again."""
        lr = rooms["living_room"]

        # Open window
        lr.attempt_local_action("Open window", [0, 0, 0, 1])
        assert lr.state_manager.get_state()[3] == 1.0

        # AC blocked
        ok, _ = lr.attempt_local_action("AC on", [0, 0, 1, 0])
        assert ok is False

        # Close window
        lr.attempt_local_action("Close window", [0, 0, 0, -1])
        assert lr.state_manager.get_state()[3] == 0.0

        # AC now works
        ok, _ = lr.attempt_local_action("AC on", [0, 0, 1, 0])
        assert ok is True
        assert lr.state_manager.get_state()[2] == 1.0


class TestStateManagerIntegration:
    """Test state manager features within SCA node."""

    def test_history_tracking(self):
        node = SCANode(node_id="hist", room_name="HistRoom", port=50090, peers={})
        node.attempt_local_action("Presence", [1, 0, 0, 0])
        node.attempt_local_action("AC on", [0, 0, 1, 0])

        history = node.state_manager.get_history()
        assert len(history) >= 2

    def test_state_serialisation(self):
        state = NodeState(4, ["A", "B", "C", "D"])
        state.apply_action([1, 0, 1, 0], "test")

        data = state.to_bytes()
        restored = NodeState.from_bytes(data)
        assert restored.get_state() == [1.0, 0.0, 1.0, 0.0]
        assert restored.state_labels == ["A", "B", "C", "D"]

    def test_node_status(self):
        node = SCANode(node_id="s1", room_name="StatusRoom", port=50091, peers={})
        status = node.get_status()
        assert status['node_id'] == "s1"
        assert status['room_name'] == "StatusRoom"
        assert 'state_vector' in status
        assert 'raft' in status


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
