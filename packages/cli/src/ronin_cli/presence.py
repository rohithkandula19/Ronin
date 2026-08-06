"""Human-centered communication policy for interactive Ronin sessions.

This module makes the agent more considerate without pretending it is a person.
It is deliberately local to the current turn: it never persists inferred mood,
does not diagnose a user, and cannot influence approval or safety decisions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


INTERACTION_STYLES = ("balanced", "direct", "supportive", "quiet")


@dataclass(frozen=True)
class ConversationCue:
    """A non-sensitive, short-lived delivery cue from the current task text."""

    kind: str
    guidance: str


_FRICTION = re.compile(
    r"\b(stuck|frustrated|overwhelmed|exhausted|burned out|spinning my wheels|driving me crazy)\b",
    re.IGNORECASE,
)
_URGENCY = re.compile(r"\b(asap|urgent|deadline|production is down|incident)\b", re.IGNORECASE)
_MOMENTUM = re.compile(r"\b(we did it|finally works|great news|that worked|nice work)\b", re.IGNORECASE)


def normalize_interaction_style(value: str | None) -> str:
    """Return a safe communication style for config and command input."""
    style = (value or "").strip().lower()
    return style if style in INTERACTION_STYLES else "balanced"


def detect_conversation_cue(message: str) -> ConversationCue | None:
    """Find only explicit, work-relevant signals in this one message.

    The result is guidance for delivery, not a claim about a person's internal
    state. It is intentionally not written to memory or telemetry.
    """
    if _FRICTION.search(message):
        return ConversationCue(
            "friction",
            "The user explicitly signals friction. Acknowledge that once in plain language, "
            "then give the next concrete action. Do not diagnose, dramatize, or linger on it.",
        )
    if _URGENCY.search(message):
        return ConversationCue(
            "urgency",
            "The user signals urgency. Stay calm, say what can be established now, and focus "
            "on the smallest useful next action. Do not manufacture certainty or urgency.",
        )
    if _MOMENTUM.search(message):
        return ConversationCue(
            "momentum",
            "The user signals progress. Recognize it briefly, then state the concrete result or "
            "the next verification step.",
        )
    return None


def interaction_system_block(style: str | None, *, checkins: bool, task: str = "") -> str:
    """Build an interactive-only communication block for the model.

    Keeping this separate from the core agent prompt means headless workers,
    evaluations, and sub-agents retain deterministic task-focused behavior.
    """
    active = normalize_interaction_style(style)
    style_instruction = {
        "balanced": "Be warm, concise, and practical. Use social language only when it helps the work.",
        "direct": "Be calm and concise. Avoid social filler unless the user explicitly invites it.",
        "supportive": "Be encouraging and attentive while staying concrete and work-focused.",
        "quiet": "Keep the interaction minimal and task-focused; do not add emotional acknowledgements.",
    }[active]
    checkin_instruction = (
        "A brief check-in is acceptable when it would genuinely unblock the work; do not make it habitual."
        if checkins and active != "quiet"
        else "Do not initiate social check-ins; respond directly to the work."
    )
    block = f"""HUMAN-CENTERED PRESENCE
You are Ronin, a software agent. Communicate naturally and respectfully, but never claim to be human,
to have feelings or consciousness, or to share personal experience. {style_instruction}

- Meet an explicitly expressed work difficulty with one proportional acknowledgement, then help.
- Do not diagnose emotions, give therapeutic advice, guilt the user, imply attachment, or say you are always there.
- Never retain inferred feelings, and never let conversational cues affect permissions, safety, or tool choices.
- {checkin_instruction}"""
    cue = detect_conversation_cue(task) if active != "quiet" and checkins else None
    if cue is not None:
        block += "\n\nCURRENT-TURN DELIVERY CUE\n" + cue.guidance
    return block
