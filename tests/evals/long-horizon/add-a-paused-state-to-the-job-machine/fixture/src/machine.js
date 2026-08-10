/**
 * Applying a transition.
 *
 * Deliberately generic: it names no state and no event. Everything it knows comes
 * from the table and the guard registry, which is why adding a state never means
 * editing this file.
 */

import { GuardRejected, InvalidTransition, MachineError } from './errors.js';
import { GUARDS } from './guards.js';
import { emptyHistory, record } from './history.js';
import { isFinished } from './states.js';
import { withState } from './store.js';
import { transitionFor } from './table.js';

export function defaultContext(overrides = {}) {
  return { maxAttempts: 3, ...overrides };
}

export function apply(job, event, context = defaultContext()) {
  const rule = transitionFor(job.state, event);
  if (rule === undefined) {
    const suffix = isFinished(job.state) ? ' (it is a finished state)' : '';
    throw new InvalidTransition(
      `job '${job.id}' cannot handle '${event}' while ${job.state}${suffix}`,
    );
  }
  if (rule.guard !== undefined) {
    const guard = GUARDS[rule.guard];
    if (typeof guard !== 'function') {
      throw new MachineError(
        `transition ${rule.from} --${rule.event}--> ${rule.to} names guard '${rule.guard}', ` +
          `which src/guards.js does not define`,
      );
    }
    const verdict = guard(job, context);
    if (verdict !== true) {
      throw new GuardRejected(
        `job '${job.id}' may not ${event} while ${job.state}: ${verdict || 'the guard refused'}`,
      );
    }
  }
  const extra = event === 'retry' ? { attempts: job.attempts + 1 } : {};
  return withState(job, rule.to, extra);
}

export function canApply(job, event, context = defaultContext()) {
  try {
    apply(job, event, context);
    return true;
  } catch {
    return false;
  }
}

export function drive(job, events, context = defaultContext()) {
  let current = job;
  let history = emptyHistory();
  for (const event of events) {
    const from = current.state;
    try {
      current = apply(current, event, context);
      history = record(history, { from, event, to: current.state });
    } catch (error) {
      history = record(history, { from, event, to: from, refused: error.message });
      return { job: current, history, error };
    }
  }
  return { job: current, history, error: undefined };
}
