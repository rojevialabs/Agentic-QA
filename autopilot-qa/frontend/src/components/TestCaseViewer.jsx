export default function TestCaseViewer({ testCases }) {
  return (
    <section className="panel" aria-labelledby="cases-title">
      <div className="panel-head"><h2 id="cases-title">Test cases</h2><span className="caption" style={{ margin: 0 }}>{testCases.length}</span></div>
      {testCases.length === 0 ? <p className="empty">Generated test cases appear after analysis.</p> : (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Type</th><th>Title</th><th>Steps</th><th>Expected result</th></tr></thead>
            <tbody>
              {testCases.map((c) => (
                <tr key={c.id}>
                  <td><span className="badge">{c.type}</span></td>
                  <td>{c.title}</td>
                  <td><ol>{c.steps.map((s, i) => <li key={i}>{s}</li>)}</ol></td>
                  <td>{c.expected_result}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
