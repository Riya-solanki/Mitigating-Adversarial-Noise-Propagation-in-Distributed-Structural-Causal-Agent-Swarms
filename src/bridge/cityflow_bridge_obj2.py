"""
CityFlow Bridge — final version with:
  1. LLM-driven phase decisions (Qwen via OpenRouter)
  2. Box-clear check before phase switch (gridlock prevention)
  3. Yellow transition phase between greens
  4. WebSocket live streaming to React
  5. gRPC → Causal Watchdog → Raft consensus pipeline
"""

import cityflow
import grpc
import time
import json
import os
import sys
import queue
import threading
import asyncio
import logging

try:
    import websockets
    _WS_AVAILABLE = True
except ImportError:
    _WS_AVAILABLE = False
    logging.warning("[Bridge] websockets not installed. Run: pip install websockets")

try:
    from openai import OpenAI
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False

sys.path.append('/app/src/proto')
import communication_pb2
import communication_pb2_grpc

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [Bridge] %(levelname)s  %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger(__name__)

# ── LLM client (same OpenRouter setup as sca_traffic_node.py) ──────────────
_OPENROUTER_API_KEY = os.getenv('OPENROUTER_API_KEY', '')
_OPENROUTER_MODEL   = os.getenv('OPENROUTER_MODEL', 'qwen/qwen-2.5-coder-32b-instruct:free')

_llm_client = None
if _OPENAI_AVAILABLE and _OPENROUTER_API_KEY:
    _llm_client = OpenAI(
        api_key=_OPENROUTER_API_KEY,
        base_url='https://openrouter.ai/api/v1',
    )
    logger.info(f"[LLM] Client initialised — model: {_OPENROUTER_MODEL}")
else:
    logger.warning("[LLM] No API key — will use heuristic decisions only")

# ── Phase mappings ──────────────────────────────────────────────────────────
CITYFLOW_PHASE_TO_SCA = {
    0: [1, 0, 0, 0, 0, 0, 0],  # NS_Through
    1: [0, 0, 0, 0, 0, 0, 0],  # Yellow
    2: [0, 1, 0, 0, 0, 0, 0],  # NS_Left
    3: [0, 0, 0, 0, 0, 0, 0],  # Yellow
    4: [0, 0, 1, 0, 0, 0, 0],  # EW_Through
    5: [0, 0, 0, 0, 0, 0, 0],  # Yellow
    6: [0, 0, 0, 1, 0, 0, 0],  # EW_Left
    7: [0, 0, 0, 0, 0, 0, 0],  # Yellow
}

PHASE_ID_TO_NAME = {
    0: 'NS_Through', 1: 'Yellow',
    2: 'NS_Left',    3: 'Yellow',
    4: 'EW_Through', 5: 'Yellow',
    6: 'EW_Left',    7: 'Yellow',
}

YELLOW_PHASE_ID = 1      # all-red / yellow phase in CityFlow
BOX_SIZE        = int(os.getenv('BOX_SIZE', 55))   # intersection box half-width
MAX_YELLOW_STEPS = int(os.getenv('MAX_YELLOW_STEPS', 6))  # max steps to hold yellow
WS_PORT         = int(os.getenv('WS_PORT', 8765))


def sca_to_cityflow_phase(action_vector):
    for phase_id, vec in CITYFLOW_PHASE_TO_SCA.items():
        if list(vec) == list(action_vector):
            return phase_id
    return -1


def sum_lanes(lane_counts, roads):
    total = 0
    for lane, count in lane_counts.items():
        if any(lane.startswith(r) for r in roads):
            total += count
    return total


# ── LLM phase decision ──────────────────────────────────────────────────────

def ask_llm_phase(current_phase_name, ns_queue, ew_queue, step, phase_duration):
    """
    Ask Qwen which phase the intersection should run next.
    Returns (decision: str, reason: str)
    decision is one of: 'KEEP', 'SWITCH_NS', 'SWITCH_EW'
    """
    if not _llm_client:
        return _heuristic(current_phase_name, ns_queue, ew_queue)

    prompt = f"""You are an AI traffic signal controller for a busy city intersection.

CURRENT STATE:
- Active phase: {current_phase_name}  (NS_Through = N-S green, EW_Through = E-W green)
- Phase active for: {phase_duration} simulation steps
- Vehicles queued NORTH-SOUTH: {ns_queue}
- Vehicles queued EAST-WEST:   {ew_queue}
- Simulation step: {step}

RULES:
- Minimum green time = 15 steps. Do NOT switch if phase_duration < 15.
- NS_Through and EW_Through can NEVER be simultaneously green.
- Switch only if the waiting queue on the other side is significantly larger (>=2x or >8 vehicles more).
- If queues are similar, KEEP current phase to avoid unnecessary disruption.

Reply with EXACTLY one of these and a brief reason:
KEEP: <reason>
SWITCH_NS: <reason>
SWITCH_EW: <reason>"""

    try:
        resp = _llm_client.chat.completions.create(
            model=_OPENROUTER_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=60, timeout=10,
        )
        reply = resp.choices[0].message.content.strip()
        logger.info(f"[LLM] {reply}")
        word = reply.split(':')[0].strip().upper()
        if word in ('KEEP', 'SWITCH_NS', 'SWITCH_EW'):
            reason = reply.split(':', 1)[1].strip() if ':' in reply else reply
            return word, reason
        logger.warning(f"[LLM] Unrecognised reply '{reply}' — using heuristic")
        return _heuristic(current_phase_name, ns_queue, ew_queue)
    except Exception as exc:
        logger.warning(f"[LLM] Error ({exc}) — using heuristic")
        return _heuristic(current_phase_name, ns_queue, ew_queue)


def _heuristic(phase_name, ns_q, ew_q):
    """Fallback threshold decision when LLM is unavailable."""
    if phase_name in ('NS_Through', 'NS_Left'):
        if ew_q > max(8, 2 * ns_q):
            return 'SWITCH_EW', f'Heuristic: EW({ew_q}) >> NS({ns_q})'
    elif phase_name in ('EW_Through', 'EW_Left'):
        if ns_q > max(8, 2 * ew_q):
            return 'SWITCH_NS', f'Heuristic: NS({ns_q}) >> EW({ew_q})'
    return 'KEEP', f'Heuristic: queues balanced NS={ns_q} EW={ew_q}'


# ── WebSocket live stream ────────────────────────────────────────────────────

class LiveStreamServer:
    def __init__(self, port=WS_PORT):
        self.port = port
        self._frame_queue = queue.Queue(maxsize=5)
        self._clients = set()
        self._thread = threading.Thread(target=self._run, daemon=True, name='ws-server')

    def start(self):
        if not _WS_AVAILABLE:
            logger.warning("[WS] websockets not installed — streaming disabled")
            return
        self._thread.start()
        logger.info(f"[WS] Starting on ws://0.0.0.0:{self.port}")

    def push_frame(self, frame):
        if not _WS_AVAILABLE:
            return
        if self._frame_queue.full():
            try: self._frame_queue.get_nowait()
            except queue.Empty: pass
        try: self._frame_queue.put_nowait(frame)
        except queue.Full: pass

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try: loop.run_until_complete(self._serve())
        except Exception as e: logger.error(f"[WS] Crashed: {e}")

    async def _serve(self):
        async with websockets.serve(self._handler, "0.0.0.0", self.port,
                                    ping_interval=20, ping_timeout=10):
            logger.info(f"[WS] Ready — connect React to ws://localhost:{self.port}")
            await self._broadcast()

    async def _handler(self, ws):
        self._clients.add(ws)
        try:
            async for _ in ws: pass
        except websockets.exceptions.ConnectionClosed: pass
        finally: self._clients.discard(ws)

    async def _broadcast(self):
        while True:
            try: frame = self._frame_queue.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.02); continue
            if not self._clients: continue
            msg = json.dumps(frame)
            dead = set()
            for ws in list(self._clients):
                try: await ws.send(msg)
                except: dead.add(ws)
            self._clients -= dead


# ── CityFlow Bridge ──────────────────────────────────────────────────────────

class CityFlowBridge:
    def __init__(self, config_path, sca_node_address):
        logger.info(f"Loading CityFlow: {config_path}")
        self.eng = cityflow.Engine(config_path, thread_num=1)

        logger.info(f"Connecting to SCA node: {sca_node_address}")
        channel = grpc.insecure_channel(sca_node_address)
        grpc.channel_ready_future(channel).result(timeout=15)
        self.stub = communication_pb2_grpc.NodeCommunicationStub(channel)
        logger.info("gRPC connected.")

        self.step_interval  = float(os.getenv('STEP_INTERVAL', 1.0))
        self.min_green_time = int(os.getenv('MIN_GREEN_TIME', 15))
        self.box_size       = BOX_SIZE

        self.current_phase      = 0   # current CityFlow phase ID
        self.last_change_step   = 0   # step when phase last changed

        # Pending phase change (set during yellow, applied once box clears)
        self._pending_phase      = None
        self._yellow_since_step  = None

        self._last_decision = 'KEEP'
        self._last_reason   = 'Initialising...'

        self.stream = LiveStreamServer(port=WS_PORT)
        self.stream.start()

    # ── Box-clear check ────────────────────────────────────────────────────

    def _box_is_clear(self):
        """
        Returns True when no running vehicle is inside the intersection box.
        Vehicles in the box are those within BOX_SIZE units of (0,0) in both axes.
        This prevents gridlock by ensuring the opposing direction only gets green
        once all conflicting vehicles have cleared the centre.
        """
        try:
            for vid, info in self.eng.get_vehicle_info().items():
                if not info.get('running', 0):
                    continue
                if (abs(float(info.get('x', 999))) < self.box_size and
                        abs(float(info.get('y', 999))) < self.box_size):
                    return False
        except Exception:
            pass
        return True

    # ── Frame builder ─────────────────────────────────────────────────────

    def _build_frame(self, step, ns_queue, ew_queue):
        vehicles = []
        try:
            for vid, info in self.eng.get_vehicle_info().items():
                if not info.get('running', 0):
                    continue
                vehicles.append({
                    'id':    str(vid),
                    'x':     float(info.get('x', 0.0)),
                    'y':     float(info.get('y', 0.0)),
                    'angle': float(info.get('angle', 0.0)),
                    'speed': float(info.get('speed', 0.0)),
                    'length': 5.0, 'width': 2.0,
                    'road':  str(info.get('road', '')),
                })
        except Exception as exc:
            logger.warning(f"get_vehicle_info error at step {step}: {exc}")

        return {
            'step':          step,
            'vehicles':      vehicles,
            'trafficLights': {'intersection_1_1': self.current_phase},
            'phase':         PHASE_ID_TO_NAME.get(self.current_phase, 'Unknown'),
            'metrics': {
                'ns_queue':     ns_queue,
                'ew_queue':     ew_queue,
                'step':         step,
                'llm_decision': self._last_decision,
                'llm_reason':   self._last_reason,
                'box_clear':    self._box_is_clear(),
                'pending_phase': PHASE_ID_TO_NAME.get(self._pending_phase, None)
                                 if self._pending_phase is not None else None,
            },
        }

    # ── Main loop ──────────────────────────────────────────────────────────

    def run_loop(self, total_steps=3600):
        logger.info(f"Starting control loop ({total_steps} steps)...")

        for step in range(total_steps):
            # ── 1. Read queue lengths ───────────────────────────────────
            lane_counts = self.eng.get_lane_vehicle_count()
            ns_queue = sum_lanes(lane_counts, ["road_1_0_1", "road_1_2_3"])
            ew_queue = sum_lanes(lane_counts, ["road_0_1_0", "road_2_1_2"])

            phase_duration = step - self.last_change_step
            current_name   = PHASE_ID_TO_NAME.get(self.current_phase, '?')

            if step % 10 == 0:
                logger.info(
                    f"[{step:>5}] NS={ns_queue:>3} EW={ew_queue:>3}  "
                    f"Phase={current_name} dur={phase_duration}  "
                    f"box={'clear' if self._box_is_clear() else 'OCCUPIED'}"
                )

            # ── 2. If yellow is active, check for box clear ─────────────
            if self._pending_phase is not None:
                yellow_steps = step - self._yellow_since_step
                box_clear    = self._box_is_clear()

                if box_clear or yellow_steps >= MAX_YELLOW_STEPS:
                    # Apply the pending phase now
                    self.eng.set_tl_phase("intersection_1_1", self._pending_phase)
                    logger.info(
                        f"[{step}] ✅ Phase → {PHASE_ID_TO_NAME[self._pending_phase]} "
                        f"(box {'cleared' if box_clear else 'forced after timeout'})"
                    )
                    self.current_phase     = self._pending_phase
                    self.last_change_step  = step
                    self._pending_phase    = None
                    self._yellow_since_step = None
                else:
                    logger.debug(
                        f"[{step}] ⏳ Yellow — waiting for box to clear "
                        f"({yellow_steps}/{MAX_YELLOW_STEPS} steps)"
                    )

            # ── 3. LLM phase decision (every 5 steps, min green met) ────
            elif (phase_duration >= self.min_green_time
                    and step % 5 == 0
                    and self.current_phase in (0, 2, 4, 6)):

                decision, reason = ask_llm_phase(
                    current_name, ns_queue, ew_queue, step, phase_duration
                )
                self._last_decision = decision
                self._last_reason   = reason

                logger.info(f"[{step}] LLM → {decision}: {reason}")

                if decision != 'KEEP':
                    # Map decision to SCA action vector
                    action_map = {
                        'SWITCH_NS': [1,  0, -1, 0, 0, 0, 0],
                        'SWITCH_EW': [-1, 0,  1, 0, 0, 0, 0],
                    }
                    action_vector = action_map[decision]
                    action_label  = (
                        f"LLM {decision} | NS={ns_queue} EW={ew_queue} | {reason}"
                    )

                    req = communication_pb2.ActionRequest(
                        node_id="bridge",
                        action_type=action_label,
                        action_vector=action_vector,
                        source_room="Main & 1st St",
                        target_room="Main & 1st St",
                        timestamp=int(time.time()),
                    )
                    try:
                        resp = self.stub.ProposeAction(req, timeout=5)
                        if resp.success:
                            target_phase = sca_to_cityflow_phase(list(resp.resulting_state))
                            if target_phase != -1:
                                # Step 1: Set yellow immediately
                                self.eng.set_tl_phase("intersection_1_1", YELLOW_PHASE_ID)
                                self.current_phase = YELLOW_PHASE_ID
                                logger.info(
                                    f"[{step}] 🟡 Yellow → waiting for box clear "
                                    f"before switching to {PHASE_ID_TO_NAME[target_phase]}"
                                )
                                # Step 2: Store pending — applied once box clears
                                self._pending_phase     = target_phase
                                self._yellow_since_step = step
                        else:
                            logger.warning(f"[{step}] ❌ Blocked: {resp.message}")
                            self._last_reason = f"BLOCKED: {resp.message}"
                    except Exception as exc:
                        logger.warning(f"[{step}] gRPC error: {exc}")

            # ── 4. Advance simulation ───────────────────────────────────
            self.eng.next_step()

            # ── 5. Broadcast live frame to React ────────────────────────
            self.stream.push_frame(self._build_frame(step, ns_queue, ew_queue))

            time.sleep(self.step_interval)

        logger.info("[Bridge] Simulation complete.")


if __name__ == "__main__":
    boot_delay = int(os.getenv('BOOT_DELAY', 10))
    if boot_delay > 0:
        logger.info(f"Waiting {boot_delay}s for SCA nodes...")
        time.sleep(boot_delay)

    bridge = CityFlowBridge(
        config_path=os.getenv('CITYFLOW_CONFIG', '/app/cityflow_config.json'),
        sca_node_address=os.getenv('SCA_ADDRESS', 'intersection_a:50051'),
    )
    bridge.run_loop(total_steps=int(os.getenv('TOTAL_STEPS', 3600)))