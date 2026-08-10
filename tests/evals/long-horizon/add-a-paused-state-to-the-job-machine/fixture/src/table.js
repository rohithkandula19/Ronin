/**
 * The transition table.
 *
 * One row per legal move. `guard` names an entry in src/guards.js; a row without
 * one is always allowed. Nothing here is code: bin/dot-export.js draws the diagram
 * from this array, and src/machine.js is the only reader that applies it.
 */

export const TRANSITIONS = Object.freeze([
  { from: 'queued', event: 'start', to: 'running' },
  { from: 'running', event: 'succeed', to: 'succeeded' },
  { from: 'running', event: 'fail', to: 'failed' },
  { from: 'failed', event: 'retry', to: 'queued', guard: 'attemptsRemaining' },
  { from: 'queued', event: 'cancel', to: 'cancelled' },
  { from: 'running', event: 'cancel', to: 'cancelled' },
  { from: 'failed', event: 'cancel', to: 'cancelled' },
]);

export function transitionFor(state, event) {
  return TRANSITIONS.find((row) => row.from === state && row.event === event);
}

export function eventsFrom(state) {
  return TRANSITIONS.filter((row) => row.from === state).map((row) => row.event);
}

export function statesReachableFrom(state) {
  return TRANSITIONS.filter((row) => row.from === state).map((row) => row.to);
}
