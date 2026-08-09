/**
 * Error types. The distinction matters to callers: an InvalidTransition is a bug
 * in whatever sent the event, while a GuardRejected is a legitimate "not now".
 */

export class FlowdeckError extends Error {
  constructor(message) {
    super(message);
    this.name = new.target.name;
  }
}

export class InvalidTransition extends FlowdeckError {}

export class GuardRejected extends FlowdeckError {}

export class MachineError extends FlowdeckError {}

export class RenderError extends FlowdeckError {}
