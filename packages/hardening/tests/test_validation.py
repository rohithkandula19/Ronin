from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from pydantic import BaseModel

from ronin_hardening import OutputValidator, ValidationFailure


class Person(BaseModel):
    name: str
    age: int


def _resp(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=10, output_tokens=20),
    )


def test_valid_first_attempt() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _resp('{"name": "Alice", "age": 30}')

    with patch("ronin_hardening.validation.anthropic.Anthropic", return_value=fake_client):
        validator = OutputValidator(output_schema=Person)
        result = validator.call(system="extract person", user_message="Alice is 30.")

    assert isinstance(result, Person)
    assert result.name == "Alice"
    assert fake_client.messages.create.call_count == 1


def test_retry_on_invalid_then_succeed() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.side_effect = [
        _resp('{"name": "Bob"}'),  # missing age — Pydantic rejects
        _resp('{"name": "Bob", "age": 42}'),
    ]
    with patch("ronin_hardening.validation.anthropic.Anthropic", return_value=fake_client):
        validator = OutputValidator(output_schema=Person, max_attempts=3)
        result = validator.call(system="extract", user_message="Bob is 42.")

    assert result.age == 42
    assert fake_client.messages.create.call_count == 2


def test_extracts_json_from_code_fence() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _resp(
        'Here you go:\n```json\n{"name": "Carol", "age": 25}\n```'
    )
    with patch("ronin_hardening.validation.anthropic.Anthropic", return_value=fake_client):
        validator = OutputValidator(output_schema=Person)
        result = validator.call(system="extract", user_message="Carol is 25.")
    assert result.name == "Carol"


def test_raises_after_max_attempts() -> None:
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _resp("nonsense, no json here")
    with patch("ronin_hardening.validation.anthropic.Anthropic", return_value=fake_client):
        validator = OutputValidator(output_schema=Person, max_attempts=2)
        with pytest.raises(ValidationFailure) as exc_info:
            validator.call(system="extract", user_message="?")

    assert exc_info.value.attempts == 2
    assert exc_info.value.last_output == "nonsense, no json here"
