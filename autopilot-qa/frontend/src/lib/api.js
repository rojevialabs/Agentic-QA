// REST + WebSocket client for the AutoPilot-QA backend (reached through the Vite proxy, see vite.config.js).
export const API = '/api';

async function request(path, options) {
  const response = await fetch(API + path, options);
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail ? JSON.stringify(detail.detail) : `HTTP ${response.status}`);
  }
  return response.json();
}

export const submitRequirement = (text, maxCases = null) =>
  request('/requirements', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(maxCases ? { text, max_cases: maxCases } : { text }),
  });
export const fetchRun = (runId) => request(`/runs/${runId}`);
export const fetchResults = (runId) => request(`/runs/${runId}/results`);
export const reexecute = (runId, threshold) => request(`/runs/${runId}/execute`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(threshold == null ? {} : { threshold }),
});

// Demo controls (test/demo only): seeded ShopLite changes and heal resets, see backend/demo.py.
export const fetchDemoState = () => request('/demo/state');
export const setSeededChanges = (changes) => request('/demo/seeded-changes', {
  method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ seeded_changes: changes }),
});
export const resetHealing = () => request('/demo/reset-healing', { method: 'POST' });
export const artifactUrl = (url) => (url ? API + url : null);

// Opens the live feed and reconnects after drops; the server replays history, and the reducer drops duplicates.
export function openFeed(runId, onMessage, onStatus = () => {}) {
  let socket;
  let closed = false;
  let retry;
  const connect = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    socket = new WebSocket(`${protocol}://${window.location.host}/ws/runs/${runId}`);
    socket.onopen = () => onStatus('live');
    socket.onmessage = (event) => onMessage(JSON.parse(event.data));
    socket.onclose = () => {
      if (closed) return;
      onStatus('reconnecting');
      retry = setTimeout(connect, 2000);
    };
  };
  connect();
  return () => {
    closed = true;
    clearTimeout(retry);
    socket?.close();
  };
}
