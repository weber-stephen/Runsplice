import assert from 'node:assert/strict';
import test from 'node:test';
import { changedFields } from '../agentdbg/web/assets/response_diff.mjs';

test('finds nested object and array changes in path order', () => {
  assert.deepEqual(changedFields({ result: { status: 'unpaid' }, items: [1, 2] }, { result: { status: 'paid' }, items: [1, 3] }).changes,
    [{ path: '$.items[1]', recorded: '2', test: '3' }, { path: '$.result.status', recorded: '"unpaid"', test: '"paid"' }]);
});

test('distinguishes missing values from null and unchanged data', () => {
  assert.deepEqual(changedFields({ value: null }, {}).changes, [{ path: '$.value', recorded: 'null', test: 'Missing' }]);
  assert.deepEqual(changedFields({ value: null }, { value: null }), { changes: [], more: false });
});

test('bounds displayed differences and long values', () => {
  const result = changedFields({ text: 'a'.repeat(200), items: [0, 0, 0, 0, 0, 0] }, { text: 'b'.repeat(200), items: [1, 1, 1, 1, 1, 1] });
  assert.equal(result.changes.length, 5);
  assert.equal(result.more, true);
  assert.ok(result.changes.every(change => change.recorded.length <= 120));
});
