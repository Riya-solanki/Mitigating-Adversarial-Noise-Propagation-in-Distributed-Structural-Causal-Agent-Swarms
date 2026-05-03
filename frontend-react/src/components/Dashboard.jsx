import React, { useState } from 'react';
import { Play, Pause, SkipBack, SkipForward, FastForward, Rewind } from 'lucide-react';

const NODE_NAMES = { A:'Main & 1st', B:'Main & 2nd', C:'Main & 3rd', D:'Main & 4th' };
const PHASE_DURATION = 12;

export default function Dashboard({
  stats, totalFrames, currentStep, vehicles, trafficLights,
  logs, playing, setPlaying, fps, setFps, setCurrentStep,
  demoMode, demoPhase, demoTimer, triggerNormal, triggerEmergency,
  isLive = false, liveMetrics = null,
  phases = {}, raftState = null, raftEvents = [],
}) {
  const [activeTab, setActiveTab] = useState('lights');

  // ── Derived signal state ───────────────────────────────────────────────
  const nsColor   = demoPhase === 'NS_Through' ? 'bg-emerald-400' : 'bg-rose-500';
  const ewColor   = demoPhase === 'EW_Through' ? 'bg-emerald-400' : 'bg-rose-500';
  const emColor   = demoPhase === 'Emergency'  ? 'bg-rose-500 animate-pulse' : 'bg-slate-700';
  const duration  = demoTimer;
  const isYellow  = demoPhase === 'Yellow';

  const agentDecision = isLive && liveMetrics?.llm_decision
    ? liveMetrics.llm_decision
    : demoMode === 'emergency' ? 'EMERGENCY'
    : isYellow ? 'YELLOW'
    : duration >= PHASE_DURATION - 2 ? `SWITCH_${demoPhase === 'NS_Through' ? 'EW' : 'NS'}`
    : 'KEEP_CURRENT';

  const agentReason = isLive && liveMetrics?.llm_reason
    ? liveMetrics.llm_reason
    : demoMode === 'emergency' ? '🚑 All signals RED — emergency preemption'
    : isYellow ? 'Clearing intersection box before next green phase'
    : duration >= PHASE_DURATION - 2 ? 'Approaching phase limit — preparing to switch'
    : 'Min green time active — maintaining current phase';

  const ns_q = isLive && liveMetrics ? liveMetrics.ns_queue : Math.max(0, 30 - duration);
  const ew_q = isLive && liveMetrics ? liveMetrics.ew_queue : 30 + duration;

  // ── Phase badge color ──────────────────────────────────────────────────
  function phaseBadge(phase) {
    if (phase === 'NS_Through') return 'bg-emerald-500/20 text-emerald-300 border-emerald-500/30';
    if (phase === 'EW_Through') return 'bg-blue-500/20 text-blue-300 border-blue-500/30';
    if (phase === 'Yellow')     return 'bg-yellow-500/20 text-yellow-300 border-yellow-500/30';
    if (phase === 'Emergency')  return 'bg-rose-500/20 text-rose-300 border-rose-500/30 animate-pulse';
    return 'bg-slate-700/50 text-gray-400 border-slate-600/30';
  }

  return (
    <div className="flex flex-col h-full w-full">

      {/* Tabs */}
      <div className="flex border-b border-slate-700/50 bg-city-surface/50">
        {['lights', 'raft', 'stats', 'logs'].map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)}
            className={`flex-1 py-3 text-[10px] font-medium uppercase tracking-wider transition-colors border-b-2 ${
              activeTab === tab
                ? 'border-city-accent text-city-accent'
                : 'border-transparent text-gray-400 hover:text-gray-200'
            }`}>
            {tab}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-4 scrollbar-hide">

        {/* ══ LIGHTS TAB ══════════════════════════════════════════════════ */}
        {activeTab === 'lights' && (
          <div className="space-y-4">
            <div className="flex gap-2">
              <button onClick={triggerNormal}
                className={`flex-1 p-2.5 rounded-lg text-[10px] uppercase tracking-wider font-bold transition-all ${
                  demoMode === 'normal' && !isYellow
                    ? 'bg-emerald-500 text-white shadow-lg'
                    : 'bg-slate-800 text-gray-400 border border-slate-700/50 hover:bg-slate-700'
                }`}>▶ Normal Cycle</button>
              <button onClick={triggerEmergency}
                className={`flex-1 p-2.5 rounded-lg text-[10px] uppercase tracking-wider font-bold transition-all ${
                  demoMode === 'emergency'
                    ? 'bg-rose-500 text-white shadow-lg animate-pulse'
                    : 'bg-slate-800 text-gray-400 border border-slate-700/50 hover:bg-slate-700'
                }`}>🚑 Emergency</button>
            </div>

            {/* Leader signal state */}
            <div className="bg-slate-800/60 rounded-xl p-4 border border-slate-700/50">
              <h3 className="text-[10px] font-semibold text-gray-400 uppercase tracking-widest mb-3">
                Leader Signal State <span className="text-emerald-400">(Node A)</span>
              </h3>
              <div className="grid grid-cols-3 gap-3 mb-4">
                {[['N-S', nsColor], ['E-W', ewColor], ['Emerg', emColor]].map(([label, cls]) => (
                  <div key={label} className="flex flex-col items-center gap-1">
                    <div className={`w-8 h-8 rounded-full ${cls} transition-colors duration-500 shadow-lg`} />
                    <span className="text-[9px] text-gray-400 uppercase">{label}</span>
                  </div>
                ))}
              </div>
              <div className="mb-3">
                <div className="flex justify-between text-[9px] text-gray-500 mb-1">
                  <span>Phase Timer</span>
                  <span className="font-mono text-gray-300">{duration}s / {PHASE_DURATION}s</span>
                </div>
                <div className="w-full bg-slate-900 rounded-full h-2 overflow-hidden">
                  <div className={`h-full rounded-full transition-all duration-1000 ${
                    isYellow ? 'bg-yellow-400' : demoMode === 'emergency' ? 'bg-rose-500' : 'bg-city-accent'
                  }`} style={{ width: `${Math.min((duration / PHASE_DURATION) * 100, 100)}%` }} />
                </div>
              </div>
            </div>

            {/* All 4 intersection phases */}
            <div className="bg-slate-800/60 rounded-xl p-3 border border-slate-700/50">
              <h3 className="text-[10px] font-semibold text-gray-400 uppercase tracking-widest mb-2">
                All Intersections
              </h3>
              <div className="grid grid-cols-2 gap-2">
                {['A','B','C','D'].map(id => (
                  <div key={id} className="bg-slate-900/60 rounded-lg p-2">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[10px] font-bold text-gray-300">
                        {id} {raftState?.nodes[id]?.state === 'leader' ? '⭐' : ''}
                      </span>
                      <span className={`text-[9px] px-1.5 py-0.5 rounded border ${phaseBadge(phases[id])}`}>
                        {(phases[id] || '—').replace(/_/g, ' ')}
                      </span>
                    </div>
                    <div className="text-[9px] text-gray-500 truncate">{NODE_NAMES[id]}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* AI Agent */}
            <div className="bg-slate-800/60 rounded-xl p-3 border border-slate-700/50">
              <h3 className="text-[10px] font-semibold text-emerald-400 uppercase tracking-widest mb-3 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                AI Agent Coordinator
                {isLive && <span className="ml-auto text-[9px] font-bold px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-300">LIVE</span>}
              </h3>
              <div className="grid grid-cols-2 gap-2 mb-3">
                <div className="bg-slate-900/50 p-2 rounded">
                  <div className="text-[9px] text-gray-500 uppercase">Vehicles</div>
                  <div className="font-mono text-sm text-gray-200">{vehicles.length}</div>
                </div>
                <div className="bg-slate-900/50 p-2 rounded">
                  <div className="text-[9px] text-gray-500 uppercase">Leader Phase</div>
                  <div className="font-mono text-[10px] text-emerald-300 truncate">{demoPhase.replace(/_/g,' ')}</div>
                </div>
                <div className="bg-slate-900/50 p-2 rounded">
                  <div className="text-[9px] text-gray-500 uppercase">NS Queue</div>
                  <div className="font-mono text-[10px] text-gray-300">Q: {ns_q}</div>
                </div>
                <div className="bg-slate-900/50 p-2 rounded">
                  <div className="text-[9px] text-gray-500 uppercase">EW Queue</div>
                  <div className="font-mono text-[10px] text-gray-300">Q: {ew_q}</div>
                </div>
              </div>
              <div className={`p-2 rounded border ${
                agentDecision === 'EMERGENCY'     ? 'bg-rose-900/20 border-rose-800/30'
                : agentDecision === 'YELLOW'      ? 'bg-yellow-900/20 border-yellow-800/30'
                : agentDecision.startsWith('SWITCH') ? 'bg-amber-900/20 border-amber-800/30'
                :                                    'bg-blue-900/20 border-blue-800/30'
              }`}>
                <div className="flex justify-between items-baseline mb-1">
                  <span className="text-[9px] text-blue-400 uppercase tracking-wider">Decision</span>
                  <span className="font-mono text-xs font-bold text-white">{agentDecision}</span>
                </div>
                <div className="text-[10px] text-gray-400 italic">"{agentReason}"</div>
              </div>
            </div>
          </div>
        )}

        {/* ══ RAFT TAB ════════════════════════════════════════════════════ */}
        {activeTab === 'raft' && (
          <div className="space-y-4">
            <div>
              <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-3 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
                Raft Cluster — {raftState?.leader ? `Leader: ${raftState.leader}` : ''}
                <span className="ml-auto font-mono text-[10px] text-gray-500">term={raftState?.term ?? 1}</span>
              </h3>
              <div className="grid grid-cols-2 gap-2">
                {raftState && ['A','B','C','D'].map(id => {
                  const node = raftState.nodes[id];
                  const isLeader = node?.state === 'leader';
                  return (
                    <div key={id} className={`rounded-xl p-3 border ${
                      isLeader
                        ? 'bg-cyan-900/20 border-cyan-700/40'
                        : 'bg-slate-800/60 border-slate-700/50'
                    }`}>
                      <div className="flex items-center justify-between mb-2">
                        <span className="font-bold text-sm text-gray-100">{id}</span>
                        <span className={`text-[9px] px-1.5 py-0.5 rounded font-bold uppercase ${
                          isLeader ? 'bg-cyan-500/30 text-cyan-300' : 'bg-slate-700 text-gray-400'
                        }`}>
                          {isLeader ? '⭐ LEADER' : '⬇ FOLLOWER'}
                        </span>
                      </div>
                      <div className="text-[9px] text-gray-500 mb-1 truncate">{NODE_NAMES[id]}</div>
                      <div className="space-y-0.5">
                        <div className="flex justify-between text-[9px]">
                          <span className="text-gray-500">Term</span>
                          <span className="font-mono text-gray-300">{node?.term ?? 1}</span>
                        </div>
                        <div className="flex justify-between text-[9px]">
                          <span className="text-gray-500">Log</span>
                          <span className="font-mono text-gray-300">{node?.logLen ?? 0} entries</span>
                        </div>
                        <div className="flex justify-between text-[9px]">
                          <span className="text-gray-500">Phase</span>
                          <span className={`font-mono text-[9px] ${
                            (phases[id]||'').includes('NS') ? 'text-emerald-400'
                            : (phases[id]||'').includes('EW') ? 'text-blue-400'
                            : (phases[id]||'').includes('Yellow') ? 'text-yellow-400'
                            : 'text-rose-400'
                          }`}>
                            {(phases[id]||'—').replace(/_/g,' ')}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Consensus event log */}
            <div>
              <h3 className="text-[10px] font-semibold text-gray-400 uppercase tracking-widest mb-2">
                Consensus Events
              </h3>
              <div className="space-y-1 font-mono text-[9px]">
                {raftEvents.length === 0 && (
                  <p className="text-gray-500 italic">No events yet.</p>
                )}
                {raftEvents.map((e, i) => {
                  const isCommit    = e.msg.startsWith('🔒');
                  const isPropose   = e.msg.startsWith('⭐');
                  const isHeartbeat = e.msg.startsWith('💓');
                  const isLLM       = e.msg.startsWith('🤖');
                  const isAck       = e.msg.startsWith('✓');
                  return (
                    <div key={i} className={`p-1.5 rounded flex gap-1.5 ${
                      isCommit    ? 'bg-emerald-900/30 text-emerald-300'
                      : isPropose ? 'bg-cyan-900/20 text-cyan-300'
                      : isLLM     ? 'bg-purple-900/20 text-purple-300'
                      : isAck     ? 'bg-slate-800/80 text-gray-300'
                      : isHeartbeat ? 'bg-slate-800/40 text-gray-500'
                      :               'bg-slate-800/60 text-gray-400'
                    }`}>
                      <span className="text-gray-600 shrink-0">{e.t}</span>
                      <span className="leading-tight">{e.msg}</span>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Causal rules */}
            <div className="bg-slate-800/40 rounded-xl p-3 border border-slate-700/30">
              <h3 className="text-[10px] font-semibold text-gray-400 uppercase tracking-widest mb-2">
                Causal Dependency Rules
              </h3>
              <div className="space-y-1 text-[10px]">
                {[
                  ['N-S ↔ E-W: Mutual exclusivity', true],
                  ['Emergency: Override all signals', demoMode === 'emergency'],
                  ['15s minimum green time', true],
                  ['Raft quorum required for phase change', true],
                ].map(([label, active]) => (
                  <div key={label} className="flex items-center gap-2">
                    <span className={`w-1.5 h-1.5 rounded-full ${active ? 'bg-emerald-400' : 'bg-gray-600'}`} />
                    <span className="text-gray-400">{label}{' '}
                      <span className={active ? 'text-emerald-400' : 'text-gray-600'}>
                        ({active ? 'enforced' : 'standby'})
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* ══ STATS TAB ═══════════════════════════════════════════════════ */}
        {activeTab === 'stats' && (
          <div className="space-y-4">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-3">Network Summary</h3>
            <div className="grid grid-cols-2 gap-2">
              <StatCard label="Intersections" value={4} highlight />
              <StatCard label="Active Vehicles" value={vehicles.length} highlight />
              <StatCard label="Raft Nodes" value={4} />
              <StatCard label="Leader" value={raftState?.leader || 'A'} />
              <StatCard label="Raft Term" value={raftState?.term || 1} />
              <StatCard label="Log Entries" value={raftState?.nodes?.A?.logLen || 0} />
            </div>
            {stats && (
              <>
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-widest mt-4 mb-2">Replay Data</h3>
                <div className="grid grid-cols-2 gap-2">
                  <StatCard label="Flow Routes" value={stats.flowRoutes} />
                  <StatCard label="Replay Size" value={stats.replayFileSize ? `${(stats.replayFileSize/1024/1024).toFixed(1)} MB` : '—'} />
                </div>
              </>
            )}
          </div>
        )}

        {/* ══ LOGS TAB ════════════════════════════════════════════════════ */}
        {activeTab === 'logs' && (
          <div className="space-y-2">
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-3">Event Stream</h3>
            <div className="font-mono text-[9px] leading-relaxed space-y-1">
              {raftEvents.length === 0 && <p className="text-gray-500 italic">No events yet.</p>}
              {raftEvents.map((e, i) => {
                const isErr = e.msg.includes('Error');
                const isSys = e.msg.startsWith('🟢') || e.msg.startsWith('🔒');
                const isLLM = e.msg.startsWith('🤖');
                return (
                  <div key={i} className={`p-1.5 rounded flex gap-1.5 ${
                    isErr ? 'text-rose-400 bg-rose-400/10'
                    : isSys ? 'text-emerald-400 bg-emerald-400/10'
                    : isLLM ? 'text-purple-300 bg-purple-400/10'
                    :          'text-gray-300 bg-slate-800/50'
                  }`}>
                    <span className="text-gray-500 shrink-0">{e.t}</span>
                    <span>{e.msg}</span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>

      {/* ── Footer controls ── */}
      <div className="bg-city-surface/90 border-t border-slate-700/50 p-3 shrink-0">
        <div className="flex items-center justify-center gap-3 mb-3">
          <button onClick={() => setCurrentStep(0)} className="p-2 rounded hover:bg-slate-700 text-gray-400 hover:text-white transition-colors">
            <SkipBack size={14} />
          </button>
          <button onClick={() => setCurrentStep(Math.max(0, currentStep - 10))} className="p-2 rounded hover:bg-slate-700 text-gray-400 hover:text-white transition-colors">
            <Rewind size={16} />
          </button>
          <button onClick={() => setPlaying(!playing)}
            className="w-10 h-10 rounded-full flex items-center justify-center bg-gradient-to-tr from-city-accent to-city-accent-alt text-city-bg shadow-[0_0_15px_rgba(34,211,238,0.3)] hover:scale-105 transition-transform">
            {playing ? <Pause size={18} className="fill-current" /> : <Play size={18} className="fill-current ml-0.5" />}
          </button>
          <button onClick={() => setCurrentStep(Math.min(totalFrames - 1, currentStep + 10))} className="p-2 rounded hover:bg-slate-700 text-gray-400 hover:text-white transition-colors">
            <FastForward size={16} />
          </button>
          <button onClick={() => setCurrentStep(totalFrames - 1)} className="p-2 rounded hover:bg-slate-700 text-gray-400 hover:text-white transition-colors">
            <SkipForward size={14} />
          </button>
        </div>
        <div className="flex items-center gap-2 text-[10px] text-gray-400">
          <span className="w-10 text-right">Speed</span>
          <input type="range" min="1" max="60" value={fps} onChange={e => setFps(+e.target.value)}
            className="flex-1 accent-city-accent bg-slate-700 h-1.5 rounded-lg appearance-none cursor-pointer" />
          <span className="w-10 font-mono text-city-accent text-right">{fps} fps</span>
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, highlight }) {
  return (
    <div className={`p-3 rounded-lg border ${highlight ? 'bg-city-accent/10 border-city-accent/30' : 'bg-slate-800/40 border-slate-700/50'}`}>
      <div className={`font-mono text-lg font-bold mb-1 ${highlight ? 'text-city-accent' : 'text-gray-100'}`}>{value ?? '—'}</div>
      <div className="text-[10px] text-gray-500 uppercase tracking-wider">{label}</div>
    </div>
  );
}
