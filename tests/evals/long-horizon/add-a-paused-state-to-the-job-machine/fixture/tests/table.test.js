import assert from 'node:assert/strict';
import { test } from 'node:test';

import { GUARDS } from '../src/guards.js';
import { STATES, isFinished, isState } from '../src/states.js';
import { EVENTS, isEvent } from '../src/events.js';
import { TRANSITIONS, eventsFrom, transitionFor } from '../src/table.js';

test('every transition uses known states and events', () => {
  for (const row of TRANSITIONS) {
    assert.ok(isState(row.from), `unknown from-state '${row.from}'`);
    assert.ok(isState(row.to), `unknown to-state '${row.to}'`);
    assert.ok(isEvent(row.event), `unknown event '${row.event}'`);
  }
});

test('every named guard exists', () => {
  for (const row of TRANSITIONS) {
    if (row.guard !== undefined) {
      assert.equal(typeof GUARDS[row.guard], 'function', `missing guard '${row.guard}'`);
    }
  }
});

test('no state and event pair appears twice', () => {
  const seen = new Set();
  for (const row of TRANSITIONS) {
    const key = `${row.from}/${row.event}`;
    assert.ok(!seen.has(key), `duplicate row for ${key}`);
    seen.add(key);
  }
});

test('finished states have no way out', () => {
  for (const state of STATES.filter(isFinished)) {
    assert.deepEqual(eventsFrom(state), [], `${state} should be final`);
  }
});

test('every event is used by at least one transition', () => {
  for (const event of EVENTS) {
    assert.ok(
      TRANSITIONS.some((row) => row.event === event),
      `event '${event}' is declared but never usable`,
    );
  }
});

test('every non-finished state can be left somehow', () => {
  for (const state of STATES.filter((candidate) => !isFinished(candidate))) {
    assert.ok(eventsFrom(state).length > 0, `${state} is a dead end`);
  }
});

test('lookups miss cleanly', () => {
  assert.equal(transitionFor('succeeded', 'start'), undefined);
});
