from __future__ import annotations

from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MODEL = "claude-sonnet-4-6"


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


def _approx_tokens(text: str) -> int:
    """Rough estimate: ~4 chars per token. Good enough for compression triggers."""
    return max(1, len(text) // 4)


class ShortTermMemory(BaseModel):
    """Conversation turns with rolling summarization.

    When the total token estimate exceeds ``compress_threshold_tokens``, the older
    turns (everything before the last ``keep_recent``) are summarized into ``summary``
    and dropped from the verbatim window.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    turns: list[Turn] = Field(default_factory=list)
    summary: str = ""
    keep_recent: int = 6
    compress_threshold_tokens: int = 4000
    model: str = DEFAULT_MODEL
    api_key: str | None = None

    def add_turn(self, role: Literal["user", "assistant"], content: str) -> None:
        self.turns.append(Turn(role=role, content=content))

    def total_tokens(self) -> int:
        budget = _approx_tokens(self.summary) if self.summary else 0
        for t in self.turns:
            budget += _approx_tokens(t.content)
        return budget

    def maybe_compress(self) -> bool:
        """If total_tokens exceeds the threshold, summarize older turns. Returns True if compressed."""
        if self.total_tokens() <= self.compress_threshold_tokens:
            return False
        if len(self.turns) <= self.keep_recent:
            return False

        older = self.turns[: -self.keep_recent]
        recent = self.turns[-self.keep_recent :]

        client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()
        prior = self.summary or "(no prior summary)"
        transcript = "\n".join(f"{t.role.upper()}: {t.content}" for t in older)
        prompt = (
            f"You are maintaining a rolling summary of a conversation.\n"
            f"Prior summary:\n{prior}\n\n"
            f"New transcript to fold in:\n{transcript}\n\n"
            "Update the summary. Keep durable facts (preferences, decisions, names, "
            "constraints). Drop greetings and chit-chat. Output the new summary only."
        )
        response = client.messages.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
        )
        new_summary = "\n".join(
            getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
        ).strip()
        self.summary = new_summary
        self.turns = recent
        return True

    def messages(self) -> list[dict[str, Any]]:
        """Return the conversation as ready-to-send Anthropic messages.

        If a rolling summary exists, it is injected as a synthetic user/assistant pair
        at the start so the model has context without needing a separate ``system`` slot.
        """
        out: list[dict[str, Any]] = []
        if self.summary:
            out.append({
                "role": "user",
                "content": f"[Summary of earlier conversation]\n{self.summary}",
            })
            out.append({
                "role": "assistant",
                "content": "Understood — I have the prior context.",
            })
        for t in self.turns:
            out.append({"role": t.role, "content": t.content})
        return out
