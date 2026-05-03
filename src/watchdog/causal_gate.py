"""
Causal Watchdog (CausalPlan Gate) for Multi-Agent Causal Dependency Maintenance.

Validates proposed actions against a causal dependency graph to ensure that
no action violates causal ordering. Supports:
  - Dependency validation (parent states must be active)
  - Conflict detection (mutually exclusive states)
  - Dynamic graph updates
  - Detailed human-readable rejection reasons
"""

import numpy as np
import logging
from typing import List, Tuple, Optional, Dict

logger = logging.getLogger(__name__)


class CausalWatchdog:
    """
    Validates proposed actions against a causal adjacency matrix and conflict rules.

    Causal Matrix Semantics:
        adjacency_matrix[i][j] = 1 means state i is a causal parent of state j.
        To activate state j, at least one parent i (where A[i][j]=1) must be active,
        OR j has no parents (self-caused / root state).

    Conflict Matrix Semantics:
        conflict_matrix[i][j] = 1 means states i and j are mutually exclusive.
        You cannot have both active simultaneously.
    """

    def __init__(self, adjacency_matrix, state_labels=None, conflict_pairs=None):
        """
        Args:
            adjacency_matrix: 2D list/array where A[i][j]=1 means i causes j.
            state_labels:     Optional list of human-readable names for each state.
            conflict_pairs:   Optional list of (i, j) tuples for mutually exclusive states.
        """
        self.adjacency_matrix = np.array(adjacency_matrix, dtype=float)
        self.num_states = self.adjacency_matrix.shape[0]

        # State labels
        if state_labels:
            self.state_labels = state_labels
        else:
            self.state_labels = [f"State_{i}" for i in range(self.num_states)]

        # Conflict matrix (symmetric)
        self.conflict_matrix = np.zeros((self.num_states, self.num_states), dtype=float)
        if conflict_pairs:
            for i, j in conflict_pairs:
                self.conflict_matrix[i][j] = 1
                self.conflict_matrix[j][i] = 1

        logger.info(f"[CausalWatchdog] Initialised with {self.num_states} states: {self.state_labels}")

    def validate_action(self, current_state_vector, proposed_action_vector):
        """
        Validates if the proposed action is causally valid given the current state.

        Args:
            current_state_vector:  List of current state values (0/1).
            proposed_action_vector: List where 1=activate, -1=deactivate, 0=no change.

        Returns:
            bool: True if action is valid, False otherwise.
        """
        is_valid, reasons = self.validate_action_detailed(current_state_vector, proposed_action_vector)
        return is_valid

    def validate_action_detailed(self, current_state_vector, proposed_action_vector):
        """
        Validates action and returns detailed reasons for any rejection.

        Returns:
            (is_valid: bool, reasons: List[str])
        """
        current = np.array(current_state_vector, dtype=float)
        action = np.array(proposed_action_vector, dtype=float)
        reasons = []

        # Compute the resulting state after applying the action
        resulting_state = current.copy()
        for i in range(self.num_states):
            if action[i] == 1:
                resulting_state[i] = 1
            elif action[i] == -1:
                resulting_state[i] = 0

        # ─── Check 1: Causal Dependency Validation ───
        # For each state being ACTIVATED (action[i] == 1),
        # check that at least one causal parent is active in the current state.
        for i in range(self.num_states):
            if action[i] != 1:
                continue  # Only check activations

            # Get parents of state i: all j where adjacency_matrix[j][i] == 1, j != i
            parents = []
            for j in range(self.num_states):
                if j != i and self.adjacency_matrix[j][i] == 1:
                    parents.append(j)

            if not parents:
                # No external parents — this is a root/self-caused state, always allowed
                continue

            # At least one parent must be active in the current state
            parent_active = any(current[p] == 1 for p in parents)
            if not parent_active:
                parent_names = [self.state_labels[p] for p in parents]
                reasons.append(
                    f"DEPENDENCY: Cannot activate '{self.state_labels[i]}' — "
                    f"requires at least one active parent: {parent_names}"
                )

        # ─── Check 2: Conflict Detection ───
        # After applying the action, no two conflicting states should both be active.
        for i in range(self.num_states):
            for j in range(i + 1, self.num_states):
                if self.conflict_matrix[i][j] == 1:
                    if resulting_state[i] == 1 and resulting_state[j] == 1:
                        reasons.append(
                            f"CONFLICT: '{self.state_labels[i]}' and "
                            f"'{self.state_labels[j]}' cannot both be active"
                        )

        is_valid = len(reasons) == 0

        if is_valid:
            logger.info(f"[CausalWatchdog] VALID: {self._format_action(action)}")
        else:
            for reason in reasons:
                logger.warning(f"[CausalWatchdog] BLOCKED -- {reason}")

        return is_valid, reasons

    def get_valid_actions(self, current_state_vector):
        """
        Given the current state, return a mask of which states CAN be activated.

        Returns:
            List[bool]: For each state, True if it can be activated from current state.
        """
        current = np.array(current_state_vector, dtype=float)
        can_activate = []

        for i in range(self.num_states):
            if current[i] == 1:
                can_activate.append(False)  # Already active
                continue

            # Check parents
            parents = [j for j in range(self.num_states)
                       if j != i and self.adjacency_matrix[j][i] == 1]

            if parents:
                parent_active = any(current[p] == 1 for p in parents)
                if not parent_active:
                    can_activate.append(False)
                    continue

            # Check conflicts (applies to ALL states, including root states)
            has_conflict = False
            for j in range(self.num_states):
                if self.conflict_matrix[i][j] == 1 and current[j] == 1:
                    has_conflict = True
                    break

            can_activate.append(not has_conflict)

        return can_activate

    def update_adjacency_matrix(self, new_matrix):
        """Update the causal dependency graph at runtime."""
        self.adjacency_matrix = np.array(new_matrix, dtype=float)
        self.num_states = self.adjacency_matrix.shape[0]
        logger.info(f"[CausalWatchdog] Adjacency matrix updated ({self.num_states} states)")

    def add_conflict(self, state_i, state_j):
        """Add a conflict between two states."""
        self.conflict_matrix[state_i][state_j] = 1
        self.conflict_matrix[state_j][state_i] = 1
        logger.info(
            f"[CausalWatchdog] Added conflict: "
            f"'{self.state_labels[state_i]}' <-> '{self.state_labels[state_j]}'"
        )

    def remove_conflict(self, state_i, state_j):
        """Remove a conflict between two states."""
        self.conflict_matrix[state_i][state_j] = 0
        self.conflict_matrix[state_j][state_i] = 0

    def get_causal_parents(self, state_index):
        """Get the list of causal parent indices for a given state."""
        parents = []
        for j in range(self.num_states):
            if j != state_index and self.adjacency_matrix[j][state_index] == 1:
                parents.append(j)
        return parents

    def get_causal_children(self, state_index):
        """Get the list of states that depend on a given state."""
        children = []
        for j in range(self.num_states):
            if j != state_index and self.adjacency_matrix[state_index][j] == 1:
                children.append(j)
        return children

    def _format_action(self, action):
        """Format an action vector as a human-readable string."""
        parts = []
        for i in range(self.num_states):
            if action[i] == 1:
                parts.append(f"+{self.state_labels[i]}")
            elif action[i] == -1:
                parts.append(f"-{self.state_labels[i]}")
        return ", ".join(parts) if parts else "(no-op)"

    def __repr__(self):
        return (
            f"CausalWatchdog(states={self.state_labels}, "
            f"edges={int(self.adjacency_matrix.sum())}, "
            f"conflicts={int(self.conflict_matrix.sum() // 2)})"
        )
