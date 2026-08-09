/** The audit trail: what happened to a job, in order. */

import { pad } from './util/text.js';

export function emptyHistory() {
  return [];
}

export function record(history, entry) {
  return [...history, Object.freeze({ ...entry })];
}

export function renderHistory(history) {
  if (history.length === 0) {
    return '(nothing happened)';
  }
  return history
    .map((entry, index) => {
      const step = pad(`${index + 1}.`, 4);
      const refused = entry.refused ? `  refused: ${entry.refused}` : '';
      return `${step}${entry.from} --${entry.event}--> ${entry.to ?? entry.from}${refused}`;
    })
    .join('\n');
}

export function lastState(history, fallback) {
  const last = history[history.length - 1];
  return last?.to ?? fallback;
}
