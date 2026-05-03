import React, { useMemo } from 'react';
import DeckGL from '@deck.gl/react';
import { OrthographicView } from '@deck.gl/core';
import { PolygonLayer, PathLayer, ScatterplotLayer } from '@deck.gl/layers';

// ── 4-intersection hardcoded geometry ──────────────────────────────────────
const ISEC_CENTERS = [
  { id:'A', cx:-300, cy:-300, label:'Main & 1st' },
  { id:'B', cx: 300, cy:-300, label:'Main & 2nd' },
  { id:'C', cx:-300, cy: 300, label:'Main & 3rd' },
  { id:'D', cx: 300, cy: 300, label:'Main & 4th' },
];

const ROAD_WIDTH = 34;  // ~3 lanes × ~11 units
const ISEC_RADIUS = 56; // octagon size

function makeOctagon(cx, cy, r) {
  return Array.from({ length: 8 }, (_, i) => {
    const a = (i * Math.PI / 4) + Math.PI / 8;
    return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  });
}

// Road color based on phase (subtle tint for direction being served)
function phaseToNSColor(phase) {
  if (phase === 'NS_Through' || phase === 'NS_Left') return [34, 197, 94, 80];   // green tint
  if (phase === 'Yellow') return [234, 179, 8, 60];
  if (phase === 'Emergency') return [239, 68, 68, 80];
  return [30, 41, 59, 255]; // default road colour
}
function phaseToEWColor(phase) {
  if (phase === 'EW_Through' || phase === 'EW_Left') return [34, 197, 94, 80];
  if (phase === 'Yellow') return [234, 179, 8, 60];
  if (phase === 'Emergency') return [239, 68, 68, 80];
  return [30, 41, 59, 255];
}

// Signal dot colors
function signalColor(on, emergency) {
  if (emergency) return [239, 68, 68];
  return on ? [34, 211, 238] : [239, 68, 68];
}

export default function CityFlowMap({ roadnet, vehicles, phases = {} }) {

  const initialViewState = useMemo(() => ({
    target: [0, 0, 0],
    zoom: 0.72,
    minZoom: -3,
    maxZoom: 12,
  }), []);

  const layers = useMemo(() => {
    const l = [];
    const isEmergency = Object.values(phases).some(p => p === 'Emergency');

    // ── 1. Road network ────────────────────────────────────────────────
    // Vertical roads (left and right corridors)
    const vertRoads = [
      { x: -300, color: [30, 41, 59] },
      { x:  300, color: [30, 41, 59] },
    ];
    const horizRoads = [
      { y: -300, color: [30, 41, 59] },
      { y:  300, color: [30, 41, 59] },
    ];

    l.push(new PathLayer({
      id: 'roads-vert',
      data: vertRoads,
      getPath: d => [[d.x, -720], [d.x, 720]],
      getColor: [30, 41, 59],
      getWidth: ROAD_WIDTH,
      widthUnits: 'meters',
      capRounded: true,
    }));
    l.push(new PathLayer({
      id: 'roads-horiz',
      data: horizRoads,
      getPath: d => [[-720, d.y], [720, d.y]],
      getColor: [30, 41, 59],
      getWidth: ROAD_WIDTH,
      widthUnits: 'meters',
      capRounded: true,
    }));

    // ── 2. Phase-tinted road overlays (shows which direction is green) ─
    // NS overlay on vertical roads
    l.push(new PathLayer({
      id: 'ns-tint-left',
      data: [{ x: -300 }],
      getPath: d => [[d.x, -720], [d.x, 720]],
      getColor: () => {
        // Use average of A and C phases for left corridor
        const p = phases.A || 'NS_Through';
        return phaseToNSColor(p);
      },
      getWidth: ROAD_WIDTH * 0.6,
      widthUnits: 'meters',
    }));
    l.push(new PathLayer({
      id: 'ns-tint-right',
      data: [{ x: 300 }],
      getPath: d => [[d.x, -720], [d.x, 720]],
      getColor: () => phaseToNSColor(phases.B || 'NS_Through'),
      getWidth: ROAD_WIDTH * 0.6,
      widthUnits: 'meters',
    }));
    // EW overlay
    l.push(new PathLayer({
      id: 'ew-tint-top',
      data: [{ y: -300 }],
      getPath: d => [[-720, d.y], [720, d.y]],
      getColor: () => phaseToEWColor(phases.A || 'NS_Through'),
      getWidth: ROAD_WIDTH * 0.6,
      widthUnits: 'meters',
    }));
    l.push(new PathLayer({
      id: 'ew-tint-bottom',
      data: [{ y: 300 }],
      getPath: d => [[-720, d.y], [720, d.y]],
      getColor: () => phaseToEWColor(phases.C || 'NS_Through'),
      getWidth: ROAD_WIDTH * 0.6,
      widthUnits: 'meters',
    }));

    // ── 3. Intersection boxes (octagons) ────────────────────────────────
    const isecPolygons = ISEC_CENTERS.map(({ id, cx, cy }) => ({
      id, polygon: makeOctagon(cx, cy, ISEC_RADIUS),
    }));
    l.push(new PolygonLayer({
      id: 'intersections',
      data: isecPolygons,
      getPolygon: d => d.polygon,
      getFillColor: [51, 65, 85],
      getLineColor: [100, 116, 139],
      lineWidthMinPixels: 1,
      stroked: true, filled: true, extruded: false,
    }));

    // ── 4. Traffic signal indicators (8 dots, 2 per intersection) ──────
    // NS signal dot (north side of each intersection)
    // EW signal dot (east side of each intersection)
    const signalDots = [];
    ISEC_CENTERS.forEach(({ id, cx, cy }) => {
      const phase = phases[id] || 'NS_Through';
      const nsGreen = phase === 'NS_Through' || phase === 'NS_Left';
      const ewGreen = phase === 'EW_Through' || phase === 'EW_Left';
      // North signal (NS)
      signalDots.push({ x: cx,       y: cy - 68, color: signalColor(nsGreen, isEmergency), axis: 'NS' });
      // South signal (NS)
      signalDots.push({ x: cx,       y: cy + 68, color: signalColor(nsGreen, isEmergency), axis: 'NS' });
      // West signal (EW)
      signalDots.push({ x: cx - 68,  y: cy,      color: signalColor(ewGreen, isEmergency), axis: 'EW' });
      // East signal (EW)
      signalDots.push({ x: cx + 68,  y: cy,      color: signalColor(ewGreen, isEmergency), axis: 'EW' });
    });
    l.push(new ScatterplotLayer({
      id: 'signal-dots',
      data: signalDots,
      getPosition: d => [d.x, d.y],
      getFillColor: d => d.color,
      getRadius: 8,
      radiusUnits: 'meters',
      radiusMinPixels: 5,
      updateTriggers: { getFillColor: [phases] },
    }));

    // ── 5. Vehicles ────────────────────────────────────────────────────
    if (vehicles && vehicles.length > 0) {
      const PALETTE = [[34,211,238],[167,139,250],[52,211,153],[248,113,113],[251,191,36]];
      l.push(new ScatterplotLayer({
        id: 'vehicles',
        data: vehicles,
        getPosition: d => [d.x, d.y],
        getFillColor: d => {
          let h = 0;
          for (let i = 0; i < d.id.length; i++) h = (h * 31 + d.id.charCodeAt(i)) | 0;
          return PALETTE[Math.abs(h) % PALETTE.length];
        },
        getRadius: d => Math.max(d.width || 2, d.length || 5) * 1.2,
        radiusUnits: 'meters',
        radiusMinPixels: 4,
        stroked: true,
        getLineColor: [0, 0, 0, 120],
        lineWidthMinPixels: 1,
        updateTriggers: { getPosition: [vehicles] },
      }));
    }

    return l;
  }, [roadnet, vehicles, phases]);

  return (
    <div className="absolute inset-0 bg-city-bg" onContextMenu={e => e.preventDefault()}>
      <DeckGL
        views={new OrthographicView({ id: 'ortho' })}
        initialViewState={initialViewState}
        controller={true}
        layers={layers}
        getCursor={({ isDragging }) => isDragging ? 'grabbing' : 'grab'}
      />
    </div>
  );
}
