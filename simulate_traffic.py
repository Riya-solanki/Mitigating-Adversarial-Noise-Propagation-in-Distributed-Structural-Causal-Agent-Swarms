"""
Traffic Intersection Simulation
--------------------------------
Demonstrates the Multi-Agent Causal Dependency Maintenance system
adapted for a traffic management domain with 3 intersections.

Each intersection has 7 phase states:
  NS_Through, NS_Left, EW_Through, EW_Left, Emergency, Pedestrian_NS, Pedestrian_EW

Causal Rules:
  - Left-turn phases require their through-phase (dependency)
  - Pedestrian phases require their direction's through-phase (dependency)
  - Emergency is a root state (always allowed, override priority)
  - NS and EW phases are mutually exclusive (conflict)

Five Core Scenarios:
  1. Normal Cycle Progression
  2. Left-Turn Request (dependency-driven)
  3. Emergency Preemption (override priority)
  4. Pedestrian Request (dependency-driven)
  5. Cross-Intersection Coordination (green wave via gRPC)

Modes:
  --mode local       Run everything in-process (no Docker needed)
  --mode distributed Connect to Docker containers via gRPC
"""

import sys
import os
import time
import argparse
import logging

project_root = os.path.dirname(__file__)
sys.path.append(project_root)
sys.path.append(os.path.join(project_root, 'src', 'proto'))
sys.path.append(os.path.join(project_root, 'src', 'nodes'))
sys.path.append(os.path.join(project_root, 'src', 'watchdog'))
sys.path.append(os.path.join(project_root, 'src', 'consensus'))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s  %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger("TrafficSimulation")


# =======================================================
#  Formatting Helpers
# =======================================================

COLORS = {
    'HEADER':  '\033[95m',
    'BLUE':    '\033[94m',
    'CYAN':    '\033[96m',
    'GREEN':   '\033[92m',
    'YELLOW':  '\033[93m',
    'RED':     '\033[91m',
    'BOLD':    '\033[1m',
    'RESET':   '\033[0m',
}

# Phase state indices for readability
NS_THROUGH = 0
NS_LEFT = 1
EW_THROUGH = 2
EW_LEFT = 3
EMERGENCY = 4
PED_NS = 5
PED_EW = 6

PHASE_ICONS = {
    "NS_Through":    "[G] NS-Thru",
    "NS_Left":       "[Y] NS-Left",
    "EW_Through":    "[G] EW-Thru",
    "EW_Left":       "[Y] EW-Left",
    "Emergency":     "[!] EMERG",
    "Pedestrian_NS": "[P] PedNS",
    "Pedestrian_EW": "[P] PedEW",
}


def header(text):
    print(f"\n{COLORS['BOLD']}{COLORS['HEADER']}{'=' * 70}")
    print(f"  {text}")
    print(f"{'=' * 70}{COLORS['RESET']}\n")


def scenario(num, text):
    print(f"\n{COLORS['BOLD']}{COLORS['CYAN']}{'-' * 70}")
    print(f"  SCENARIO {num}: {text}")
    print(f"{'-' * 70}{COLORS['RESET']}\n")


def action_log(intersection, action, result, detail=""):
    color = COLORS['GREEN'] if result else COLORS['RED']
    symbol = "[OK] APPLIED" if result else "[XX] BLOCKED"
    print(f"  {color}{symbol}{COLORS['RESET']} [{COLORS['BOLD']}{intersection}{COLORS['RESET']}] {action}")
    if detail:
        print(f"         \\-- {COLORS['YELLOW']}{detail}{COLORS['RESET']}")


def print_all_states(nodes):
    """Print current phase states for all intersection nodes."""
    print(f"\n  {COLORS['BLUE']}{'-' * 60}")
    print(f"  Current Phase States:{COLORS['RESET']}")
    for node in nodes.values():
        state = node.state_manager.get_state()
        labels = node.state_manager.state_labels
        parts = []
        for i in range(len(state)):
            icon = PHASE_ICONS.get(labels[i], labels[i])
            if state[i]:
                parts.append(f"{COLORS['GREEN']}{icon}=ON{COLORS['RESET']}")
            else:
                parts.append(f"{icon}=OFF")
        print(f"    {COLORS['BOLD']}{node.room_name}{COLORS['RESET']}:")
        print(f"      {', '.join(parts)}")
    print(f"  {COLORS['BLUE']}{'-' * 60}{COLORS['RESET']}\n")


def print_single_state(node):
    """Print current phase state for a single intersection node."""
    state = node.state_manager.get_state()
    labels = node.state_manager.state_labels
    parts = []
    for i in range(len(state)):
        icon = PHASE_ICONS.get(labels[i], labels[i])
        if state[i]:
            parts.append(f"{COLORS['GREEN']}{icon}=ON{COLORS['RESET']}")
        else:
            parts.append(f"{icon}=OFF")
    print(f"    {COLORS['BOLD']}{node.room_name}{COLORS['RESET']}: {', '.join(parts)}")


def pause(seconds=1.0):
    time.sleep(seconds)


# =======================================================
#  LOCAL MODE -- All intersection nodes in-process
# =======================================================

ACTION_MAP = {
    "KEEP_CURRENT": [0, 0, 0, 0, 0, 0, 0],
    "SWITCH_NS_THROUGH": [1, -1, -1, -1, -1, -1, -1],
    "SWITCH_EW_THROUGH": [-1, -1, 1, -1, -1, -1, -1],
    "ACTIVATE_NS_LEFT": [0, 1, 0, -1, -1, 0, 0],
    "ACTIVATE_EW_LEFT": [-1, -1, 0, 1, -1, 0, 0],
    "EMERGENCY": [-1, -1, -1, -1, 1, -1, -1]
}

def agent_decision_loop(nodes):
    """
    Continuous AI-driven decision loop replacing static scenarios.
    Features: CURRENT STATE tracking, RULES enforcement, DECISION engine, ACTION_MAP, Coordination.
    """
    import random
    header("AI-DRIVEN TRAFFIC COORDINATION LOOP STARTING")
    
    # Initialize basic state
    for name, node in nodes.items():
        node.attempt_local_action("Initialize NS_Through", [1, 0, 0, 0, 0, 0, 0])
        
    for step in range(20): # Run for a set number of cycles
        print(f"\n{COLORS['BOLD']}{COLORS['CYAN']}--- STEP {step+1} ---{COLORS['RESET']}")
        
        for name, node in nodes.items():
            state = node.state_manager
            
            # Simulate fetching CityFlow queue lengths dynamically
            ns_queue = random.randint(0, 30)
            ew_queue = random.randint(0, 30)
            state.update_metrics(ns_queue, ew_queue, ns_queue // 2, ew_queue // 2)
            
            duration = state.get_phase_duration()
            
            # Very basic deterministic 'agent' logic mimicking an LLM policy
            decision = "KEEP_CURRENT"
            reason = "Default behavior"
            
            if random.random() < 0.1:
                decision = "EMERGENCY"
                reason = "Ambulance detected!"
            elif state.state_vector[NS_THROUGH] == 1.0:
                if ew_queue > ns_queue and duration > 15.0:
                    decision = "SWITCH_EW_THROUGH"
                    reason = f"EW queue ({ew_queue}) > NS queue ({ns_queue}) and min green met"
                elif ns_queue > 15 and state.state_vector[NS_LEFT] == 0.0:
                    decision = "ACTIVATE_NS_LEFT"
                    reason = "High NS queue, deploying left turn"
            elif state.state_vector[EW_THROUGH] == 1.0:
                if ns_queue > ew_queue and duration > 15.0:
                    decision = "SWITCH_NS_THROUGH"
                    reason = f"NS queue ({ns_queue}) > EW queue ({ew_queue}) and min green met"
                elif ew_queue > 15 and state.state_vector[EW_LEFT] == 0.0:
                    decision = "ACTIVATE_EW_LEFT"
                    reason = "High EW queue, deploying left turn"
            elif state.state_vector[EMERGENCY] == 1.0:
                if duration > 10.0: # clear emergency
                    decision = "SWITCH_NS_THROUGH"
                    reason = "Emergency cleared"

            print(f"  [{name}] Q: (NS={ns_queue}, EW={ew_queue}) | Dur: {duration:.1f}s | Decision: {decision}")
            
            if decision != "KEEP_CURRENT":
                action_vector = ACTION_MAP[decision]
                ok, msg = node.attempt_local_action(decision, action_vector)
                action_log(name, decision, ok, reason if ok else msg)
                
                # Multi-Agent Coordination (gRPC propagation / Green Wave)
                if ok and "SWITCH" in decision:
                    for peer_name, peer_node in nodes.items():
                        if peer_name != name:
                            print(f"    {COLORS['CYAN']}[Coordination] {name} -> {peer_name}: Sync {decision}{COLORS['RESET']}")
                            peer_node.attempt_local_action(f"Sync {decision}", action_vector)
                            
        pause(1.0)
        
    header("AI-DRIVEN LOOP COMPLETE")

def run_local_simulation():
    """Run the full traffic simulation locally (no Docker, no gRPC networking)."""
    from sca_traffic_node import SCATrafficNode

    header("TRAFFIC INTERSECTION SIMULATION (Local Mode)")

    # Create 3 intersection nodes (no peers for local mode -- in-process)
    intersection_A = SCATrafficNode(node_id="intA", room_name="Main & 1st St", port=50061, peers={})
    intersection_B = SCATrafficNode(node_id="intB", room_name="Main & 2nd St", port=50062, peers={})
    intersection_C = SCATrafficNode(node_id="intC", room_name="Main & 3rd St", port=50063, peers={})

    nodes = {
        "Main & 1st St": intersection_A,
        "Main & 2nd St": intersection_B,
        "Main & 3rd St": intersection_C,
    }

    print("  Intersection nodes initialised (Local Mode - no gRPC networking)")
    print_all_states(nodes)
    pause()
    # Start the new AI loop instead of static scenarios
    agent_decision_loop(nodes)

# =======================================================
#  DISTRIBUTED MODE -- Connect to Docker containers
# =======================================================

def run_distributed_simulation():
    """Connect to running Docker containers via gRPC and run traffic scenarios."""
    import grpc
    import communication_pb2
    import communication_pb2_grpc

    header("TRAFFIC INTERSECTION SIMULATION (Distributed Mode)")

    # Node addresses (matching docker-compose.traffic.yml port mapping)
    nodes_config = {
        "Main & 1st St": "localhost:50051",
        "Main & 2nd St": "localhost:50052",
        "Main & 3rd St": "localhost:50053",
    }

    print("  Connecting to Docker intersection nodes...")
    stubs = {}
    for intersection, address in nodes_config.items():
        try:
            channel = grpc.insecure_channel(address)
            grpc.channel_ready_future(channel).result(timeout=5)
            stubs[intersection] = communication_pb2_grpc.NodeCommunicationStub(channel)
            print(f"    [OK] {intersection} at {address}")
        except Exception as e:
            print(f"    [FAIL] {intersection} at {address} -- {e}")
            print(f"\n  ERROR: Could not connect to all nodes.")
            print(f"  Make sure Docker containers are running:")
            print(f"    docker-compose -f docker-compose.traffic.yml up")
            return

    def send_action(source, target, action_name, action_vector):
        """Send an action proposal via gRPC."""
        stub = stubs[target]
        request = communication_pb2.ActionRequest(
            node_id=source,
            action_type=action_name,
            action_vector=[float(v) for v in action_vector],
            source_room=source,
            target_room=target,
            timestamp=int(time.time()),
        )
        try:
            response = stub.ProposeAction(request, timeout=60.0)
            action_log(target, action_name, response.success,
                       response.message if not response.success else "")
            return response.success
        except Exception as e:
            action_log(target, action_name, False, str(e))
            return False

    pause()

    # -- SCENARIO 1: Normal Cycle --
    scenario(1, "Normal Cycle Progression")
    send_action("Controller", "Main & 1st St", "Activate NS Through", [1, 0, 0, 0, 0, 0, 0])
    pause(0.5)
    send_action("Controller", "Main & 1st St", "Cycle NS->EW", [-1, 0, 1, 0, 0, 0, 0])
    pause(0.5)
    # Test conflict
    send_action("Controller", "Main & 1st St", "NS+EW Both (invalid)", [1, 0, 0, 0, 0, 0, 0])
    pause()

    # -- SCENARIO 2: Left-Turn Dependency --
    scenario(2, "Left-Turn Request (Dependency)")
    send_action("Controller", "Main & 2nd St", "Activate NS Through", [1, 0, 0, 0, 0, 0, 0])
    pause(0.5)
    send_action("Controller", "Main & 2nd St", "Activate NS Left", [0, 1, 0, 0, 0, 0, 0])
    pause(0.5)
    # Test dependency failure on a different intersection
    send_action("Controller", "Main & 3rd St", "NS Left without Through (invalid)", [0, 1, 0, 0, 0, 0, 0])
    pause()

    # -- SCENARIO 3: Emergency Preemption --
    scenario(3, "Emergency Preemption")
    send_action("Emergency", "Main & 1st St", "Emergency Vehicle Detected", [0, 0, 0, 0, 1, 0, 0])
    pause(0.5)
    send_action("Emergency", "Main & 1st St", "Clear EW, Open NS Corridor", [0, 0, -1, 0, 0, 0, 0])
    pause()

    # -- SCENARIO 4: Pedestrian --
    scenario(4, "Pedestrian Request")
    send_action("Pedestrian", "Main & 2nd St", "Pedestrian NS Crossing", [0, 0, 0, 0, 0, 1, 0])
    pause(0.5)
    send_action("Pedestrian", "Main & 3rd St", "Pedestrian EW (no EW active)", [0, 0, 0, 0, 0, 0, 1])
    pause()

    # -- SCENARIO 5: Cross-Intersection Green Wave --
    scenario(5, "Cross-Intersection Green Wave")
    print("  Coordinating NS green across all intersections via gRPC...\n")
    for name in nodes_config:
        send_action("Coordinator", name, "Green Wave: Sync NS", [1, 0, -1, 0, 0, 0, 0])
        pause(0.5)

    header("DISTRIBUTED SIMULATION COMPLETE")


# =======================================================
#  Entry Point
# =======================================================

def main():
    parser = argparse.ArgumentParser(
        description="Traffic Intersection Simulation for Causal Dependency Maintenance"
    )
    parser.add_argument(
        '--mode', choices=['local', 'distributed'], default='local',
        help='Run mode: "local" (in-process) or "distributed" (Docker gRPC)'
    )
    args = parser.parse_args()

    if args.mode == 'distributed':
        run_distributed_simulation()
    else:
        run_local_simulation()


if __name__ == "__main__":
    main()
