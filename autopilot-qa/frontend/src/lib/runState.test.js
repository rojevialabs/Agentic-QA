import { describe, expect, test } from 'vitest';
import { AGENTS, initialRunState, reduceMessages, runReducer } from './runState';

const msg = (type, from_agent, to_agent, payload = {}, task_id = 't1') => ({
  message_id: Math.random().toString(36), run_id: 'run-1', type, from_agent, to_agent, task_id, payload,
  timestamp: '2026-09-27T10:00:00.000Z',
});

describe('runReducer', () => {
  test('starts with every agent idle', () => {
    const state = initialRunState('run-1');
    expect(Object.keys(state.agents)).toEqual(AGENTS.map((a) => a.id));
    expect(Object.values(state.agents).every((a) => a.state === 'idle')).toBe(true);
    expect(state.status).toBe('pending');
  });

  test('a request marks the agent working and a result marks it done', () => {
    let state = runReducer(initialRunState('run-1'), msg('request', 'orchestrator', 'test_generator', { kind: 'generate', attempt: 1 }));
    expect(state.agents.test_generator).toMatchObject({ state: 'working', active: 1 });
    expect(state.status).toBe('running');
    state = runReducer(state, msg('result', 'test_generator', 'orchestrator', { kind: 'generate', attempt: 1 }));
    expect(state.agents.test_generator).toMatchObject({ state: 'done', active: 0, completed: 1 });
    expect(state.tasks.t1.status).toBe('done');
  });

  test('errors are shown as retries and the third one as escalated', () => {
    const state = reduceMessages('run-1', [
      msg('request', 'orchestrator', 'test_executor', { attempt: 1 }),
      msg('error', 'test_executor', 'orchestrator', { attempt: 1, error: 'RuntimeError: boom' }),
      msg('request', 'orchestrator', 'test_executor', { attempt: 2 }),
      msg('error', 'test_executor', 'orchestrator', { attempt: 2, error: 'RuntimeError: boom' }),
    ]);
    expect(state.tasks.t1.status).toBe('retrying');
    expect(state.agents.test_executor).toMatchObject({ state: 'retrying', errors: 2, lastError: 'RuntimeError: boom' });
    const escalated = runReducer(state, msg('error', 'test_executor', 'orchestrator', { attempt: 3, error: 'x' }));
    expect(escalated.tasks.t1.status).toBe('escalated');
    expect(escalated.agents.test_executor.state).toBe('escalated');
  });

  test('thoughts are collected in order and heal decisions are extracted', () => {
    const state = reduceMessages('run-1', [
      msg('thought', 'triage_healer', 'dashboard', { text: 'Triage: locator_missing', action: { triage: {} } }),
      msg('thought', 'triage_healer', 'dashboard', { text: 'THOUGHT: renamed to Add to Bag', action: {} }),
      msg('thought', 'triage_healer', 'dashboard', {
        text: 'ACTION: applied', action: { selector_key: 'catalog.addToCartButton', new_locator: '#add-to-bag-btn',
          confidence: 0.9, threshold: 0.8, applied: true, verified: true } }),
    ]);
    expect(state.thoughts.map((t) => t.text)).toEqual(['Triage: locator_missing', 'THOUGHT: renamed to Add to Bag', 'ACTION: applied']);
    expect(state.decisions).toEqual([expect.objectContaining({ new_locator: '#add-to-bag-btn', applied: true, confidence: 0.9 })]);
  });

  test('the final dashboard result sets the run status and counts', () => {
    const state = runReducer(initialRunState('run-1'),
      msg('result', 'orchestrator', 'dashboard', { kind: 'run', status: 'completed', counts: { pass: 3 } }, 'run'));
    expect(state.status).toBe('completed');
    expect(state.final.counts).toEqual({ pass: 3 });
    expect(state.agents.orchestrator.state).toBe('done');
  });

  test('a re-execution after the run finished shows the run as running again', () => {
    const state = reduceMessages('run-1', [
      msg('result', 'orchestrator', 'dashboard', { kind: 'run', status: 'completed', counts: {} }, 'run'),
      msg('request', 'orchestrator', 'test_executor', { kind: 'execute', attempt: 1 }, 't9'),
    ]);
    expect(state.status).toBe('running');
    expect(state.final).toBeNull();
  });

  test('duplicate messages (replay after reconnect) are ignored', () => {
    const m = msg('thought', 'triage_healer', 'dashboard', { text: 'once', action: {} });
    expect(reduceMessages('run-1', [m, m]).thoughts).toHaveLength(1);
  });
});
