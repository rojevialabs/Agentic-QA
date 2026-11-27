import { artifactUrl } from '../lib/api';
import Badge from './Badge';

const firstLine = (text) => (text ? text.trim().split('\n')[0] : '');

export default function ExecutionResults({ executions, scripts, testCases, onReexecute, busy }) {
  const caseByScript = Object.fromEntries(scripts.map((s) => [s.id, testCases.find((c) => c.id === s.test_case_id)]));
  return (
    <section className="panel" aria-labelledby="results-title">
      <div className="panel-head">
        <h2 id="results-title">Execution results</h2>
        <button type="button" onClick={onReexecute} disabled={busy || scripts.length === 0}>Re-execute</button>
      </div>
      {executions.length === 0 ? <p className="empty">Results appear as scripts run.</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Test case</th><th>Status</th><th>Map</th><th>Attempts</th><th>Detail</th><th>Screenshot</th></tr></thead>
            <tbody>
              {executions.map((e) => (
                <tr key={e.id}>
                  <td>{caseByScript[e.script_id]?.title ?? <code>{e.script_id}</code>}</td>
                  <td>
                    <Badge status={e.status} />{' '}
                    {e.healing && <Badge status="healed" label={`Healed → ${e.healing.new_locator}`} />}
                  </td>
                  <td>v{e.selector_map_version}</td>
                  <td>{e.attempts}</td>
                  <td className="mono">{firstLine(e.failure?.error)}</td>
                  <td>{e.screenshot_url
                    ? <a href={artifactUrl(e.screenshot_url)} target="_blank" rel="noreferrer"><img className="thumb" src={artifactUrl(e.screenshot_url)} alt="Failure screenshot" /></a>
                    : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
