import { AGENTS } from '../lib/runState';
import Badge from './Badge';

export default function PipelineView({ run, feed }) {
  return (
    <section className="panel" aria-labelledby="pipeline-title">
      <div className="panel-head">
        <h2 id="pipeline-title">Pipeline</h2>
        <span className="row" style={{ marginTop: 0 }}>
          <Badge status={run.status} />
          <span className="caption" style={{ margin: 0 }}>feed: {feed}</span>
        </span>
      </div>
      <div className="pipeline">
        {AGENTS.map(({ id, label }) => {
          const agent = run.agents[id];
          return (
            <div key={id} className={`agent ${agent.state}`}>
              <span className="name">{label}</span>
              <Badge status={agent.state} />
              <span className="meta">
                {agent.completed} done{agent.errors ? ` · ${agent.errors} error${agent.errors > 1 ? 's' : ''}` : ''}
              </span>
              {agent.lastError && <span className="meta error-text" title={agent.lastError}>{agent.lastError.slice(0, 80)}</span>}
            </div>
          );
        })}
      </div>
    </section>
  );
}
