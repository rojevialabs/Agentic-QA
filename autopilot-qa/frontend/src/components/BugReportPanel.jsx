import Badge from './Badge';

export default function BugReportPanel({ bugReports }) {
  return (
    <section className="panel" aria-labelledby="bugs-title">
      <div className="panel-head"><h2 id="bugs-title">Bug reports</h2><span className="caption" style={{ margin: 0 }}>{bugReports.length}</span></div>
      {bugReports.length === 0 ? <p className="empty">No bug reports: failures that heal cleanly never become one.</p> : (
        bugReports.map((b) => (
          <article key={b.id} className="bug">
            <div className="row" style={{ marginTop: 0 }}>
              <Badge status={b.severity} label={`${b.severity} severity`} />
              <span className="badge">root cause: {b.root_cause}</span>
            </div>
            {b.notes && <p style={{ margin: 0 }}>{b.notes}</p>}
            <dl>
              <dt>Expected</dt><dd>{b.expected}</dd>
              <dt>Actual</dt><dd className="pre">{b.actual}</dd>
              <dt>Steps</dt><dd><ol style={{ margin: 0, paddingLeft: 18 }}>{b.steps_to_reproduce.map((s, i) => <li key={i}>{s}</li>)}</ol></dd>
            </dl>
          </article>
        ))
      )}
    </section>
  );
}
