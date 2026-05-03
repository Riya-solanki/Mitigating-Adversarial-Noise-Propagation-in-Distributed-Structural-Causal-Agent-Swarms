import { useState, useEffect, useRef } from 'react';

const STOP = 48;
const SPAWN = 270;
const DESPAWN = 295;
const TURN_ZONE = 20;
const GAP = 16;
const TICK = 33;
const RATE = 1100;
const LANES = [6, 12, 18];
const MAX_CARS = 40;

let nid = 0;

function isGreenFor(dir, phase) {
  if (phase === 'Emergency' || phase === 'Yellow') return false;
  if (phase === 'NS_Through' || phase === 'NS_Left') return dir === 'N' || dir === 'S';
  if (phase === 'EW_Through' || phase === 'EW_Left') return dir === 'E' || dir === 'W';
  return false;
}

function distFromCenter(v) {
  return (v.odir === 'N' || v.odir === 'S') ? Math.abs(v.y) : Math.abs(v.x);
}

// True once vehicle reaches or crosses the stop line
function pastStopLine(v) {
  switch (v.odir) {
    case 'N': return v.y >= -STOP;
    case 'S': return v.y <= STOP;
    case 'W': return v.x >= -STOP;
    case 'E': return v.x <= STOP;
    default: return false;
  }
}

// Undo the last move by pushing vehicle back just before the stop line
function revertToStopLine(v) {
  switch (v.odir) {
    case 'N': v.y = -STOP - 0.5; break;
    case 'S': v.y = STOP + 0.5; break;
    case 'W': v.x = -STOP - 0.5; break;
    case 'E': v.x = STOP + 0.5; break;
  }
}

function moveForward(v) {
  switch (v.mdir) {
    case 'DOWN': v.y += v.spd; break;
    case 'UP': v.y -= v.spd; break;
    case 'RIGHT': v.x += v.spd; break;
    case 'LEFT': v.x -= v.spd; break;
  }
}

const TURNS = {
  N: { L: { mdir: 'RIGHT', angle: 90 }, R: { mdir: 'LEFT', angle: 270 } },
  S: { L: { mdir: 'LEFT', angle: 270 }, R: { mdir: 'RIGHT', angle: 90 } },
  W: { L: { mdir: 'UP', angle: 0 }, R: { mdir: 'DOWN', angle: 180 } },
  E: { L: { mdir: 'DOWN', angle: 180 }, R: { mdir: 'UP', angle: 0 } },
};

function spawn(d) {
  const ln = Math.floor(Math.random() * 3);
  const off = LANES[ln];
  const spd = 0.8 + Math.random() * 0.4;
  const id = `v${nid++}`;
  const tr = Math.random();
  const turn = tr < 0.60 ? 'S' : tr < 0.82 ? 'L' : 'R';
  switch (d) {
    case 'N': return { id, x: off, y: -SPAWN, angle: 180, odir: d, mdir: 'DOWN', ln, turn, spd, pastStop: false, turned: false, length: 5, width: 2 };
    case 'S': return { id, x: -off, y: SPAWN, angle: 0, odir: d, mdir: 'UP', ln, turn, spd, pastStop: false, turned: false, length: 5, width: 2 };
    case 'W': return { id, x: -SPAWN, y: -off, angle: 90, odir: d, mdir: 'RIGHT', ln, turn, spd, pastStop: false, turned: false, length: 5, width: 2 };
    case 'E': return { id, x: SPAWN, y: off, angle: 270, odir: d, mdir: 'LEFT', ln, turn, spd, pastStop: false, turned: false, length: 5, width: 2 };
    default: return null;
  }
}

export default function useTrafficSimulation(demoPhase) {
  const vehiclesRef = useRef([]);
  const lastSpawnRef = useRef({ N: 0, S: 0, E: 0, W: 0 });
  const [out, setOut] = useState([]);

  useEffect(() => {
    const iv = setInterval(() => {
      const now = Date.now();
      let list = [...vehiclesRef.current];

      // 1. Spawn
      if (list.length < MAX_CARS) {
        ['N', 'S', 'E', 'W'].forEach(d => {
          if (now - lastSpawnRef.current[d] > RATE + Math.random() * 500) {
            const v = spawn(d);
            if (v) list.push(v);
            lastSpawnRef.current[d] = now;
          }
        });
      }

      // 2. Queue groups for gap logic
      const groups = {};
      list.forEach(v => {
        if (!v.pastStop) {
          const k = `${v.odir}_${v.ln}`;
          if (!groups[k]) groups[k] = [];
          groups[k].push(v);
        }
      });
      Object.values(groups).forEach(g =>
        g.sort((a, b) => distFromCenter(a) - distFromCenter(b))
      );

      // 3. Tick
      list = list.map(v => {
        const nv = { ...v };
        const green = isGreenFor(nv.odir, demoPhase);

        // ── Committed vehicles: always move, no exceptions ────────────
        if (nv.pastStop) {
          moveForward(nv);
          if (!nv.turned
            && Math.abs(nv.x) < TURN_ZONE
            && Math.abs(nv.y) < TURN_ZONE
            && nv.turn !== 'S') {
            const t = TURNS[nv.odir]?.[nv.turn];
            if (t) { nv.mdir = t.mdir; nv.angle = t.angle; nv.turned = true; }
          }
          return nv;
        }

        // ── Approaching: gap check first ──────────────────────────────
        const k = `${nv.odir}_${nv.ln}`;
        const g = groups[k] || [];
        const idx = g.findIndex(o => o.id === nv.id);
        if (idx > 0) {
          const ahead = g[idx - 1];
          if (distFromCenter(nv) - distFromCenter(ahead) < GAP) return nv;
        }

        // ── Move ──────────────────────────────────────────────────────
        moveForward(nv);

        // ── AFTER moving: check if we crossed the stop line ───────────
        // This is the critical fix — checking AFTER the move catches
        // vehicles that were 1 unit away (atStop=false before move,
        // atStop=true after move). On red they get pushed back.
        if (pastStopLine(nv)) {
          if (green) {
            nv.pastStop = true;   // committed — green light, go through
          } else {
            revertToStopLine(nv); // red light — push back to stop line
          }
        }

        return nv;
      });

      // 4. Despawn
      list = list.filter(v =>
        Math.abs(v.x) < DESPAWN && Math.abs(v.y) < DESPAWN
      );

      vehiclesRef.current = list;
      setOut([...list]);
    }, TICK);

    return () => clearInterval(iv);
  }, [demoPhase]);

  return out;
}