from __future__ import annotations

import json
import re
from typing import Any, Callable, TypeVar

import anthropic
from pydantic import BaseModel, ConfigDict, ValidationError

T = TypeVar("T", bound=BaseModel)


class ValidationFailure(Exception):
    """Raised when output validation fails after all retries."""

    def __init__(self, attempts: int, last_error: str, last_output: str):
        super().__init__(f"output validation failed after {attempts} attempts: {last_error}")
        self.attempts = attempts
        self.last_error = last_error
        self.last_output = last_output


def _extract_json(text: str) -> str:
    """Pull a JSON object out of common LLM output shapes (code fences, prose, etc.)."""
    fence = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        return fence.group(1)
    bare = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    return bare.group(1) if bare else text


class OutputValidator(BaseModel):
    """Validate structured Claude output against a Pydantic schema, with retry on failure.

    Each failed attempt feeds the validation error back to the model so it can self-correct.
    Returns a parsed Pydantic instance or raises ``ValidationFailure``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    output_schema: type[BaseModel]
    model: str = "claude-sonnet-4-6"
    max_attempts: int = 3
    api_key: str | None = None

    def validate_output(self, text: str) -> BaseModel:
        return self.output_schema.model_validate_json(_extract_json(text))

    def call(
        self,
        system: str,
        user_message: str,
        prompt_builder: Callable[[str], str] | None = None,
    ) -> Any:
        """Make a Claude call and validate the output against ``schema``.

        ``prompt_builder`` is an optional fn that takes a validation error string and
        returns a follow-up message. Default behavior appends the error to the user
        message verbatim.
        """
        client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()
        builder = prompt_builder or (lambda err: f"Your previous response failed validation: {err}\nReturn corrected output.")

        messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
        last_output = ""
        last_error = ""

        for attempt in range(self.max_attempts):
            response = client.messages.create(
                model=self.model,
                system=system,
                messages=messages,
                max_tokens=2048,
            )
            text = "\n".join(
                getattr(b, "text", "") for b in response.content if getattr(b, "type", None) == "text"
            ).strip()
            last_output = text

            try:
                return self.validate_output(text)
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                last_error = str(exc)
                messages.append({"role": "assistant", "content": text})
                messages.append({"role": "user", "content": builder(last_error)})

        raise ValidationFailure(self.max_attempts, last_error, last_output)
