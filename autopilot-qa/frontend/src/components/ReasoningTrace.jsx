import { useEffect, useRef } from 'react';
import { AGENTS } from '../lib/runState';
import Badge from './Badge';

const LABELS = Object.fromEntries(AGENTS.map((a) => [a.id, a.label]));
const time = (iso) => new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

// The healer's decision: old -> new locator, and its confidence against the auto-apply threshold.
export function DecisionCard({ decision }) {
  const confidence = decision.confidence ?? 0;
  const threshold = decision.threshold ?? 0.8;
  return (
    <div className="decision" aria-label="Healing decision">
      <div className="row" style={{ marginTop: 0, justifyContent: 'space-between' }}>
        <strong>{decision.selector_key}</strong>
        <Badge status={decision.applied ? 'healed' : 'fail'} label={decision.applied ? 'Applied' : 'Not applied'} />
      </div>
      <div className="swap">
        {decision.broken_selector ?? '?'} → {decision.new_locator ?? 'no replacement'}
      </div>
      <div>
        <div className="meter" role="img" aria-label={`confidence ${confidence.toFixed(2)}, threshold ${threshold.toFixed(2)}`}>
          <div className="fill" style={{ width: `${confidence * 100}%` }} />
          <div className="threshold" style={{ left: `calc(${threshold * 100}% - 1px)` }} />
        </div>
        <div className="meter-labels">
          <span>confidence {confidence.toFixed(2)}{decision.llm_confidence != null && decision.llm_confidence !== confidence
            ? ` (model said ${decision.llm_confidence.toFixed(2)})` : ''}</span>
          <span>threshold {threshold.toFixed(2)}</span>
        </div>
      </div>
      {decision.verification && <span className="caption" style={{ margin: 0 }}>verification: {decision.verification}</span>}
    </div>
  );
}

export default function ReasoningTrace({ thoughts }) {
  const end = useRef(null);
  useEffect(() => { end.current?.scrollIntoView?.({ block: 'nearest' }); }, [thoughts.length]);

  return (
    <section className="panel" aria-labelledby="trace-title">
      <div className="panel-head">
        <h2 id="trace-title">Reasoning trace</h2>
        <span className="caption" style={{ margin: 0 }}>{thoughts.length} lines</span>
      </div>
      {thoughts.length === 0 ? <p className="empty">Agent reasoning streams here while the run is in progress.</p> : (
        <div className="trace" aria-live="polite">
          {thoughts.map((t) => (
            <div key={t.id}>
              <div className={`line ${t.text.startsWith('THOUGHT:') ? 'thought' : ''}`}>
                <time dateTime={t.timestamp}>{time(t.timestamp)}</time>
                <span><span className="who">{LABELS[t.agent] ?? t.agent}</span><span className="text">{t.text}</span></span>
              </div>
              {typeof t.action?.applied === 'boolean' && t.action.selector_key && <DecisionCard decision={t.action} />}
            </div>
          ))}
          <div ref={end} />
        </div>
      )}
    </section>
  );
}
