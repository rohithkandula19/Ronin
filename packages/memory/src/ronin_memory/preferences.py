from __future__ import annotations

import json
import re
from typing import Any

import anthropic
from pydantic import BaseModel, ConfigDict, Field

DEFAULT_MODEL = "claude-sonnet-4-6"

EXTRACTION_SYSTEM = (
    "You extract durable user preferences and facts from messages. "
    "Output JSON only. Do NOT extract ephemeral details, greetings, or one-off requests."
)


class UserPreferenceMemory(BaseModel):
    """Namespaced key-value store with Claude-driven fact extraction.

    The store is a plain dict so callers can persist it however they like
    (Postgres JSON column, Redis, file). Mutate it via ``set`` / ``unset`` / ``extract_from_message``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    store: dict[str, dict[str, Any]] = Field(default_factory=dict)
    model: str = DEFAULT_MODEL
    api_key: str | None = None

    def set(self, namespace: str, key: str, value: Any) -> None:
        self.store.setdefault(namespace, {})[key] = value

    def unset(self, namespace: str, key: str) -> None:
        self.store.get(namespace, {}).pop(key, None)

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        return self.store.get(namespace, {}).get(key, default)

    def all(self, namespace: str) -> dict[str, Any]:
        return dict(self.store.get(namespace, {}))

    def extract_from_message(self, namespace: str, message: str) -> list[tuple[str, Any]]:
        """Use Claude to extract durable preferences from ``message`` and store them.

        Returns the list of (key, value) pairs that were stored. Never raises on
        bad JSON — returns an empty list and leaves the store untouched.
        """
        client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()
        prompt = (
            f"User message:\n{message}\n\n"
            "Extract DURABLE user preferences/facts as a JSON array of {key, value} objects. "
            "Use snake_case keys. If nothing durable is mentioned, return []. "
            "Wrap your JSON in <facts></facts> tags."
        )
        response = client.messages.create(
            model=self.model,
            system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=512,
        )
        text = "\n".join(
            getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
        ).strip()

        match = re.search(r"<facts>(.*?)</facts>", text, re.DOTALL)
        payload = match.group(1).strip() if match else text
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return []
        if not isinstance(parsed, list):
            return []

        stored: list[tuple[str, Any]] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            key = item.get("key")
            if not isinstance(key, str) or not key:
                continue
            value = item.get("value")
            self.set(namespace, key, value)
            stored.append((key, value))
        return stored
