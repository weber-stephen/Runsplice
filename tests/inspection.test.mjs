import assert from 'node:assert/strict';
import test from 'node:test';
import { initialSequence, runKind, runGroups, valuePreview, comparisonPresentation } from '../agentdbg/web/assets/inspection.mjs';

const recorded = { final_answer: 'unpaid' };
const fork = (steps = [], status = 'replayed') => ({ run: { parent_id: 'original', fork_step: 2, provider: 'example', status }, steps });

test('includes every replay descendant under its recording in source order', () => {
  const runs = [
    { id: 'recording', parent_id: null }, { id: 'test', parent_id: 'recording' },
    { id: 'replay', parent_id: 'test' }, { id: 'latest-test', parent_id: 'recording' },
    { id: 'replay-again', parent_id: 'replay' }, { id: 'other-recording', parent_id: null }
  ];
  assert.deepEqual(runGroups(runs).map(group => group.map(run => run.id)), [
    ['other-recording'], ['recording', 'latest-test', 'test', 'replay', 'replay-again']
  ]);
  assert.equal(runGroups(runs).flat().length, runs.length);
  assert.equal(runs[0].id, 'recording');
});

test('does not hide runs whose parent is missing or form a cycle', () => {
  const runs = [{ id: 'orphan', parent_id: 'missing' }, { id: 'a', parent_id: 'b' }, { id: 'b', parent_id: 'a' }];
  assert.deepEqual(runGroups(runs).flat().map(run => run.id).sort(), ['a', 'b', 'orphan']);
  assert.deepEqual(runGroups([]), []);
});

test('inherited overrides do not create a new response-edit moment', () => {
  const steps = [{ sequence: 2, origin: 'overridden', status: 'ok' }, { sequence: 4, operation: 'system', origin: 'blocked', status: 'blocked' }];
  const replay = { run: { parent_id: 'test', fork_step: 4, provider: 'example', status: 'blocked' }, steps };
  assert.equal(runKind(replay), 'Replay attempt');
  const presentation = comparisonPresentation({ fork_step: 4, outcome: 'blocked', first_change: { sequence: 4, kind: 'request' } }, recorded, replay);
  assert.equal(presentation.kind, 'Replay attempt');
  assert.deepEqual(presentation.moments[0].titles, ['Replay diagnostic', 'Execution stopped']);
  assert.equal(runKind({ ...replay, run: { ...replay.run, status: 'replayed' } }), 'Unchanged replay');
  assert.equal(runKind({ ...replay, run: { ...replay.run, fork_step: 2 } }), 'Response test');
});

test('opens the edit, diagnosis, failure, or final step in that order', () => {
  const steps = [{ sequence: 1, status: 'ok' }, { sequence: 2, status: 'ok', failure_hint: 'stale lookup' }, { sequence: 3, status: 'error' }];
  assert.equal(initialSequence(steps, 1), 1);
  assert.equal(initialSequence(steps), 2);
  assert.equal(initialSequence(steps.filter(step => !step.failure_hint)), 3);
  assert.equal(initialSequence(steps.slice(0, 1)), 1);
  assert.equal(initialSequence([]), undefined);
});

test('combines moments at the same step and separates later changed requests', () => {
  const result = comparisonPresentation({ fork_step: 2, first_change: { sequence: 2, kind: 'response' }, first_changed_request: 4, outcome: 'blocked' },
    recorded, fork([{ sequence: 2, origin: 'overridden' }, { sequence: 4, operation: 'model', status: 'blocked' }], 'blocked'));
  assert.deepEqual(result.moments.map(moment => [moment.sequence, moment.titles]), [
    [2, ['Response edited', 'First observed difference']], [4, ['First changed request', 'Execution stopped']]
  ]);
  assert.equal(result.testAnswer, undefined);
  assert.match(result.answerUnavailable, /No new answer/);
});

test('does not attribute earlier live differences or confirm unconfirmed answers', () => {
  const result = comparisonPresentation({ fork_step: 2, first_change: { sequence: 1, kind: 'request' }, first_changed_request: 1,
    outcome: 'override_not_applied', answer_comparison: { original: 'a', fork: 'unverified' } }, recorded,
  { run: { parent_id: 'original', provider: 'claude', status: 'override_not_applied' }, steps: [] });
  assert.equal(result.moments[0].titles[0], 'Proposed response');
  assert.match(result.moments[1].descriptions.join(' '), /cannot be attributed/);
  assert.equal(result.testAnswer, undefined);
  assert.match(result.answerUnavailable, /not confirmed/);
  const failed = comparisonPresentation({ fork_step: 2, outcome: 'error', override_applied: false }, recorded,
    { run: { parent_id: 'original', provider: 'claude', status: 'error' }, steps: [] });
  assert.equal(failed.moments[0].titles[0], 'Proposed response');
  assert.equal(failed.testAnswer, undefined);
});

test('distinguishes unchanged replay and environment failures from response tests', () => {
  assert.equal(runKind(fork()), 'Unchanged replay');
  assert.equal(runKind(fork([], 'blocked')), 'Replay attempt');
  const result = comparisonPresentation({ fork_step: 1, outcome: 'blocked', first_change: { sequence: 1, kind: 'request' } }, recorded,
    fork([{ sequence: 1, operation: 'system', status: 'blocked' }], 'blocked'));
  assert.equal(result.moments.length, 1);
  assert.match(result.moments[0].descriptions.join(' '), /recorded diagnostic/);
  assert.equal(result.moments[0].titles.includes('First changed request'), false);
  assert.equal(result.moments[0].titles.includes('First observed difference'), false);
});

test('does not infer an answer when captures or successful outcome are missing', () => {
  for (const outcome of ['error', 'running', 'replayed_error', 'blocked']) {
    const result = comparisonPresentation({ outcome, answer_comparison: { fork: 'stale' } }, recorded, fork([], outcome));
    assert.equal(result.testAnswer, undefined);
  }
  assert.equal(comparisonPresentation({ outcome: 'replayed' }, recorded, fork()).testAnswer, undefined);
  assert.equal(comparisonPresentation({ outcome: 'replayed', answer_comparison: { fork: false } }, recorded, fork()).testAnswer, false);
  assert.equal(valuePreview(undefined), 'Unavailable');
  assert.equal(valuePreview(null), 'null');
  assert.equal(valuePreview('<img src=x onerror=alert(1)>'), '"<img src=x onerror=alert(1)>"');
  assert.equal(valuePreview('x'.repeat(1000)).length, 500);
});
