import { useState, useEffect } from 'react';

const API_BASE = 'http://localhost:8080/api';

export function useCityFlowData() {
  const [roadnet, setRoadnet] = useState(null);
  const [stats, setStats] = useState(null);
  const [totalFrames, setTotalFrames] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    async function init() {
      try {
        const [rRes, sRes, fRes] = await Promise.all([
          fetch(`${API_BASE}/replay-roadnet`),
          fetch(`${API_BASE}/stats`),
          fetch(`${API_BASE}/frames/count`),
        ]);

        if (!rRes.ok) throw new Error(`Roadnet fetch failed: ${rRes.statusText}`);

        setRoadnet(await rRes.json());
        setStats(await sRes.json());
        setTotalFrames((await fRes.json()).total);
        setLoading(false);
      } catch (err) {
        setError(err.message);
        setLoading(false);
      }
    }
    init();
  }, []);

  return { roadnet, stats, totalFrames, loading, error, API_BASE };
}
