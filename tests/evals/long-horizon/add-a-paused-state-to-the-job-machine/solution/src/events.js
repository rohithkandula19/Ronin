/** The events the outside world may send to a job. */

export const EVENTS = Object.freeze([
  'start',
  'succeed',
  'fail',
  'retry',
  'cancel',
  'pause',
  'resume',
]);

export function isEvent(value) {
  return EVENTS.includes(value);
}

export function assertEvent(event) {
  if (!isEvent(event)) {
    throw new Error(`unknown event '${event}'; known events are ${EVENTS.join(', ')}`);
  }
  return event;
}
