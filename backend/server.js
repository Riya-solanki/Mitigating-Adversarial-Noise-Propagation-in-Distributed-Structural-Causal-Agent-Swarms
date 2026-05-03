/* ═══════════════════════════════════════════════════════════════
   server.js  —  Lightweight Express backend for CityFlow Replay
   Serves data files via REST endpoints so the frontend never
   has to upload 113 MB through a file dialog.
   ═══════════════════════════════════════════════════════════════ */

const express = require("express");
const fs = require("fs");
const path = require("path");
const readline = require("readline");
const cors = require("cors");

const app = express();
const PORT = 8080;

app.use(cors());

// ── Paths to data files (one directory up from frontend/) ──
const DATA_DIR = path.join(__dirname, "..");
const ROADNET_PATH = path.join(DATA_DIR, "roadnet.json");
const REPLAY_ROADNET_PATH = path.join(DATA_DIR, "replay_roadnet.json");
const FLOW_PATH = path.join(DATA_DIR, "flow.json");
const REPLAY_PATH = path.join(DATA_DIR, "replay.txt");

// ── Serve static frontend files ──
app.use(express.static(__dirname));

// ── API: Road network geometry ──
app.get("/api/roadnet", (req, res) => {
  res.sendFile(ROADNET_PATH);
});

// ── API: Replay-roadnet (simplified topology) ──
app.get("/api/replay-roadnet", (req, res) => {
  res.sendFile(REPLAY_ROADNET_PATH);
});

// ── API: Traffic flow definitions ──
app.get("/api/flow", (req, res) => {
  res.sendFile(FLOW_PATH);
});

// ── API: Total frame count (without loading the entire file) ──
let cachedTotalFrames = null;
app.get("/api/frames/count", async (req, res) => {
  if (cachedTotalFrames !== null) {
    return res.json({ total: cachedTotalFrames });
  }
  try {
    let count = 0;
    const rl = readline.createInterface({
      input: fs.createReadStream(REPLAY_PATH),
      crlfDelay: Infinity,
    });
    for await (const line of rl) {
      if (line.trim().length > 0) count++;
    }
    cachedTotalFrames = count;
    res.json({ total: count });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── API: Fetch a range of frames ──
// GET /api/frames?start=0&count=200
app.get("/api/frames", async (req, res) => {
  const start = parseInt(req.query.start) || 0;
  const count = Math.min(parseInt(req.query.count) || 200, 500); // max 500 per request

  try {
    const frames = [];
    let lineIndex = 0;
    const rl = readline.createInterface({
      input: fs.createReadStream(REPLAY_PATH),
      crlfDelay: Infinity,
    });

    for await (const line of rl) {
      if (line.trim().length === 0) continue;
      if (lineIndex >= start && lineIndex < start + count) {
        frames.push(line);
      }
      if (lineIndex >= start + count) break;
      lineIndex++;
    }

    res.json({ start, count: frames.length, frames });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── API: Simulation log summary (quick stats) ──
app.get("/api/stats", async (req, res) => {
  try {
    const roadnet = JSON.parse(fs.readFileSync(REPLAY_ROADNET_PATH, "utf-8"));
    const flow = JSON.parse(fs.readFileSync(FLOW_PATH, "utf-8"));

    // Count the first frame's vehicles for a quick stat
    const rl = readline.createInterface({
      input: fs.createReadStream(REPLAY_PATH),
      crlfDelay: Infinity,
    });
    let firstLine = "";
    for await (const line of rl) {
      if (line.trim().length > 0) { firstLine = line; break; }
    }

    const vehiclePart = (firstLine.split(";")[0] || "").trim();
    const initialVehicles = vehiclePart
      ? vehiclePart.split(",").filter((e) => e.trim().length > 0).length
      : 0;

    const intersections = roadnet.static?.nodes?.filter((n) => !n.virtual) || [];
    const roads = roadnet.static?.edges || [];

    res.json({
      intersections: intersections.length,
      roads: roads.length,
      flowRoutes: flow.length,
      initialVehicles,
      replayFileSize: fs.statSync(REPLAY_PATH).size,
      totalFrames: cachedTotalFrames,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ── Start ──
app.listen(PORT, () => {
  console.log(`\n  🚦  CityFlow Visualizer running at  http://localhost:${PORT}\n`);
  console.log(`  Data directory: ${DATA_DIR}`);
  console.log(`  Replay file:   ${REPLAY_PATH}`);
  console.log(`  Roadnet file:  ${ROADNET_PATH}\n`);
});
