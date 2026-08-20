/** Delay before a retry. Deterministic: no jitter, because the tests read it. */

export function backoffFor(attempt, schedule) {
  if (attempt <= 0) {
    return 0;
  }
  const index = Math.min(attempt, schedule.length) - 1;
  return schedule[index];
}

export function totalBackoff(attempts, schedule) {
  let total = 0;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    total += backoffFor(attempt, schedule);
  }
  return total;
}
