"""
Real-Time Traffic Control Dashboard
====================================
Streamlit dashboard for the Multi-Agent Causal Dependency Traffic System.

Displays:
  - Live signal phase state for all 3 intersections
  - MiniMax LLM decision log (APPROVE / REJECT with reasons)
  - Raft consensus status (leader / follower / term)
  - CityFlow queue metrics (NS vs EW vehicle counts) from bridge logs

Run:
    streamlit run dashboard.py
"""

import time
import grpc
import sys
import os
import subprocess
import json
from datetime import datetime
import streamlit as st

# ── Path setup ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src', 'proto'))
import communication_pb2
import communication_pb2_grpc

# ───────────────────────────────────────────────────────────
#  Config
# ───────────────────────────────────────────────────────────

NODES = {
    "Main & 1st St": "localhost:50051",
    "Main & 2nd St": "localhost:50052",
    "Main & 3rd St": "localhost:50053",
}

STATE_LABELS = [
    "NS_Through", "NS_Left", "EW_Through", "EW_Left",
    "Emergency", "Pedestrian_NS", "Pedestrian_EW",
]

PHASE_COLORS = {
    "NS_Through":    "#22c55e",   # green
    "NS_Left":       "#86efac",   # light green
    "EW_Through":    "#3b82f6",   # blue
    "EW_Left":       "#93c5fd",   # light blue
    "Emergency":     "#ef4444",   # red
    "Pedestrian_NS": "#f59e0b",   # amber
    "Pedestrian_EW": "#fbbf24",   # yellow
}

RAFT_COLORS = {
    "LEADER":    "#22c55e",
    "FOLLOWER":  "#94a3b8",
    "CANDIDATE": "#f59e0b",
    "UNKNOWN":   "#6b7280",
}

# ───────────────────────────────────────────────────────────
#  Page config
# ───────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Traffic Control Dashboard",
    page_icon="🚦",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Inject custom CSS ──
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

.stApp {
    background: linear-gradient(135deg, #0f172a 0%, #1e293b 50%, #0f172a 100%);
    min-height: 100vh;
}

.dash-header {
    text-align: center;
    padding: 2rem 0 1.5rem;
}

.dash-title {
    font-size: 2.4rem;
    font-weight: 700;
    background: linear-gradient(90deg, #22d3ee, #818cf8, #c084fc);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    margin: 0;
}

.dash-subtitle {
    color: #94a3b8;
    font-size: 0.95rem;
    margin-top: 0.4rem;
}

.node-card {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 16px;
    padding: 1.4rem;
    margin-bottom: 1rem;
    backdrop-filter: blur(12px);
}

.node-name {
    font-size: 1.1rem;
    font-weight: 600;
    color: #e2e8f0;
    margin-bottom: 0.8rem;
}

.phase-pill {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 0.75rem;
    font-weight: 600;
    margin: 2px;
    letter-spacing: 0.02em;
}

.phase-on {
    color: #0f172a;
}

.phase-off {
    background: rgba(255,255,255,0.06);
    color: #475569;
    border: 1px solid rgba(255,255,255,0.07);
}

.raft-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: 0.05em;
    text-transform: uppercase;
}

.metric-box {
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.08);
    border-radius: 12px;
    padding: 1rem;
    text-align: center;
}

.metric-val {
    font-size: 2rem;
    font-weight: 700;
    color: #e2e8f0;
}

.metric-label {
    font-size: 0.78rem;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    margin-top: 4px;
}

.llm-entry {
    padding: 0.7rem 1rem;
    border-radius: 10px;
    margin-bottom: 0.5rem;
    border-left: 4px solid;
    font-size: 0.83rem;
    line-height: 1.5;
    color: #cbd5e1;
}

.llm-approve {
    background: rgba(34,197,94,0.08);
    border-color: #22c55e;
}

.llm-reject {
    background: rgba(239,68,68,0.08);
    border-color: #ef4444;
}

.llm-neutral {
    background: rgba(148,163,184,0.06);
    border-color: #475569;
}

.section-label {
    font-size: 0.7rem;
    font-weight: 600;
    color: #64748b;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 0.6rem;
}

.offline-badge {
    color: #ef4444;
    font-size: 0.85rem;
    font-style: italic;
}

.divider {
    border: none;
    border-top: 1px solid rgba(255,255,255,0.06);
    margin: 1rem 0;
}

.status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    display: inline-block;
    margin-right: 6px;
}
</style>
""", unsafe_allow_html=True)


# ───────────────────────────────────────────────────────────
#  Data fetching
# ───────────────────────────────────────────────────────────

@st.cache_resource
def get_stub(address):
    """Create (and cache) a gRPC stub for a node address."""
    channel = grpc.insecure_channel(address)
    return communication_pb2_grpc.NodeCommunicationStub(channel)


def fetch_node_status(name, address):
    """Poll a node for its status. Returns dict or None on failure."""
    try:
        stub = get_stub(address)
        resp = stub.GetNodeStatus(
            communication_pb2.NodeStatusRequest(requester="dashboard"),
            timeout=3.0,
        )
        return {
            "name":        name,
            "node_id":     resp.node_id,
            "room_name":   resp.room_name,
            "state":       list(resp.state_vector),
            "labels":      list(resp.state_labels) or STATE_LABELS,
            "raft_state":  resp.raft_state,
            "raft_term":   resp.raft_term,
            "leader_id":   resp.leader_id,
            "log_length":  resp.log_length,
            "llm":         resp.last_llm_decision,
            "ts":          resp.timestamp,
            "online":      True,
        }
    except Exception as exc:
        return {
            "name":    name,
            "online":  False,
            "error":   str(exc)[:100],
        }


def fetch_cityflow_metrics():
    """
    Parse CityFlow queue metrics from the bridge container logs.
    Returns list of (step, ns_queue, ew_queue) tuples.
    """
    try:
        result = subprocess.run(
            ["docker", "logs",
             "multi_agent-causal-dependency-maintenance-main-cityflow_bridge-1",
             "--tail", "60"],
            capture_output=True, text=True, timeout=4,
        )
        lines = result.stdout.strip().splitlines()
        metrics = []
        for line in lines:
            # Format: [step] NS Queue: X, EW Queue: Y
            if "NS Queue:" in line and "EW Queue:" in line:
                try:
                    step = int(line.split("]")[0].lstrip("[").strip())
                    ns   = int(line.split("NS Queue:")[1].split(",")[0].strip())
                    ew   = int(line.split("EW Queue:")[1].strip())
                    metrics.append((step, ns, ew))
                except Exception:
                    pass
        return metrics[-20:] if metrics else []
    except Exception:
        return []


# ───────────────────────────────────────────────────────────
#  Render helpers
# ───────────────────────────────────────────────────────────

def render_phase_pills(state_vec, labels):
    pills = ""
    for i, (val, label) in enumerate(zip(state_vec, labels)):
        active = val > 0
        color  = PHASE_COLORS.get(label, "#94a3b8")
        if active:
            pills += (
                f'<span class="phase-pill phase-on" '
                f'style="background:{color};">{label}</span>'
            )
        else:
            pills += f'<span class="phase-pill phase-off">{label}</span>'
    return pills


def render_raft_badge(raft_state):
    color = RAFT_COLORS.get(raft_state, "#6b7280")
    return (
        f'<span class="raft-badge" style="background:{color}22;color:{color};'
        f'border:1px solid {color}55;">{raft_state}</span>'
    )


def render_llm_entry(text):
    if text.startswith("APPROVE"):
        css = "llm-approve"
        icon = "✅"
    elif text.startswith("REJECT"):
        css = "llm-reject"
        icon = "❌"
    else:
        css = "llm-neutral"
        icon = "💭"
    return f'<div class="llm-entry {css}">{icon}&nbsp; {text}</div>'


# ───────────────────────────────────────────────────────────
#  Main Dashboard Layout
# ───────────────────────────────────────────────────────────

st.markdown("""
<div class="dash-header">
  <p class="dash-title">🚦 Traffic Control Dashboard</p>
  <p class="dash-subtitle">
    Multi-Agent Causal Dependency System &nbsp;·&nbsp;
    MiniMax LLM + Raft Consensus + CityFlow Engine
  </p>
</div>
""", unsafe_allow_html=True)

# Auto-refresh
refresh = st.sidebar.slider("Auto-refresh (seconds)", 2, 30, 5)
st.sidebar.markdown("---")
st.sidebar.markdown("**Nodes**")
for n, addr in NODES.items():
    st.sidebar.markdown(f"`{n}` → `{addr}`")
st.sidebar.markdown("---")
st.sidebar.caption("Dashboard auto-refreshes every few seconds using Streamlit's `st.rerun()`.")

placeholder = st.empty()

while True:
    # ── Fetch all node statuses ──
    all_status = {n: fetch_node_status(n, addr) for n, addr in NODES.items()}
    cityflow   = fetch_cityflow_metrics()

    with placeholder.container():

        # ── Top metrics row ──
        m1, m2, m3, m4, m5 = st.columns(5)
        online_count = sum(1 for s in all_status.values() if s.get("online"))
        leaders      = [s for s in all_status.values() if s.get("raft_state") == "LEADER"]
        leader_name  = leaders[0]["room_name"] if leaders else "—"
        cf_latest    = cityflow[-1] if cityflow else None

        with m1:
            st.markdown(f"""
            <div class="metric-box">
              <div class="metric-val">{online_count}/3</div>
              <div class="metric-label">Nodes Online</div>
            </div>""", unsafe_allow_html=True)
        with m2:
            st.markdown(f"""
            <div class="metric-box">
              <div class="metric-val" style="font-size:1.1rem;">{leader_name}</div>
              <div class="metric-label">Raft Leader</div>
            </div>""", unsafe_allow_html=True)
        with m3:
            ns_q = cf_latest[1] if cf_latest else "—"
            st.markdown(f"""
            <div class="metric-box">
              <div class="metric-val" style="color:#22c55e;">{ns_q}</div>
              <div class="metric-label">NS Queue</div>
            </div>""", unsafe_allow_html=True)
        with m4:
            ew_q = cf_latest[2] if cf_latest else "—"
            st.markdown(f"""
            <div class="metric-box">
              <div class="metric-val" style="color:#3b82f6;">{ew_q}</div>
              <div class="metric-label">EW Queue</div>
            </div>""", unsafe_allow_html=True)
        with m5:
            cf_step = cf_latest[0] if cf_latest else "—"
            st.markdown(f"""
            <div class="metric-box">
              <div class="metric-val">{cf_step}</div>
              <div class="metric-label">CityFlow Step</div>
            </div>""", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # ── Intersection cards + LLM log ──
        left_col, right_col = st.columns([2, 1], gap="large")

        with left_col:
            st.markdown('<div class="section-label">🚦 Intersection Signal States</div>',
                        unsafe_allow_html=True)

            for name, status in all_status.items():
                if not status.get("online"):
                    st.markdown(f"""
                    <div class="node-card">
                      <div class="node-name">
                        <span class="status-dot" style="background:#ef4444;"></span>{name}
                      </div>
                      <span class="offline-badge">⚠ Offline — {status.get('error', 'unreachable')}</span>
                    </div>""", unsafe_allow_html=True)
                    continue

                state  = status.get("state", [0]*7)
                labels = status.get("labels", STATE_LABELS)
                raft   = status.get("raft_state", "UNKNOWN")
                term   = status.get("raft_term", 0)
                log_n  = status.get("log_length", 0)
                pills  = render_phase_pills(state, labels)
                badge  = render_raft_badge(raft)

                active_phases = [labels[i] for i, v in enumerate(state) if v > 0]
                dot_color = "#22c55e" if active_phases else "#f59e0b"

                st.markdown(f"""
                <div class="node-card">
                  <div class="node-name">
                    <span class="status-dot" style="background:{dot_color};box-shadow:0 0 6px {dot_color};"></span>
                    {name}
                    &nbsp;&nbsp;{badge}
                    <span style="color:#475569;font-size:0.75rem;margin-left:8px;">
                      term {term} · {log_n} entries
                    </span>
                  </div>
                  <div style="margin-top:0.5rem;">{pills}</div>
                </div>""", unsafe_allow_html=True)

        with right_col:
            st.markdown('<div class="section-label">🤖 MiniMax LLM Decisions</div>',
                        unsafe_allow_html=True)

            all_decisions = []
            for name, status in all_status.items():
                if status.get("online") and status.get("llm"):
                    short_name = name.split("&")[-1].strip()
                    all_decisions.append(
                        f"[{short_name}] {status['llm']}"
                    )

            if all_decisions:
                for decision in all_decisions:
                    st.markdown(render_llm_entry(decision), unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div class="llm-neutral llm-entry">💭 No LLM decisions yet — '
                    'run simulate_traffic.py to trigger actions.</div>',
                    unsafe_allow_html=True
                )

        # ── CityFlow chart ──
        if cityflow:
            st.markdown("<hr class='divider'>", unsafe_allow_html=True)
            st.markdown('<div class="section-label">🚗 CityFlow Vehicle Queue (last 20 steps)</div>',
                        unsafe_allow_html=True)

            steps = [c[0] for c in cityflow]
            ns_q  = [c[1] for c in cityflow]
            ew_q  = [c[2] for c in cityflow]

            import pandas as pd
            df = pd.DataFrame({"Step": steps, "NS Queue": ns_q, "EW Queue": ew_q})
            df = df.set_index("Step")
            st.line_chart(df, color=["#22c55e", "#3b82f6"], height=200)

        # ── Footer ──
        ts = datetime.now().strftime("%H:%M:%S")
        st.markdown(
            f'<div style="text-align:right;color:#334155;font-size:0.72rem;margin-top:1rem;">'
            f'Last updated: {ts}</div>',
            unsafe_allow_html=True
        )

    time.sleep(refresh)
    st.rerun()
