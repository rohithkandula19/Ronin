import assert from 'node:assert/strict';
import { test } from 'node:test';

import { RenderError } from '../src/errors.js';
import { LABELS, SYMBOLS, board, labelFor, legend, symbolFor } from '../src/render.js';
import { STATES } from '../src/states.js';
import { newJob } from '../src/store.js';

test('every state has a symbol and a label', () => {
  for (const state of STATES) {
    assert.equal(typeof symbolFor(state), 'string', `no symbol for ${state}`);
    assert.equal(typeof labelFor(state), 'string', `no label for ${state}`);
  }
});

test('symbols are unique so the board is readable', () => {
  const symbols = STATES.map((state) => SYMBOLS[state]);
  assert.equal(new Set(symbols).size, symbols.length, `duplicate symbols in ${symbols.join('')}`);
});

test('the legend has one line per state', () => {
  assert.equal(legend().split('\n').length, STATES.length);
});

test('an unknown state is refused rather than rendered blank', () => {
  assert.throws(() => symbolFor('napping'), RenderError);
  assert.throws(() => labelFor('napping'), /add one to LABELS/);
});

test('the board shows one row per job plus a header and a rule', () => {
  const jobs = [newJob('job-1'), newJob('job-2', { state: 'running' })];
  const lines = board(jobs).split('\n');
  assert.equal(lines.length, jobs.length + 2);
  assert.match(lines.at(-1), /job-2/);
});

test('labels are declared for exactly the known states', () => {
  assert.deepEqual(Object.keys(LABELS).sort(), [...STATES].sort());
});
