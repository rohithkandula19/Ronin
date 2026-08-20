/**
 * Jobs are plain frozen objects; every transition produces a new one, so the
 * history in src/history.js can hold the old ones without copying.
 */

import { DEFAULT_PRIORITY } from './policy/priority.js';
import { INITIAL } from './states.js';

let counter = 0;

export function newJob(id, options = {}) {
  counter += 1;
  return Object.freeze({
    id: id ?? `job-${counter}`,
    state: options.state ?? INITIAL,
    priority: options.priority ?? DEFAULT_PRIORITY,
    attempts: options.attempts ?? 0,
    label: options.label ?? '',
  });
}

export function withState(job, state, extra = {}) {
  return Object.freeze({ ...job, ...extra, state });
}

export function resetCounter() {
  counter = 0;
}
