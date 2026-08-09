/**
 * What the ops board prints.
 *
 * Every state needs a symbol and a label here. The board refuses to render a state
 * it does not know rather than printing a blank column, because a blank column on
 * the wall display has been mistaken for "idle" twice.
 */

import { RenderError } from './errors.js';
import { STATES, isFinished } from './states.js';
import { renderTable } from './util/table.js';
import { pad, titleCase } from './util/text.js';

export const SYMBOLS = Object.freeze({
  queued: '.',
  running: '>',
  paused: '=',
  succeeded: '+',
  failed: '!',
  cancelled: 'x',
});

export const LABELS = Object.freeze({
  queued: 'waiting for a worker',
  running: 'on a worker now',
  paused: 'paused by a human',
  succeeded: 'done',
  failed: 'failed, may retry',
  cancelled: 'cancelled by a human',
});

export function symbolFor(state) {
  const symbol = SYMBOLS[state];
  if (symbol === undefined) {
    throw new RenderError(`no symbol for state '${state}'; add one to SYMBOLS in src/render.js`);
  }
  return symbol;
}

export function labelFor(state) {
  const label = LABELS[state];
  if (label === undefined) {
    throw new RenderError(`no label for state '${state}'; add one to LABELS in src/render.js`);
  }
  return label;
}

export function legend() {
  return STATES.map(
    (state) => `${symbolFor(state)}  ${pad(state, 10)}${labelFor(state)}${isFinished(state) ? ' (finished)' : ''}`,
  ).join('\n');
}

export function board(jobs) {
  const rows = jobs.map((job) => [
    symbolFor(job.state),
    job.id,
    job.state,
    job.priority,
    String(job.attempts),
    titleCase(job.label || '-'),
  ]);
  return renderTable(['', 'job', 'state', 'priority', 'tries', 'label'], rows);
}
