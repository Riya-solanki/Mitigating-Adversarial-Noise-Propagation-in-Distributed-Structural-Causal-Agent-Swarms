/**
 * useMultiTraffic — vehicle simulation for a 2×2 intersection grid.
 *
 * Layout (centers):
 *   A(-300,-300)  B(300,-300)
 *   C(-300, 300)  D(300, 300)
 *
 * 8 traffic channels (one per direction per corridor).
 * Each vehicle carries a route through 1–2 intersections.
 * Signal logic: post-move stop-line check (same proven fix as single-intersection).
 */

import { useState, useEffect, useRef } from 'react';

// ── Intersection geometry ──────────────────────────────────────────────────
const ISEC = {
  A: { cx: -300, cy: -300 },
  B: { cx:  300, cy: -300 },
  C: { cx: -300, cy:  300 },
  D: { cx:  300, cy:  300 },
};

const STOP = 52;   // stop before intersection center
const BOX  = 60;   // clear zone past intersection center
const GAP  = 22;   // min bumper-to-bumper spacing
const TICK = 33;
const RATE = 1400;
const MAX  = 80;

let nid = 0;

// ── 8 traffic channels ─────────────────────────────────────────────────────
// Each channel defines a fixed lane and route through the grid.
const CHANNELS = [
  // North → South (left corridor, x≈-292, through A then C)
  { key:'DL', mdir:'DOWN',  angle:180, route:['A','C'], sx:() => -292, sy:() => -720 },
  // South → North (left corridor, x≈-308, through C then A)
  { key:'UL', mdir:'UP',    angle:0,   route:['C','A'], sx:() => -308, sy:() =>  720 },
  // North → South (right corridor, x≈308, through B then D)
  { key:'DR', mdir:'DOWN',  angle:180, route:['B','D'], sx:() =>  308, sy:() => -720 },
  // South → North (right corridor, x≈292, through D then B)
  { key:'UR', mdir:'UP',    angle:0,   route:['D','B'], sx:() =>  292, sy:() =>  720 },
  // West → East (top corridor, y≈-292, through A then B)
  { key:'RT', mdir:'RIGHT', angle:90,  route:['A','B'], sx:() => -720, sy:() => -292 },
  // East → West (top corridor, y≈-308, through B then A)
  { key:'LT', mdir:'LEFT',  angle:270, route:['B','A'], sx:() =>  720, sy:() => -308 },
  // West → East (bottom corridor, y≈308, through C then D)
  { key:'RB', mdir:'RIGHT', angle:90,  route:['C','D'], sx:() => -720, sy:() =>  308 },
  // East → West (bottom corridor, y≈292, through D then C)
  { key:'LB', mdir:'LEFT',  angle:270, route:['D','C'], sx:() =>  720, sy:() =>  292 },
];

// ── Helpers ────────────────────────────────────────────────────────────────

function isGreenForMdir(mdir, phase) {
  if (phase === 'Emergency' || phase === 'Yellow') return false;
  if (phase === 'NS_Through' || phase === 'NS_Left') return mdir === 'DOWN' || mdir === 'UP';
  if (phase === 'EW_Through' || phase === 'EW_Left') return mdir === 'LEFT' || mdir === 'RIGHT';
  return false;
}

// "Progress" = how far the vehicle has travelled along its route.
// Used for gap sorting (higher = further along = closer to destination).
function progress(v) {
  switch (v.mdir) {
    case 'DOWN':  return  v.y;
    case 'UP':    return -v.y;
    case 'RIGHT': return  v.x;
    case 'LEFT':  return -v.x;
    default: return 0;
  }
}

function atOrPastStop(v, iid) {
  const { cx, cy } = ISEC[iid];
  switch (v.mdir) {
    case 'DOWN':  return v.y >= cy - STOP;
    case 'UP':    return v.y <= cy + STOP;
    case 'RIGHT': return v.x >= cx - STOP;
    case 'LEFT':  return v.x <= cx + STOP;
    default: return false;
  }
}

function pastBox(v, iid) {
  const { cx, cy } = ISEC[iid];
  switch (v.mdir) {
    case 'DOWN':  return v.y >  cy + BOX;
    case 'UP':    return v.y <  cy - BOX;
    case 'RIGHT': return v.x >  cx + BOX;
    case 'LEFT':  return v.x <  cx - BOX;
    default: return false;
  }
}

function revertToStop(v, iid) {
  const { cx, cy } = ISEC[iid];
  switch (v.mdir) {
    case 'DOWN':  v.y = cy - STOP - 1; break;
    case 'UP':    v.y = cy + STOP + 1; break;
    case 'RIGHT': v.x = cx - STOP - 1; break;
    case 'LEFT':  v.x = cx + STOP + 1; break;
  }
}

function moveForward(v) {
  switch (v.mdir) {
    case 'DOWN':  v.y += v.spd; break;
    case 'UP':    v.y -= v.spd; break;
    case 'RIGHT': v.x += v.spd; break;
    case 'LEFT':  v.x -= v.spd; break;
  }
}

function spawnVehicle(ch) {
  const jitter = (Math.random() - 0.5) * 6;
  return {
    id:        `v${nid++}`,
    x:         ch.sx() + (ch.mdir === 'DOWN' || ch.mdir === 'UP' ? jitter : 0),
    y:         ch.sy() + (ch.mdir === 'LEFT' || ch.mdir === 'RIGHT' ? jitter : 0),
    angle:     ch.angle,
    mdir:      ch.mdir,
    spd:       1.0 + Math.random() * 0.5,
    laneKey:   ch.key,
    route:     [...ch.route],
    routeIdx:  0,
    committed: false,
    length:    5,
    width:     2,
  };
}

// ── Hook ───────────────────────────────────────────────────────────────────

export default function useMultiTraffic(phases) {
  const vehiclesRef  = useRef([]);
  const phasesRef    = useRef(phases);
  const lastSpawnRef = useRef({});
  CHANNELS.forEach(ch => { lastSpawnRef.current[ch.key] = 0; });
  const [out, setOut] = useState([]);

  // Keep phasesRef current without restarting the interval
  useEffect(() => { phasesRef.current = phases; }, [phases]);

  useEffect(() => {
    const iv = setInterval(() => {
      const now    = Date.now();
      const phases = phasesRef.current;
      let   list   = [...vehiclesRef.current];

      // ── Spawn ────────────────────────────────────────────────────────
      if (list.length < MAX) {
        CHANNELS.forEach(ch => {
          const last = lastSpawnRef.current[ch.key] || 0;
          if (now - last > RATE + Math.random() * 800) {
            list.push(spawnVehicle(ch));
            lastSpawnRef.current[ch.key] = now;
          }
        });
      }

      // ── Lane groups for gap logic ─────────────────────────────────────
      // Sort each lane by progress descending (lead vehicle first).
      const lanes = {};
      list.forEach(v => {
        if (!lanes[v.laneKey]) lanes[v.laneKey] = [];
        lanes[v.laneKey].push(v);
      });
      Object.values(lanes).forEach(g =>
        g.sort((a, b) => progress(b) - progress(a))
      );

      // ── Tick each vehicle ─────────────────────────────────────────────
      list = list.map(v => {
        const nv = { ...v };

        // Done with all intersections → free travel to despawn
        if (nv.routeIdx >= nv.route.length) {
          moveForward(nv);
          return nv;
        }

        const iid   = nv.route[nv.routeIdx];
        const phase = phases[iid] || 'NS_Through';
        const green = isGreenForMdir(nv.mdir, phase);

        // Committed → keep moving until we clear the box, then advance route
        if (nv.committed) {
          moveForward(nv);
          if (pastBox(nv, iid)) {
            nv.routeIdx++;
            nv.committed = false;
          }
          return nv;
        }

        // Gap check — don't rear-end the vehicle ahead in the same lane
        const lane = lanes[nv.laneKey] || [];
        const idx  = lane.findIndex(o => o.id === nv.id);
        if (idx > 0) {
          const ahead = lane[idx - 1];
          if (progress(ahead) - progress(nv) < GAP) return nv;
        }

        // Move
        moveForward(nv);

        // Post-move stop-line check (the critical fix: check AFTER moving)
        if (atOrPastStop(nv, iid)) {
          if (green) {
            nv.committed = true;   // green: commit and flow through
          } else {
            revertToStop(nv, iid); // red: push back, try again next tick
          }
        }

        return nv;
      });

      // ── Despawn ───────────────────────────────────────────────────────
      list = list.filter(v => Math.abs(v.x) < 800 && Math.abs(v.y) < 800);

      vehiclesRef.current = list;
      setOut([...list]);
    }, TICK);

    return () => clearInterval(iv);
  }, []); // empty — phases read via ref

  return out;
}
