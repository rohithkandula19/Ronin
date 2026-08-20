import assert from 'node:assert/strict';
import { test } from 'node:test';

import { GuardRejected, InvalidTransition } from '../src/errors.js';
import { apply, canApply, defaultContext, drive } from '../src/machine.js';
import { newJob } from '../src/store.js';

test('a queued job starts', () => {
  assert.equal(apply(newJob('job-1'), 'start').state, 'running');
});

test('an impossible event is an InvalidTransition', () => {
  assert.throws(() => apply(newJob('job-1'), 'succeed'), InvalidTransition);
});

test('a finished job refuses everything', () => {
  const done = newJob('job-1', { state: 'succeeded' });
  assert.throws(() => apply(done, 'start'), /finished state/);
});

test('retry increments the attempt count', () => {
  const failed = newJob('job-1', { state: 'failed', attempts: 0 });
  assert.equal(apply(failed, 'retry').attempts, 1);
});

test('retry is refused once the attempts are gone', () => {
  const spent = newJob('job-1', { state: 'failed', attempts: 3 });
  assert.throws(() => apply(spent, 'retry', defaultContext({ maxAttempts: 3 })), GuardRejected);
  assert.equal(canApply(spent, 'retry', defaultContext({ maxAttempts: 3 })), false);
});

test('drive records every step it managed', () => {
  const result = drive(newJob('job-1'), ['start', 'succeed']);
  assert.equal(result.error, undefined);
  assert.equal(result.job.state, 'succeeded');
  assert.deepEqual(
    result.history.map((entry) => `${entry.from}->${entry.to}`),
    ['queued->running', 'running->succeeded'],
  );
});

test('drive stops at the first refusal and keeps the reason', () => {
  const result = drive(newJob('job-1'), ['start', 'retry', 'succeed']);
  assert.ok(result.error instanceof InvalidTransition);
  assert.equal(result.job.state, 'running');
  assert.match(result.history.at(-1).refused, /cannot handle 'retry'/);
});
