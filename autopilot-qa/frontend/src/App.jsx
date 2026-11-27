import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import BugReportPanel from './components/BugReportPanel';
import CoverageDashboard from './components/CoverageDashboard';
import DemoControls from './components/DemoControls';
import ExecutionResults from './components/ExecutionResults';
import PipelineView from './components/PipelineView';
import ReasoningTrace from './components/ReasoningTrace';
import RequirementInput from './components/RequirementInput';
import TestCaseViewer from './components/TestCaseViewer';
import { fetchResults, fetchRun, openFeed, reexecute, submitRequirement } from './lib/api';
import { initialRunState, runReducer } from './lib/runState';

const ACTIVE = new Set(['pending', 'running']);
const runFromHash = () => new URLSearchParams(window.location.hash.slice(1)).get('run');

function reducer(state, action) {
  if (action.type === 'reset') return initialRunState(action.runId);
  return runReducer(state, action.message);
}

export default function App() {
  const [runId, setRunId] = useState(runFromHash);
  const [run, dispatch] = useReducer(reducer, runId, initialRunState);
  const [report, setReport] = useState(null);
  const [executions, setExecutions] = useState([]);
  const [feed, setFeed] = useState('idle');
  const refreshTimer = useRef(null);

  const refresh = useCallback((id) => {
    clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(async () => {
      try {
        const [nextReport, nextResults] = await Promise.all([fetchRun(id), fetchResults(id)]);
        setReport(nextReport);
        setExecutions(nextResults);
      } catch { /* the next result message triggers another refresh */ }
    }, 300);
  }, []);

  useEffect(() => {
    if (!runId) return undefined;
    dispatch({ type: 'reset', runId });
    setReport(null);
    setExecutions([]);
    refresh(runId);
    const close = openFeed(runId, (message) => {
      dispatch({ type: 'message', message });
      if (message.type === 'result') refresh(runId);
    }, setFeed);
    return () => { close(); setFeed('idle'); };
  }, [runId, refresh]);

  async function submit(text, maxCases) {
    const { run_id: id } = await submitRequirement(text, maxCases);
    window.location.hash = `run=${id}`;
    setRunId(id);
  }

  const busy = ACTIVE.has(run.status) && Boolean(runId);
  return (
    <main className="app">
      <header className="header">
        <div>
          <h1>AutoPilot-QA</h1>
          <p>Requirement → test cases → Playwright scripts → execution → explainable self-healing.</p>
        </div>
        {runId && <code>run {runId}</code>}
      </header>
      <RequirementInput onSubmit={submit} busy={busy} />
      <DemoControls runId={runId} busy={busy} refreshKey={`${run.status}-${run.decisions.length}`}
        onReexecute={async (threshold) => { await reexecute(runId, threshold); }} />
      {runId && (
        <>
          <PipelineView run={run} feed={feed} />
          <div className="grid-2">
            <ReasoningTrace thoughts={run.thoughts} />
            <CoverageDashboard report={report} decisions={run.decisions} />
          </div>
          <TestCaseViewer testCases={report?.test_cases ?? []} />
          <ExecutionResults executions={executions} scripts={report?.scripts ?? []} testCases={report?.test_cases ?? []}
            busy={busy} onReexecute={async () => { await reexecute(runId); }} />
          <BugReportPanel bugReports={report?.bug_reports ?? []} />
        </>
      )}
    </main>
  );
}
