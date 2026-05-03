(() => {
"use strict";
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

// DOM
const splash = $("#splash"), app = $("#app");
const splashFill = $("#splash-fill"), splashText = $("#splash-text");
const canvas = $("#traffic-canvas"), ctx = canvas.getContext("2d");
const stepLabel = $("#step-label"), statVehicles = $("#stat-vehicles");
const statFps = $("#stat-fps"), overlayTime = $("#overlay-time");
const btnPlay = $("#btn-play"), btnPrev = $("#btn-prev"), btnNext = $("#btn-next");
const btnStart = $("#btn-start"), btnEnd = $("#btn-end"), btnFS = $("#btn-fullscreen");
const timeline = $("#timeline"), speedSlider = $("#speed"), zoomSlider = $("#zoom");
const speedVal = $("#speed-val"), zoomVal = $("#zoom-val");
const tlList = $("#tl-list"), logEntries = $("#log-entries");
const chartCanvas = $("#chart-vehicles");

// State
let roadnet = null, stats = null, totalFrames = 0;
let frameBuffer = new Map(); // Map<frameIndex, parsed frame>
let currentStep = 0, playing = false, fps = 10, intervalId = null;
let zoomLevel = 1, panX = 0, panY = 0;
let dragging = false, dragSX = 0, dragSY = 0, panSX = 0, panSY = 0;
let peakVehicles = 0;
const vehicleHistory = []; // for chart
const BUFFER_SIZE = 400;
const COLORS = ["#22d3ee","#a78bfa","#34d399","#f87171","#fbbf24","#f472b6",
  "#60a5fa","#c084fc","#fb923c","#4ade80","#38bdf8","#e879f9"];

// ── Boot ──
init();

async function init() {
  try {
    splashText.textContent = "Loading road network…";
    splashFill.style.width = "15%";
    roadnet = await fetchJSON("/api/replay-roadnet");

    splashText.textContent = "Loading simulation stats…";
    splashFill.style.width = "30%";
    stats = await fetchJSON("/api/stats");

    splashText.textContent = "Counting frames…";
    splashFill.style.width = "50%";
    const fc = await fetchJSON("/api/frames/count");
    totalFrames = fc.total;

    splashText.textContent = "Buffering initial frames…";
    splashFill.style.width = "70%";
    await loadFrames(0, BUFFER_SIZE);

    splashFill.style.width = "100%";
    splashText.textContent = `Ready — ${totalFrames.toLocaleString()} frames loaded.`;
    await delay(600);

    splash.classList.remove("active");
    app.classList.add("active");
    initApp();
  } catch (e) {
    splashText.textContent = "Error: " + e.message;
    console.error(e);
  }
}

async function fetchJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

async function loadFrames(start, count) {
  const data = await fetchJSON(`/api/frames?start=${start}&count=${count}`);
  data.frames.forEach((raw, i) => {
    frameBuffer.set(start + i, parseFrame(raw));
  });
  addLog("info", `Buffered frames ${start}–${start + data.frames.length - 1}`);
}

function parseFrame(line) {
  const parts = line.split(";");
  const vPart = (parts[0] || "").trim(), tlPart = (parts[1] || "").trim();
  const vehicles = [];
  if (vPart) {
    for (const e of vPart.split(",")) {
      const t = e.trim(); if (!t) continue;
      const tk = t.split(/\s+/);
      if (tk.length >= 4) vehicles.push({
        x: +tk[0], y: +tk[1], angle: +tk[2], id: tk[3],
        status: +(tk[4]||0), length: +(tk[5]||5), width: +(tk[6]||2)
      });
    }
  }
  const trafficLights = {};
  if (tlPart) {
    for (const e of tlPart.split(",")) {
      const t = e.trim(); if (!t) continue;
      const tk = t.split(/\s+/);
      if (tk.length >= 2) trafficLights[tk[0]] = tk.slice(1);
    }
  }
  return { vehicles, trafficLights };
}

// ── App Init ──
function initApp() {
  resizeCanvas();
  window.addEventListener("resize", resizeCanvas);
  timeline.max = totalFrames - 1;
  autoFit();
  populateStats();
  setupControls();
  draw();
}

function resizeCanvas() {
  const w = $("#canvas-area");
  canvas.width = w.clientWidth * devicePixelRatio;
  canvas.height = w.clientHeight * devicePixelRatio;
  canvas.style.width = w.clientWidth + "px";
  canvas.style.height = w.clientHeight + "px";
  ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  draw();
}

function autoFit() {
  if (!roadnet?.static?.nodes) return;
  let mnX=Infinity, mxX=-Infinity, mnY=Infinity, mxY=-Infinity;
  for (const n of roadnet.static.nodes) {
    mnX = Math.min(mnX, n.point[0]); mxX = Math.max(mxX, n.point[0]);
    mnY = Math.min(mnY, n.point[1]); mxY = Math.max(mxY, n.point[1]);
  }
  const nw = mxX-mnX||600, nh = mxY-mnY||600;
  const cw = canvas.width/devicePixelRatio, ch = canvas.height/devicePixelRatio;
  zoomLevel = Math.min((cw-100)/nw, (ch-100)/nh);
  zoomLevel = Math.max(0.1, Math.min(3, zoomLevel));
  zoomSlider.value = Math.round(zoomLevel*100);
  zoomVal.textContent = zoomLevel.toFixed(1)+"x";
  panX = cw/2 - ((mnX+mxX)/2)*zoomLevel;
  panY = ch/2 - ((mnY+mxY)/2)*zoomLevel;
}

function populateStats() {
  if (!stats) return;
  $("#s-intersections").textContent = stats.intersections;
  $("#s-roads").textContent = stats.roads;
  $("#s-routes").textContent = stats.flowRoutes;
  $("#s-replay-size").textContent = (stats.replayFileSize / (1024*1024)).toFixed(1) + " MB";
  $("#s-total-frames").textContent = totalFrames.toLocaleString();
}

// ── Controls ──
function setupControls() {
  btnPlay.onclick = togglePlay;
  btnPrev.onclick = () => goTo(currentStep - 1);
  btnNext.onclick = () => goTo(currentStep + 1);
  btnStart.onclick = () => goTo(0);
  btnEnd.onclick = () => goTo(totalFrames - 1);
  timeline.oninput = () => goTo(+timeline.value);
  speedSlider.oninput = () => {
    fps = +speedSlider.value;
    speedVal.textContent = fps + " fps";
    if (playing) { clearInterval(intervalId); intervalId = setInterval(tick, 1000/fps); }
  };
  zoomSlider.oninput = () => {
    zoomLevel = +zoomSlider.value / 100;
    zoomVal.textContent = zoomLevel.toFixed(1) + "x";
    draw();
  };
  btnFS.onclick = () => {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen();
    else document.exitFullscreen();
  };
  canvas.addEventListener("wheel", e => {
    e.preventDefault();
    zoomLevel = Math.max(0.1, Math.min(4, zoomLevel + (e.deltaY > 0 ? -0.08 : 0.08)));
    zoomSlider.value = Math.round(zoomLevel * 100);
    zoomVal.textContent = zoomLevel.toFixed(1) + "x";
    draw();
  });
  canvas.addEventListener("mousedown", e => {
    dragging = true; dragSX = e.clientX; dragSY = e.clientY;
    panSX = panX; panSY = panY; canvas.style.cursor = "grabbing";
  });
  canvas.addEventListener("mousemove", e => {
    if (!dragging) return;
    panX = panSX + (e.clientX - dragSX);
    panY = panSY + (e.clientY - dragSY);
    draw();
  });
  canvas.addEventListener("mouseup", () => { dragging = false; canvas.style.cursor = "grab"; });
  canvas.addEventListener("mouseleave", () => { dragging = false; canvas.style.cursor = "grab"; });
  canvas.style.cursor = "grab";

  document.addEventListener("keydown", e => {
    if (e.code === "Space") { e.preventDefault(); togglePlay(); }
    if (e.code === "ArrowRight") goTo(currentStep + 1);
    if (e.code === "ArrowLeft") goTo(currentStep - 1);
  });

  // Sidebar tabs
  for (const btn of $$(".tab-btn")) {
    btn.onclick = () => {
      $$(".tab-btn").forEach(b => b.classList.remove("active"));
      $$(".tab-panel").forEach(p => p.classList.remove("active"));
      btn.classList.add("active");
      $("#" + btn.dataset.tab).classList.add("active");
    };
  }
  $("#btn-clear-log").onclick = () => { logEntries.innerHTML = ""; };
}

// ── Playback ──
function togglePlay() {
  playing = !playing;
  btnPlay.textContent = playing ? "⏸" : "▶";
  if (playing) intervalId = setInterval(tick, 1000/fps);
  else clearInterval(intervalId);
}
function tick() {
  if (currentStep >= totalFrames - 1) { playing = false; btnPlay.textContent = "▶"; clearInterval(intervalId); return; }
  goTo(currentStep + 1);
}
async function goTo(s) {
  s = Math.max(0, Math.min(totalFrames - 1, s));
  // Buffer ahead if needed
  if (!frameBuffer.has(s)) {
    const wasPlaying = playing;
    if (playing) { playing = false; clearInterval(intervalId); }
    addLog("warn", `Loading frames near ${s}…`);
    const bStart = Math.max(0, s - 50);
    await loadFrames(bStart, BUFFER_SIZE);
    if (wasPlaying) { playing = true; intervalId = setInterval(tick, 1000/fps); btnPlay.textContent = "⏸"; }
  }
  // Prefetch next chunk
  if (s > 0 && s % (BUFFER_SIZE - 100) === 0) {
    const next = s + BUFFER_SIZE - 100;
    if (next < totalFrames && !frameBuffer.has(next)) {
      loadFrames(next, BUFFER_SIZE); // fire and forget
    }
  }
  // Evict old frames to save memory (keep ±500)
  if (frameBuffer.size > 2000) {
    for (const k of frameBuffer.keys()) {
      if (k < s - 500 || k > s + 1000) frameBuffer.delete(k);
    }
  }
  currentStep = s;
  timeline.value = s;
  draw();
}

// ── Rendering ──
function draw() {
  const cw = canvas.width / devicePixelRatio, ch = canvas.height / devicePixelRatio;
  ctx.clearRect(0, 0, cw, ch);
  ctx.save();
  ctx.translate(panX, panY);
  ctx.scale(zoomLevel, zoomLevel);
  drawRoads();
  drawIntersections();
  const frame = frameBuffer.get(currentStep);
  if (frame) {
    drawVehicles(frame.vehicles);
    updateTL(frame.trafficLights);
    updateHUD(frame);
  }
  ctx.restore();
}

function drawRoads() {
  if (!roadnet?.static?.edges) return;
  for (const edge of roadnet.static.edges) {
    const pts = edge.points; if (!pts || pts.length < 2) continue;
    const [x1,y1] = pts[0], [x2,y2] = pts[1];
    const nLane = edge.nLane || 3;
    const lw = (edge.laneWidths?.[0]) || 4;
    const tw = nLane * lw;
    const dx = x2-x1, dy = y2-y1, len = Math.hypot(dx,dy);
    const nx = -dy/len, ny = dx/len;

    // Asphalt
    ctx.beginPath();
    ctx.moveTo(x1+nx*tw/2, y1+ny*tw/2);
    ctx.lineTo(x2+nx*tw/2, y2+ny*tw/2);
    ctx.lineTo(x2-nx*tw/2, y2-ny*tw/2);
    ctx.lineTo(x1-nx*tw/2, y1-ny*tw/2);
    ctx.closePath();
    ctx.fillStyle = "#1a2340";
    ctx.fill();

    // Lane dashes
    ctx.strokeStyle = "rgba(100,120,180,0.2)";
    ctx.lineWidth = 0.4;
    ctx.setLineDash([4,6]);
    for (let i = 1; i < nLane; i++) {
      const off = -tw/2 + i*lw;
      ctx.beginPath();
      ctx.moveTo(x1+nx*off, y1+ny*off);
      ctx.lineTo(x2+nx*off, y2+ny*off);
      ctx.stroke();
    }
    ctx.setLineDash([]);

    // Edges
    ctx.strokeStyle = "rgba(100,120,180,0.15)";
    ctx.lineWidth = 0.8;
    ctx.beginPath();
    ctx.moveTo(x1+nx*tw/2, y1+ny*tw/2);
    ctx.lineTo(x2+nx*tw/2, y2+ny*tw/2);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x1-nx*tw/2, y1-ny*tw/2);
    ctx.lineTo(x2-nx*tw/2, y2-ny*tw/2);
    ctx.stroke();

    // Road label
    ctx.fillStyle = "rgba(100,120,180,0.2)";
    ctx.font = "5px Inter, sans-serif";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText(edge.id.replace("road_",""), (x1+x2)/2, (y1+y2)/2);
  }
}

function drawIntersections() {
  if (!roadnet?.static?.nodes) return;
  for (const node of roadnet.static.nodes) {
    if (node.virtual) continue;
    const ol = node.outline; if (!ol || ol.length < 4) continue;
    ctx.beginPath();
    ctx.moveTo(ol[0], ol[1]);
    for (let i = 2; i < ol.length; i += 2) ctx.lineTo(ol[i], ol[i+1]);
    ctx.closePath();
    ctx.fillStyle = "#243054";
    ctx.fill();
    ctx.strokeStyle = "rgba(100,120,180,0.25)";
    ctx.lineWidth = 0.8;
    ctx.stroke();

    // Crosswalk dashes
    ctx.fillStyle = "rgba(200,210,230,0.08)";
    const [cx,cy] = node.point;
    for (let i = -12; i <= 12; i += 4) {
      ctx.fillRect(cx + i - 1, cy - 14, 2, 3);
      ctx.fillRect(cx + i - 1, cy + 11, 2, 3);
      ctx.fillRect(cx - 14, cy + i - 1, 3, 2);
      ctx.fillRect(cx + 11, cy + i - 1, 3, 2);
    }
  }
}

function drawVehicles(vehicles) {
  for (const v of vehicles) {
    ctx.save();
    ctx.translate(v.x, v.y);
    ctx.rotate(-v.angle + Math.PI/2);
    const hL = v.length/2, hW = v.width/2;
    const col = vColor(v.id);

    // Shadow
    ctx.fillStyle = "rgba(0,0,0,0.3)";
    ctx.beginPath();
    rRect(ctx, -hW+0.3, -hL+0.3, v.width, v.length, 1);
    ctx.fill();

    // Body
    ctx.fillStyle = col;
    ctx.beginPath();
    rRect(ctx, -hW, -hL, v.width, v.length, 1);
    ctx.fill();

    // Windshield
    ctx.fillStyle = "rgba(0,0,0,0.3)";
    ctx.fillRect(-hW+0.4, hL-2, v.width-0.8, 0.9);

    // Headlights
    ctx.fillStyle = "rgba(255,255,220,0.8)";
    ctx.fillRect(-hW+0.2, hL-0.7, 0.6, 0.4);
    ctx.fillRect(hW-0.8, hL-0.7, 0.6, 0.4);

    // Taillights
    ctx.fillStyle = "rgba(248,113,113,0.7)";
    ctx.fillRect(-hW+0.2, -hL+0.2, 0.6, 0.4);
    ctx.fillRect(hW-0.8, -hL+0.2, 0.6, 0.4);

    ctx.restore();
  }
}

function vColor(id) {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) | 0;
  return COLORS[Math.abs(h) % COLORS.length];
}

function rRect(ctx, x, y, w, h, r) {
  ctx.moveTo(x+r, y);
  ctx.lineTo(x+w-r, y); ctx.quadraticCurveTo(x+w, y, x+w, y+r);
  ctx.lineTo(x+w, y+h-r); ctx.quadraticCurveTo(x+w, y+h, x+w-r, y+h);
  ctx.lineTo(x+r, y+h); ctx.quadraticCurveTo(x, y+h, x, y+h-r);
  ctx.lineTo(x, y+r); ctx.quadraticCurveTo(x, y, x+r, y);
}

// ── Traffic Light Panel ──
function updateTL(tl) {
  const roads = Object.keys(tl);
  if (!roads.length) return;
  if (tlList.childElementCount !== roads.length) {
    tlList.innerHTML = "";
    for (const road of roads) {
      const item = document.createElement("div");
      item.className = "tl-item"; item.dataset.road = road;
      const name = document.createElement("span");
      name.className = "tl-road"; name.textContent = road.replace("road_","");
      item.appendChild(name);
      const lanes = document.createElement("div");
      lanes.className = "tl-lanes";
      for (const s of tl[road]) {
        const dot = document.createElement("span");
        dot.className = "tl-dot " + s;
        lanes.appendChild(dot);
      }
      item.appendChild(lanes);
      tlList.appendChild(item);
    }
  } else {
    for (const road of roads) {
      const item = tlList.querySelector(`[data-road="${road}"]`);
      if (!item) continue;
      const dots = item.querySelectorAll(".tl-dot");
      const states = tl[road];
      dots.forEach((d, i) => { d.className = "tl-dot " + (states[i] || "r"); });
    }
  }
}

// ── HUD ──
function updateHUD(frame) {
  const vc = frame.vehicles.length;
  stepLabel.textContent = `Step ${currentStep.toLocaleString()} / ${(totalFrames-1).toLocaleString()}`;
  statVehicles.textContent = "🚗 " + vc;
  statFps.textContent = "⚡ " + fps + " fps";
  overlayTime.textContent = `t = ${currentStep}s`;
  if (vc > peakVehicles) peakVehicles = vc;
  $("#s-live-vehicles").textContent = vc;
  $("#s-buffer").textContent = frameBuffer.size;
  $("#s-peak-vehicles").textContent = peakVehicles;

  // Chart data (sample every 10 steps)
  if (currentStep % 10 === 0) {
    vehicleHistory.push({ step: currentStep, count: vc });
    if (vehicleHistory.length > 200) vehicleHistory.shift();
    drawChart();
  }
}

// ── Mini Chart ──
function drawChart() {
  if (!chartCanvas) return;
  const cctx = chartCanvas.getContext("2d");
  const w = chartCanvas.clientWidth, h = chartCanvas.clientHeight;
  chartCanvas.width = w * devicePixelRatio;
  chartCanvas.height = h * devicePixelRatio;
  cctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
  cctx.clearRect(0, 0, w, h);
  if (vehicleHistory.length < 2) return;

  const maxC = Math.max(...vehicleHistory.map(d => d.count), 1);
  const pad = 4;

  // Gradient fill
  const grad = cctx.createLinearGradient(0, 0, 0, h);
  grad.addColorStop(0, "rgba(34,211,238,0.25)");
  grad.addColorStop(1, "rgba(34,211,238,0.02)");

  cctx.beginPath();
  cctx.moveTo(pad, h - pad);
  vehicleHistory.forEach((d, i) => {
    const x = pad + (i / (vehicleHistory.length - 1)) * (w - pad * 2);
    const y = h - pad - (d.count / maxC) * (h - pad * 2);
    cctx.lineTo(x, y);
  });
  cctx.lineTo(w - pad, h - pad);
  cctx.closePath();
  cctx.fillStyle = grad;
  cctx.fill();

  // Line
  cctx.beginPath();
  vehicleHistory.forEach((d, i) => {
    const x = pad + (i / (vehicleHistory.length - 1)) * (w - pad * 2);
    const y = h - pad - (d.count / maxC) * (h - pad * 2);
    i === 0 ? cctx.moveTo(x, y) : cctx.lineTo(x, y);
  });
  cctx.strokeStyle = "#22d3ee";
  cctx.lineWidth = 1.5;
  cctx.stroke();
}

// ── Log System ──
function addLog(type, msg) {
  const entry = document.createElement("div");
  entry.className = "log-entry";
  const ts = new Date().toLocaleTimeString();
  entry.innerHTML = `<span class="log-ts">[${ts}]</span><span class="log-ev ${type}">${msg}</span>`;
  logEntries.prepend(entry);
  if (logEntries.children.length > 200) logEntries.lastChild.remove();
}

function delay(ms) { return new Promise(r => setTimeout(r, ms)); }
})();
