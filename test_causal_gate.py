"""
Tests for the Causal Watchdog (CausalPlan Gate).
Run: pytest test_causal_gate.py -v
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.abspath('src'), 'watchdog'))
from causal_gate import CausalWatchdog


# ── Smart-Home causal matrix ──
# States: 0=Presence, 1=Lights, 2=AC, 3=Window
# Presence -> Lights (presence is a parent of lights)
ADJ_MATRIX = [
    [1, 1, 0, 0],
    [0, 1, 0, 0],
    [0, 0, 1, 0],
    [0, 0, 0, 1],
]
LABELS = ["Presence", "Lights", "AC", "Window"]
CONFLICTS = [(2, 3)]  # AC ↔ Window


@pytest.fixture
def watchdog():
    return CausalWatchdog(ADJ_MATRIX, state_labels=LABELS, conflict_pairs=CONFLICTS)


class TestCausalDependency:
    """Test that causal parent dependencies are enforced."""

    def test_activate_root_state(self, watchdog):
        """Root states (Presence, AC, Window) can always be activated."""
        current = [0, 0, 0, 0]
        assert watchdog.validate_action(current, [1, 0, 0, 0]) is True  # Presence
        assert watchdog.validate_action(current, [0, 0, 1, 0]) is True  # AC
        assert watchdog.validate_action(current, [0, 0, 0, 1]) is True  # Window

    def test_lights_require_presence(self, watchdog):
        """Lights cannot be turned on without Presence."""
        current = [0, 0, 0, 0]
        assert watchdog.validate_action(current, [0, 1, 0, 0]) is False

    def test_lights_with_presence(self, watchdog):
        """Lights CAN be turned on when Presence is active."""
        current = [1, 0, 0, 0]
        assert watchdog.validate_action(current, [0, 1, 0, 0]) is True

    def test_activate_presence_and_lights_together(self, watchdog):
        """Activating Presence and Lights in same action — Lights still needs
        Presence to be already active in current state."""
        current = [0, 0, 0, 0]
        # Presence is being activated, but it's not yet in current state
        result = watchdog.validate_action(current, [1, 1, 0, 0])
        # This should fail because Lights checks current state where Presence=0
        assert result is False

    def test_lights_with_presence_already_on(self, watchdog):
        """When Presence is already on, adding Lights is fine."""
        current = [1, 0, 0, 0]
        assert watchdog.validate_action(current, [0, 1, 0, 0]) is True


class TestConflictDetection:
    """Test that mutually exclusive states are enforced."""

    def test_ac_and_window_conflict(self, watchdog):
        """Cannot turn on AC when Window is already open."""
        current = [0, 0, 0, 1]  # Window is open
        assert watchdog.validate_action(current, [0, 0, 1, 0]) is False

    def test_window_and_ac_conflict(self, watchdog):
        """Cannot open Window when AC is already on."""
        current = [0, 0, 1, 0]  # AC is on
        assert watchdog.validate_action(current, [0, 0, 0, 1]) is False

    def test_ac_alone_is_fine(self, watchdog):
        """AC can be activated when Window is off."""
        current = [0, 0, 0, 0]
        assert watchdog.validate_action(current, [0, 0, 1, 0]) is True

    def test_window_alone_is_fine(self, watchdog):
        """Window can be opened when AC is off."""
        current = [0, 0, 0, 0]
        assert watchdog.validate_action(current, [0, 0, 0, 1]) is True

    def test_turn_off_ac_open_window(self, watchdog):
        """Deactivating AC (-1) while opening Window (+1) should be fine."""
        current = [0, 0, 1, 0]  # AC is on
        assert watchdog.validate_action(current, [0, 0, -1, 1]) is True


class TestDeactivation:
    """Test that deactivation (action=-1) always works."""

    def test_deactivate_any_state(self, watchdog):
        current = [1, 1, 1, 0]
        assert watchdog.validate_action(current, [-1, 0, 0, 0]) is True
        assert watchdog.validate_action(current, [0, -1, 0, 0]) is True
        assert watchdog.validate_action(current, [0, 0, -1, 0]) is True

    def test_noop_always_valid(self, watchdog):
        current = [1, 1, 0, 0]
        assert watchdog.validate_action(current, [0, 0, 0, 0]) is True


class TestDetailedReasons:
    """Test that validate_action_detailed returns proper rejection reasons."""

    def test_dependency_reason(self, watchdog):
        current = [0, 0, 0, 0]
        is_valid, reasons = watchdog.validate_action_detailed(current, [0, 1, 0, 0])
        assert is_valid is False
        assert len(reasons) >= 1
        assert "DEPENDENCY" in reasons[0]

    def test_conflict_reason(self, watchdog):
        current = [0, 0, 0, 1]  # Window open
        is_valid, reasons = watchdog.validate_action_detailed(current, [0, 0, 1, 0])
        assert is_valid is False
        assert any("CONFLICT" in r for r in reasons)

    def test_valid_has_no_reasons(self, watchdog):
        current = [1, 0, 0, 0]
        is_valid, reasons = watchdog.validate_action_detailed(current, [0, 1, 0, 0])
        assert is_valid is True
        assert len(reasons) == 0


class TestValidActionMask:
    """Test the get_valid_actions mask."""

    def test_from_empty_state(self, watchdog):
        current = [0, 0, 0, 0]
        mask = watchdog.get_valid_actions(current)
        # Presence=True (root), Lights=False (needs Presence), AC=True, Window=True
        assert mask[0] is True   # Presence
        assert mask[1] is False  # Lights (no parent)
        assert mask[2] is True   # AC
        assert mask[3] is True   # Window

    def test_with_presence(self, watchdog):
        current = [1, 0, 0, 0]
        mask = watchdog.get_valid_actions(current)
        assert mask[0] is False  # Already active
        assert mask[1] is True   # Lights now possible
        assert mask[2] is True
        assert mask[3] is True

    def test_with_ac_on(self, watchdog):
        current = [0, 0, 1, 0]
        mask = watchdog.get_valid_actions(current)
        assert mask[3] is False  # Window blocked by conflict with AC


class TestDynamicUpdates:
    """Test dynamic graph updates."""

    def test_add_conflict(self, watchdog):
        # Initially Presence and AC have no conflict
        current = [1, 0, 0, 0]
        assert watchdog.validate_action(current, [0, 0, 1, 0]) is True

        # Add conflict
        watchdog.add_conflict(0, 2)  # Presence ↔ AC
        assert watchdog.validate_action(current, [0, 0, 1, 0]) is False

        # Remove conflict
        watchdog.remove_conflict(0, 2)
        assert watchdog.validate_action(current, [0, 0, 1, 0]) is True


class TestChainedDependency:
    """Test with a longer causal chain: A → B → C → D → E."""

    @pytest.fixture
    def chain_watchdog(self):
        adj = [
            [1, 1, 0, 0, 0],  # A causes B
            [0, 1, 1, 0, 0],  # B causes C
            [0, 0, 1, 1, 0],  # C causes D
            [0, 0, 0, 1, 1],  # D causes E
            [0, 0, 0, 0, 1],  # E (leaf)
        ]
        return CausalWatchdog(adj, state_labels=["A", "B", "C", "D", "E"])

    def test_chain_valid_step_by_step(self, chain_watchdog):
        """Can activate B when A is on."""
        current = [1, 0, 0, 0, 0]
        assert chain_watchdog.validate_action(current, [0, 1, 0, 0, 0]) is True

    def test_chain_skip_fails(self, chain_watchdog):
        """Cannot activate C directly from A (skipping B)."""
        current = [1, 0, 0, 0, 0]
        assert chain_watchdog.validate_action(current, [0, 0, 1, 0, 0]) is False

    def test_chain_deep_skip_fails(self, chain_watchdog):
        """Cannot activate E from A."""
        current = [1, 0, 0, 0, 0]
        assert chain_watchdog.validate_action(current, [0, 0, 0, 0, 1]) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
