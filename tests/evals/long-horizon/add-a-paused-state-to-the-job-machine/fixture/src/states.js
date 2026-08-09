/**
 * The states a job can be in.
 *
 * FINISHED is the set with no outgoing transitions; the scheduler stops looking at
 * a job once it reaches one of them.
 */

export const STATES = Object.freeze([
  'queued',
  'running',
  'succeeded',
  'failed',
  'cancelled',
]);

export const FINISHED = Object.freeze(['succeeded', 'cancelled']);

export const INITIAL = 'queued';

export function isState(value) {
  return STATES.includes(value);
}

export function isFinished(state) {
  return FINISHED.includes(state);
}

export function assertState(state) {
  if (!isState(state)) {
    throw new Error(`unknown state '${state}'; known states are ${STATES.join(', ')}`);
  }
  return state;
}
