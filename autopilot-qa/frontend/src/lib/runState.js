// Folds the run's bus messages (spec §5) into what the dashboard shows. Pure, so it is unit-tested without a DOM.

export const AGENTS = [
  { id: 'orchestrator', label: 'Orchestrator' },
  { id: 'requirement_analyzer', label: 'Requirement Analyzer' },
  { id: 'test_generator', label: 'Test Generator' },
  { id: 'test_executor', label: 'Test Executor' },
  { id: 'triage_healer', label: 'Triage & Healer' },
];

const MAX_ATTEMPTS = 3; // orchestrator retries a task twice before escalating

export function initialRunState(runId) {
  return {
    runId,
    status: 'pending',
    agents: Object.fromEntries(AGENTS.map((a) => [a.id, { state: 'idle', active: 0, completed: 0, errors: 0, lastError: null }])),
    tasks: {},
    thoughts: [],
    decisions: [],
    final: null,
    seen: new Set(),
  };
}

const agentState = (agent) => (agent.active > 0 ? 'working' : agent.completed > 0 ? 'done' : 'idle');

export function runReducer(state, message) {
  if (state.seen.has(message.message_id)) return state;
  const next = { ...state, seen: new Set(state.seen).add(message.message_id) };
  const { type, from_agent: from, to_agent: to, task_id: taskId, payload = {} } = message;

  if (type === 'request' && next.agents[to]) {
    const agent = { ...next.agents[to], active: next.agents[to].active + (next.tasks[taskId] ? 0 : 1) };
    next.agents = { ...next.agents, [to]: { ...agent, state: 'working' } };
    next.tasks = { ...next.tasks, [taskId]: { agent: to, kind: payload.kind, attempt: payload.attempt ?? 1, status: 'working' } };
    // A request after the final result means the run was re-executed (POST /runs/{id}/execute).
    if (next.status !== 'running') {
      next.status = 'running';
      next.final = null;
    }
  }

  if (type === 'result' && to === 'dashboard' && payload.kind === 'run') {
    next.final = payload;
    next.status = payload.status;
    next.agents = { ...next.agents, orchestrator: { ...next.agents.orchestrator, state: 'done', completed: 1 } };
  } else if (type === 'result' && next.agents[from]) {
    const before = next.agents[from];
    const agent = { ...before, active: Math.max(0, before.active - 1), completed: before.completed + 1 };
    next.agents = { ...next.agents, [from]: { ...agent, state: agentState(agent) } };
    next.tasks = { ...next.tasks, [taskId]: { ...next.tasks[taskId], status: 'done' } };
  }

  if (type === 'error' && next.agents[from]) {
    const escalated = (payload.attempt ?? 1) >= MAX_ATTEMPTS;
    const before = next.agents[from];
    const agent = { ...before, errors: before.errors + 1, lastError: payload.error ?? null,
      active: escalated ? Math.max(0, before.active - 1) : before.active };
    next.agents = { ...next.agents, [from]: { ...agent, state: escalated ? 'escalated' : 'retrying' } };
    next.tasks = { ...next.tasks, [taskId]: { ...next.tasks[taskId], status: escalated ? 'escalated' : 'retrying', error: payload.error } };
  }

  if (type === 'thought') {
    const thought = { id: message.message_id, agent: from, taskId, text: payload.text ?? '', action: payload.action ?? {}, timestamp: message.timestamp };
    next.thoughts = [...next.thoughts, thought];
    if (typeof thought.action.applied === 'boolean' && thought.action.selector_key) {
      next.decisions = [...next.decisions, { ...thought.action, taskId, timestamp: message.timestamp }];
    }
  }
  return next;
}

export const reduceMessages = (runId, messages) => messages.reduce(runReducer, initialRunState(runId));
