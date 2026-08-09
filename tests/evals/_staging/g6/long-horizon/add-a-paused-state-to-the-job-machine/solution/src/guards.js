/**
 * Guards, by name.
 *
 * A guard returns `true` to allow the transition, or a string saying why not. The
 * string ends up in the GuardRejected message, so it is written for whoever reads
 * the ops board, not for us.
 */

import { isCritical } from './policy/priority.js';
import { attemptsLeft } from './policy/retry.js';

export const GUARDS = Object.freeze({
  attemptsRemaining(job, context) {
    const left = attemptsLeft(job, context.maxAttempts);
    if (left > 0) {
      return true;
    }
    return `no attempts left: ${job.attempts} of ${context.maxAttempts} used`;
  },

  notCritical(job) {
    if (!isCritical(job.priority)) {
      return true;
    }
    return 'critical jobs stay running until they finish or are cancelled';
  },
});

export function guardNamed(name) {
  const guard = GUARDS[name];
  if (typeof guard !== 'function') {
    throw new Error(
      `no guard named '${name}'; guards defined in src/guards.js are ${Object.keys(GUARDS).join(', ')}`,
    );
  }
  return guard;
}
