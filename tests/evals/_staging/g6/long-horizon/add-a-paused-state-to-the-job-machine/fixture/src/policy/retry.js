/** How many times a failed job may be picked up again. */

export function attemptsLeft(job, maxAttempts) {
  return Math.max(0, maxAttempts - job.attempts);
}

export function shouldRetry(job, maxAttempts) {
  return job.state === 'failed' && attemptsLeft(job, maxAttempts) > 0;
}

export function describeAttempts(job, maxAttempts) {
  return `${job.attempts}/${maxAttempts} attempts`;
}
