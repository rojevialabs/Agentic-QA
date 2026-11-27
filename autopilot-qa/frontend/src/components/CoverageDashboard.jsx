import { useEffect, useState } from 'react';
import { CartesianGrid, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis } from 'recharts';
import Badge from './Badge';

const TOKEN_NAMES = ['accent', 'surface', 'grid', 'axis', 'muted', 'ink', 'ink-2'];

// SVG presentation attributes cannot use CSS variables, so resolve the theme tokens (and follow scheme changes).
function useTokens() {
  const read = () => {
    const style = getComputedStyle(document.documentElement);
    return Object.fromEntries(TOKEN_NAMES.map((n) => [n, style.getPropertyValue(`--${n}`).trim() || '#888']));
  };
  const [tokens, setTokens] = useState(read);
  useEffect(() => {
    const media = window.matchMedia?.('(prefers-color-scheme: dark)');
    const update = () => setTokens(read());
    media?.addEventListener?.('change', update);
    return () => media?.removeEventListener?.('change', update);
  }, []);
  return tokens;
}

function Tile({ status, label, value }) {
  return (
    <div className="tile">
      <div className="value">{value}</div>
      <div className="label"><Badge status={status} label={label} /></div>
    </div>
  );
}

function DecisionTooltip({ active, payload }) {
  if (!active || !payload?.length) return null;
  const d = payload[0].payload;
  return (
    <div className="tooltip">
      <div><strong>#{d.n} {d.key}</strong></div>
      <div className="mono">{d.locator ?? 'no replacement'}</div>
      <div>confidence {d.y.toFixed(2)}{d.llm != null && d.llm !== d.y ? ` (model said ${d.llm.toFixed(2)})` : ''}</div>
      <div>{d.applied ? 'applied' : 'not applied'}{d.verification ? ` · ${d.verification}` : ''}</div>
    </div>
  );
}

export default function CoverageDashboard({ report, decisions }) {
  const tokens = useTokens();
  const executions = report?.executions ?? [];
  const coverage = report?.coverage?.[0];
  const count = (status) => executions.filter((e) => e.status === status).length;
  const healed = executions.filter((e) => e.healing).length;
  const threshold = decisions.at(-1)?.threshold ?? 0.8; // the current setting; each decision keeps its own below
  const points = decisions.map((d, i) => ({
    n: i + 1, y: d.confidence ?? 0, llm: d.llm_confidence, applied: d.applied, key: d.selector_key, threshold: d.threshold ?? 0.8,
    locator: d.new_locator, verification: d.verification,
  }));
  const marker = (filled) => function Marker({ cx, cy }) {
    return <circle cx={cx} cy={cy} r={5} fill={filled ? tokens.accent : tokens.surface} stroke={tokens.accent} strokeWidth={2} />;
  };

  return (
    <section className="panel" aria-labelledby="coverage-title">
      <div className="panel-head">
        <h2 id="coverage-title">Coverage &amp; healing</h2>
        {coverage && <span className="caption" style={{ margin: 0 }}>
          requirement {coverage.has_test ? 'covered' : 'not covered'} · last status {coverage.last_status}</span>}
      </div>
      <div className="tiles">
        <Tile status="pass" label="Passed" value={count('pass')} />
        <Tile status="fail" label="Failed" value={count('fail')} />
        <Tile status="flaky" label="Flaky" value={count('flaky')} />
        <Tile status="healed" label="Healed re-runs" value={healed} />
      </div>

      <h2>Healing confidence per decision</h2>
      {points.length === 0 ? <p className="empty">No healing attempts in this run.</p> : (
        <>
          <p className="caption">Each dot is one healing attempt; it is applied only when verified and at or above the threshold line.</p>
          <div className="legend" aria-hidden="true">
            <span><span className="filled" /> applied</span>
            <span><span className="ring" /> not applied</span>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <ScatterChart margin={{ top: 8, right: 16, bottom: 4, left: -16 }}>
              <CartesianGrid vertical={false} stroke={tokens.grid} />
              <XAxis type="number" dataKey="n" name="attempt" allowDecimals={false} domain={[0.5, Math.max(points.length, 1) + 0.5]}
                tick={{ fill: tokens.muted, fontSize: 12 }} stroke={tokens.axis} tickLine={false} />
              <YAxis type="number" dataKey="y" name="confidence" domain={[0, 1]} ticks={[0, 0.5, threshold, 1]}
                tick={{ fill: tokens.muted, fontSize: 12 }} stroke={tokens.axis} tickLine={false} />
              <ReferenceLine y={threshold} stroke={tokens.ink} strokeDasharray="4 3"
                label={{ value: `threshold ${threshold.toFixed(2)}`, position: 'insideTopRight', fill: tokens['ink-2'], fontSize: 12 }} />
              <Tooltip content={<DecisionTooltip />} cursor={false} />
              <Scatter data={points.filter((p) => p.applied)} shape={marker(true)} isAnimationActive={false} />
              <Scatter data={points.filter((p) => !p.applied)} shape={marker(false)} isAnimationActive={false} />
            </ScatterChart>
          </ResponsiveContainer>
          <div className="table-wrap">
            <table aria-label="Healing decisions">
              <thead><tr><th>#</th><th>Element</th><th>New locator</th><th>Confidence</th><th>Threshold</th><th>Outcome</th></tr></thead>
              <tbody>
                {points.map((p) => (
                  <tr key={p.n}>
                    <td>{p.n}</td><td>{p.key}</td><td className="mono">{p.locator ?? '—'}</td><td>{p.y.toFixed(2)}</td><td>{p.threshold.toFixed(2)}</td>
                    <td><Badge status={p.applied ? 'healed' : 'fail'} label={p.applied ? 'Applied' : 'Not applied'} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
