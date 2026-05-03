/**
 * useLiveTraffic — React hook that connects to the Python CityFlow bridge
 * WebSocket server (ws://localhost:8765) and streams live vehicle positions,
 * traffic light phases, and queue metrics into the UI.
 *
 * Returned shape:
 * {
 *   connected: boolean,          // true when WS is open and receiving frames
 *   vehicles:  VehicleObject[],  // [{id, x, y, angle, speed, length, width}, ...]
 *   phase:     string,           // "NS_Through" | "EW_Through" | "NS_Left" | ...
 *   phaseId:   number,           // raw CityFlow phase integer (0-7)
 *   metrics:   { ns_queue, ew_queue, step },
 *   step:      number,
 *   error:     string | null,
 * }
 *
 * Auto-reconnect: exponential backoff up to 8 s between retries.
 * The hook cleans up the WebSocket on unmount.
 */

import { useState, useEffect, useRef, useCallback } from 'react';

const WS_URL   = import.meta.env.VITE_WS_URL || 'ws://localhost:8765';
const MAX_BACKOFF_MS = 8000;

export default function useLiveTraffic() {
  const [connected, setConnected]   = useState(false);
  const [vehicles,  setVehicles]    = useState([]);
  const [phase,     setPhase]       = useState('NS_Through');
  const [phaseId,   setPhaseId]     = useState(0);
  const [metrics,   setMetrics]     = useState({ ns_queue: 0, ew_queue: 0, step: 0 });
  const [step,      setStep]        = useState(0);
  const [error,     setError]       = useState(null);

  const wsRef        = useRef(null);
  const backoffRef   = useRef(500);   // initial retry delay ms
  const retryTimer   = useRef(null);
  const mountedRef   = useRef(true);

  const connect = useCallback(() => {
    if (!mountedRef.current) return;
    if (wsRef.current && wsRef.current.readyState < WebSocket.CLOSING) return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      if (!mountedRef.current) return;
      setConnected(true);
      setError(null);
      backoffRef.current = 500;  // reset backoff on successful connect
    };

    ws.onmessage = (event) => {
      if (!mountedRef.current) return;
      try {
        const frame = JSON.parse(event.data);

        // ── vehicles ──────────────────────────────────────────────────────
        // CityFlow x/y are in the same ±300 coordinate space as the roadnet,
        // so they drop directly into CityFlowMap with no transform needed.
        if (Array.isArray(frame.vehicles)) {
          setVehicles(frame.vehicles);
        }

        // ── traffic light phase ───────────────────────────────────────────
        if (frame.phase)   setPhase(frame.phase);
        if (frame.trafficLights?.intersection_1_1 !== undefined) {
          setPhaseId(frame.trafficLights.intersection_1_1);
        }

        // ── metrics & step ────────────────────────────────────────────────
        if (frame.metrics) setMetrics(frame.metrics);
        if (frame.step !== undefined) setStep(frame.step);

      } catch (parseErr) {
        console.warn('[useLiveTraffic] Failed to parse frame:', parseErr);
      }
    };

    ws.onerror = () => {
      // onerror always fires before onclose; let onclose handle reconnect
    };

    ws.onclose = (event) => {
      if (!mountedRef.current) return;
      setConnected(false);
      if (!event.wasClean) {
        setError(`WS closed (code ${event.code}). Retrying in ${backoffRef.current}ms…`);
      }
      // Schedule reconnect with exponential backoff
      retryTimer.current = setTimeout(() => {
        if (mountedRef.current) {
          backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF_MS);
          connect();
        }
      }, backoffRef.current);
    };
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    connect();

    return () => {
      mountedRef.current = false;
      clearTimeout(retryTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;  // prevent reconnect on intentional unmount
        wsRef.current.close();
      }
    };
  }, [connect]);

  return { connected, vehicles, phase, phaseId, metrics, step, error };
}
