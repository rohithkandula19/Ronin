import assert from 'node:assert/strict';
import { test } from 'node:test';

import { GUARDS, guardNamed } from '../src/guards.js';
import { newJob } from '../src/store.js';

test('attemptsRemaining allows a job with attempts left', () => {
  const job = newJob('job-a', { state: 'failed', attempts: 1 });
  assert.equal(GUARDS.attemptsRemaining(job, { maxAttempts: 3 }), true);
});

test('attemptsRemaining refuses with a reason a human can read', () => {
  const job = newJob('job-b', { state: 'failed', attempts: 3 });
  const verdict = GUARDS.attemptsRemaining(job, { maxAttempts: 3 });
  assert.equal(typeof verdict, 'string');
  assert.match(verdict, /no attempts left/);
});

test('an unknown guard name is a clear error', () => {
  assert.throws(() => guardNamed('nope'), /no guard named 'nope'/);
});
