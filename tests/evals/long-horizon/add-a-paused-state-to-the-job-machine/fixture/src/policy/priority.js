/** Priorities, ordered from least to most urgent. */

export const PRIORITIES = Object.freeze(['low', 'normal', 'high', 'critical']);

export const DEFAULT_PRIORITY = 'normal';

export function rank(priority) {
  const index = PRIORITIES.indexOf(priority);
  return index === -1 ? PRIORITIES.indexOf(DEFAULT_PRIORITY) : index;
}

export function isAtLeast(priority, floor) {
  return rank(priority) >= rank(floor);
}

export function isCritical(priority) {
  return priority === 'critical';
}

export function byUrgency(jobs) {
  return [...jobs].sort((left, right) => {
    const delta = rank(right.priority) - rank(left.priority);
    return delta !== 0 ? delta : left.id.localeCompare(right.id);
  });
}
