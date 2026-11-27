import { useCallback, useEffect, useState } from 'react';
import { fetchDemoState, resetHealing, setSeededChanges } from '../lib/api';

// Drives the whole demo from the browser: switch a seeded ShopLite change, reset heals, re-execute at any threshold.
export default function DemoControls({ runId, busy, onReexecute, refreshKey }) {
  const [state, setState] = useState(null);
  const [choice, setChoice] = useState('');
  const [threshold, setThreshold] = useState('');
  const [message, setMessage] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const next = await fetchDemoState();
      setState(next);
      setThreshold((current) => (current === '' ? String(next.threshold) : current));
      setChoice((current) => current || (next.seeded_changes?.[0] ?? 'none'));
    } catch (err) {
      setMessage(`Demo controls unavailable: ${err.message}`);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh, refreshKey]);

  async function act(label, fn) {
    setMessage(null);
    try {
      await fn();
      setMessage(label);
      await refresh();
    } catch (err) {
      setMessage(`Failed: ${err.message}`);
    }
  }

  const byLabel = (label) => (state?.changes ?? []).filter((c) => c.label === label);
  const live = state?.seeded_changes;
  const value = Number(threshold);
  const validThreshold = threshold !== '' && value >= 0 && value <= 1;

  return (
    <details className="panel" open>
      <summary><h2 style={{ display: 'inline' }}>Demo controls</h2>
        <span className="caption"> · ShopLite now: {live == null ? 'not reachable' : live.length ? live.join(', ') : 'normal app'}</span>
      </summary>
      <div className="row">
        <label htmlFor="demo-change" className="caption" style={{ margin: 0 }}>UI change on ShopLite</label>
        <select id="demo-change" value={choice} onChange={(e) => setChoice(e.target.value)} disabled={busy}>
          <option value="none">Normal app (no change)</option>
          <optgroup label="Benign UI changes (should heal)">
            {byLabel('benign_ui_change').map((c) => <option key={c.id} value={c.id}>{c.description}</option>)}
          </optgroup>
          <optgroup label="Real defects (must be reported)">
            {byLabel('real_defect').map((c) => <option key={c.id} value={c.id}>{c.description}</option>)}
          </optgroup>
        </select>
        <button type="button" disabled={busy || !state}
          onClick={() => act('Change applied', () => setSeededChanges(choice === 'none' ? [] : [choice]))}>Apply change</button>
        <button type="button" disabled={busy} onClick={() => act('Healing reset: selector map back to the seed', resetHealing)}>
          Reset healing</button>
      </div>
      <div className="row">
        <label htmlFor="demo-threshold" className="caption" style={{ margin: 0 }}>Healing threshold</label>
        <input id="demo-threshold" type="number" min="0" max="1" step="0.01" value={threshold}
          onChange={(e) => setThreshold(e.target.value)} style={{ width: 80 }} />
        <button type="button" className="primary" disabled={busy || !runId || !validThreshold}
          onClick={() => onReexecute(value)}>Re-execute with this threshold</button>
        {!runId && <span className="caption" style={{ margin: 0 }}>Run a requirement first.</span>}
      </div>
      {state && (
        <p className="caption" style={{ marginBottom: 0 }}>
          Selector maps:{' '}
          {state.selector_maps.map((m) => (
            <span key={`${m.version}-${m.status}-${m.healed.join()}`} className="badge" style={{ marginRight: 6 }}>
              v{m.version} {m.status}{m.healed.length ? ` · ${m.healed.join('; ')}` : ''}
            </span>
          ))}
        </p>
      )}
      {message && <p className="caption" role="status" style={{ marginBottom: 0 }}>{message}</p>}
    </details>
  );
}
