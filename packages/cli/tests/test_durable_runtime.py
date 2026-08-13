from __future__ import annotations

from ronin_cli.durable_runtime import surface_runtime
import pytest


def test_surface_runtime_keeps_journal_project_local_and_identities_opaque(tmp_path) -> None:
    runtime = surface_runtime(tmp_path, "telegram", "chat-id-12345")
    assert runtime.journal.path.parent == tmp_path / ".ronin" / "durable-runs"
    assert "12345" not in runtime.run_id
    assert runtime.run_id.startswith("telegram-")


def test_surface_runtime_uses_distinct_run_ids_for_recurring_work(tmp_path) -> None:
    first = surface_runtime(tmp_path, "schedule", "daily-brief")
    second = surface_runtime(tmp_path, "schedule", "daily-brief")
    assert first.run_id != second.run_id
    assert first.journal.path == second.journal.path
    assert first.budget.limits.max_tokens == 50_000
    assert first.budget.limits.max_tool_calls == 80


@pytest.mark.parametrize(
    "surface",
    ["agent", "cli-session", "acp", "mission", "pipeline", "telegram", "schedule", "relay-target"],
)
def test_surface_runtime_isolated_by_named_surface(tmp_path, surface: str) -> None:
    runtime = surface_runtime(tmp_path, surface, "opaque-user")
    assert runtime.journal.path.name == f"{surface}.sqlite"
    assert runtime.run_id.startswith(f"{surface}-")
