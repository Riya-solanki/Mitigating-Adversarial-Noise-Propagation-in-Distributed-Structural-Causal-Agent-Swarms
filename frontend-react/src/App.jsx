import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useCityFlowData } from './hooks/useCityFlowData';
import useMultiTraffic from './hooks/useMultiTraffic';
import useLiveTraffic from './hooks/useLiveTraffic';
import CityFlowMap from './components/CityFlowMap';
import Dashboard from './components/Dashboard';
import { Radio, WifiOff } from 'lucide-react';

// ── Phase sequence (same for all nodes; Raft propagates it) ───────────────
const PHASE_SEQ = [
  { phase: 'NS_Through', duration: 12 },
  { phase: 'Yellow', duration: 3 },
  { phase: 'EW_Through', duration: 12 },
  { phase: 'Yellow', duration: 3 },
];

// Raft propagation delays — how long after the leader before each follower adopts
const PROPAGATION_MS = { A: 0, B: 500, C: 800, D: 1100 };
const NODE_NAMES = { A: 'Main & 1st', B: 'Main & 2nd', C: 'Main & 3rd', D: 'Main & 4th' };

// Simulated LLM decision based on phase
const LLM_DECISIONS = {
  NS_Through: (ns, ew) =>
    `SWITCH_NS: NS queue (${ns}) justifies N-S green — clearing ${ns} vehicles`,
  EW_Through: (ns, ew) =>
    `SWITCH_EW: EW queue (${ew}) justifies E-W green — clearing ${ew} vehicles`,
  Yellow: () => 'YELLOW: Intersection box clearing before next green phase',
  Emergency: () => 'EMERGENCY: All signals RED — emergency vehicle preemption active',
};

function ts() {
  return new Date().toLocaleTimeString('en', { hour12: false });
}

export default function App() {
  // ── Backend (optional) ────────────────────────────────────────────────
  const { roadnet, stats, totalFrames, loading: backendLoading,
    error: backendError, API_BASE } = useCityFlowData();

  // ── Live bridge ───────────────────────────────────────────────────────
  const { connected: liveConnected, vehicles: liveVehicles,
    phase: livePhase, metrics: liveMetrics,
    step: liveStep, error: wsError } = useLiveTraffic();

  // ── Phase state (one per intersection) ───────────────────────────────
  const [phases, setPhases] = useState({ A: 'NS_Through', B: 'NS_Through', C: 'NS_Through', D: 'NS_Through' });
  const [seqIdx, setSeqIdx] = useState(0);
  const [phaseTimer, setPhaseTimer] = useState(0);
  const [demoMode, setDemoMode] = useState('normal');

  // ── Raft state ────────────────────────────────────────────────────────
  const [raftState, setRaftState] = useState({
    leader: 'A', term: 1,
    nodes: {
      A: { state: 'leader', term: 1, logLen: 1 },
      B: { state: 'follower', term: 1, logLen: 0 },
      C: { state: 'follower', term: 1, logLen: 0 },
      D: { state: 'follower', term: 1, logLen: 0 },
    },
  });
  const [raftEvents, setRaftEvents] = useState([
    { t: ts(), msg: '🟢 Cluster started — A elected leader (term=1)' },
    { t: ts(), msg: '💓 A → B,C,D: Heartbeat (term=1)' },
  ]);

  const timeoutsRef = useRef([]);
  const logEntryRef = useRef(1);
  const demoModeRef = useRef(demoMode);
  const seqIdxRef = useRef(seqIdx);
  useEffect(() => { demoModeRef.current = demoMode; }, [demoMode]);
  useEffect(() => { seqIdxRef.current = seqIdx; }, [seqIdx]);

  function addEvent(msg) {
    setRaftEvents(prev => [{ t: ts(), msg }, ...prev].slice(0, 40));
  }

  // ── Commit a phase change through Raft ────────────────────────────────
  const commitPhase = useCallback((newPhase) => {
    timeoutsRef.current.forEach(clearTimeout);
    timeoutsRef.current = [];

    const entry = ++logEntryRef.current;
    // Count real vehicles per axis from the live simulation
    const _vs = vehiclesRef.current;
    const nsKeys = ['DL', 'UL', 'DR', 'UR'];
    const ewKeys = ['RT', 'LT', 'RB', 'LB'];
    const ns = _vs.filter(v => nsKeys.includes(v.laneKey)).length;
    const ew = _vs.filter(v => ewKeys.includes(v.laneKey)).length;
    const llmFn = LLM_DECISIONS[newPhase];
    if (llmFn) addEvent(`🤖 LLM → ${llmFn(ns, ew)}`);

    addEvent(`⭐ A proposes phase=${newPhase} (term=${raftState.term}, entry=${entry})`);

    // Leader adopts immediately
    setPhases(p => ({ ...p, A: newPhase }));
    setRaftState(prev => ({
      ...prev,
      nodes: {
        ...prev.nodes,
        A: { ...prev.nodes.A, logLen: entry }
      }
    }));

    // Followers adopt with staggered delays (simulates AppendEntries RPC)
    ['B', 'C', 'D'].forEach((nodeId, i) => {
      const delay = PROPAGATION_MS[nodeId];
      const tid = setTimeout(() => {
        setPhases(p => ({ ...p, [nodeId]: newPhase }));
        setRaftState(prev => ({
          ...prev,
          nodes: {
            ...prev.nodes,
            [nodeId]: { ...prev.nodes[nodeId], logLen: entry }
          }
        }));
        addEvent(`✓ ${nodeId} ack entry=${entry} → adopted ${newPhase}`);
        if (i === 2) addEvent(`🔒 Committed entry=${entry} (quorum 4/4)`);
      }, delay);
      timeoutsRef.current.push(tid);
    });
  }, [raftState.term]);

  // ── Periodic heartbeat events ─────────────────────────────────────────
  useEffect(() => {
    const id = setInterval(() => {
      addEvent(`💓 A → B,C,D: Heartbeat (term=${raftState.term})`);
    }, 8000);
    return () => clearInterval(id);
  }, [raftState.term]);

  // ── Phase timer (demo mode) ───────────────────────────────────────────
  useEffect(() => {
    if (liveConnected) return;

    const id = setInterval(() => {
      setPhaseTimer(prev => {
        const mode = demoModeRef.current;

        if (mode === 'emergency') {
          const next = prev + 1;
          if (next >= 10) {
            setDemoMode('normal');
            const newPhase = PHASE_SEQ[0].phase;
            setSeqIdx(0);
            commitPhase(newPhase);
            return 0;
          }
          return next;
        }

        const cur = PHASE_SEQ[seqIdxRef.current];
        const next = prev + 1;
        if (next >= cur.duration) {
          const nextIdx = (seqIdxRef.current + 1) % PHASE_SEQ.length;
          const nextPhase = PHASE_SEQ[nextIdx].phase;
          setSeqIdx(nextIdx);
          commitPhase(nextPhase);
          return 0;
        }
        return next;
      });
    }, 1000);

    return () => clearInterval(id);
  }, [liveConnected, commitPhase]);

  // ── Sync live phase to leader node ────────────────────────────────────
  useEffect(() => {
    if (!liveConnected || !livePhase) return;
    if (livePhase !== 'Unknown' && livePhase !== 'Yellow') {
      commitPhase(livePhase);
    }
  }, [livePhase, liveConnected]);

  // ── Log bridge connect ────────────────────────────────────────────────
  useEffect(() => {
    if (liveConnected) addEvent('🟢 Python bridge connected — switching to LIVE data');
  }, [liveConnected]);

  // ── Manual triggers ───────────────────────────────────────────────────
  const triggerNormal = useCallback(() => {
    setDemoMode('normal');
    setSeqIdx(0);
    setPhaseTimer(0);
    commitPhase(PHASE_SEQ[0].phase);
  }, [commitPhase]);

  const triggerEmergency = useCallback(() => {
    setDemoMode('emergency');
    setPhaseTimer(0);
    timeoutsRef.current.forEach(clearTimeout);
    timeoutsRef.current = [];
    setPhases({ A: 'Emergency', B: 'Emergency', C: 'Emergency', D: 'Emergency' });
    addEvent('🚑 EMERGENCY: All signals RED');
  }, []);

  // ── Vehicles ──────────────────────────────────────────────────────────
  const demoVehicles = useMultiTraffic(phases);
  const displayVehicles = liveConnected ? liveVehicles : demoVehicles;

  // Track real vehicle counts per axis so LLM messages reference actual queue sizes
  const vehiclesRef = useRef([]);
  useEffect(() => { vehiclesRef.current = demoVehicles; }, [demoVehicles]);

  // ── Derive dashboard props from leader phase ──────────────────────────
  const leaderPhase = phases.A;
  const isYellow = leaderPhase === 'Yellow';
  const currentSeq = PHASE_SEQ[seqIdx];
  const dashPhase = demoMode === 'emergency' ? 'Emergency'
    : isYellow ? PHASE_SEQ[(seqIdx + 1) % PHASE_SEQ.length].phase
      : leaderPhase;

  // Cleanup on unmount
  useEffect(() => () => timeoutsRef.current.forEach(clearTimeout), []);

  return (
    <div className="flex h-screen w-screen overflow-hidden text-gray-100 flex-col">

      {/* ── Top Bar ── */}
      <header className="h-12 border-b border-slate-700/50 bg-city-surface/90 backdrop-blur-md flex items-center justify-between px-4 z-20 shrink-0">
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 rounded-full bg-gradient-to-tr from-city-accent to-city-accent-alt shadow-[0_0_10px_rgba(34,211,238,0.5)]" />
          <span className="font-semibold text-sm tracking-wide">CityFlow Visualizer</span>
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-700 text-emerald-400 ml-1">4-Node Cluster</span>
          {(backendLoading || backendError) && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-slate-700 text-gray-400">
              {backendLoading ? 'connecting…' : 'backend offline'}
            </span>
          )}
        </div>

        {/* Centre */}
        <div className={`absolute left-1/2 -translate-x-1/2 font-mono text-sm px-4 py-1 rounded-full border ${isYellow
          ? 'text-yellow-400 bg-yellow-400/10 border-yellow-400/20'
          : 'text-city-accent bg-city-accent/10 border-city-accent/20'
          }`}>
          {isYellow ? '🟡 Clearing…' : leaderPhase.replace(/_/g, ' ')}
          {' — '}
          {liveConnected ? `step ${liveStep}` : `${phaseTimer}s / ${currentSeq?.duration ?? 12}s`}
        </div>

        {/* Right */}
        <div className="flex items-center gap-2">
          {liveConnected && liveMetrics && (
            <span className="font-mono text-[10px] text-gray-400 bg-slate-800/80 px-2 py-1 rounded-full">
              NS:{liveMetrics.ns_queue} EW:{liveMetrics.ew_queue}
            </span>
          )}
          <span className="font-mono text-xs text-gray-400 bg-slate-800/80 px-3 py-1 rounded-full">
            🚗 {displayVehicles.length}
          </span>
          {liveConnected ? (
            <span className="font-mono text-xs px-3 py-1 rounded-full bg-cyan-500/20 text-cyan-300 flex items-center gap-1.5">
              <Radio size={11} className="animate-pulse" /> LIVE
            </span>
          ) : (
            <span className={`font-mono text-xs px-3 py-1 rounded-full ${demoMode === 'emergency' ? 'bg-rose-500/20 text-rose-400 animate-pulse'
              : isYellow ? 'bg-yellow-500/20 text-yellow-400'
                : 'bg-emerald-500/20 text-emerald-400'
              }`}>
              {demoMode === 'emergency' ? '🚑 EMERGENCY' : isYellow ? '🟡 YELLOW' : '🟢 DEMO'}
            </span>
          )}
          {!liveConnected && wsError && <WifiOff size={12} className="text-yellow-500" />}
        </div>
      </header>

      {/* ── Main layout ── */}
      <div className="flex flex-1 overflow-hidden relative">
        <main className="flex-1 relative">
          <CityFlowMap
            roadnet={roadnet}
            vehicles={displayVehicles}
            phases={phases}
          />
        </main>

        <aside className="w-[340px] flex shrink-0 bg-city-surface/95 border-l border-slate-700/50 shadow-xl z-20">
          <Dashboard
            stats={stats}
            totalFrames={totalFrames}
            currentStep={0}
            vehicles={displayVehicles}
            trafficLights={{}}
            logs={[]}
            playing={false}
            setPlaying={() => { }}
            fps={10}
            setFps={() => { }}
            setCurrentStep={() => { }}
            demoMode={demoMode}
            demoPhase={dashPhase}
            demoTimer={liveConnected ? liveStep % 12 : phaseTimer}
            triggerNormal={triggerNormal}
            triggerEmergency={triggerEmergency}
            isLive={liveConnected}
            liveMetrics={liveConnected ? liveMetrics : {
              ns_queue: vehiclesRef.current.filter(v => ['DL', 'UL', 'DR', 'UR'].includes(v.laneKey)).length,
              ew_queue: vehiclesRef.current.filter(v => ['RT', 'LT', 'RB', 'LB'].includes(v.laneKey)).length,
              llm_decision: undefined,
              llm_reason: undefined,
            }}
            phases={phases}
            raftState={raftState}
            raftEvents={raftEvents}
          />
        </aside>
      </div>
    </div>
  );
}