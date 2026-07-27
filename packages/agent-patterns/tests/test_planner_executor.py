from __future__ import annotations

import json
import subprocess

from ronin_agent_patterns import FakeProvider, LLMResponse, Plan, PlanCache, PlannerExecutorAgent


def test_planner_executor_happy_path() -> None:
    plan_text = (
        "Here is the plan:\n"
        "<plan>\n"
        '{"goal": "greet user", "steps": ["say hello"]}\n'
        "</plan>"
    )
    provider = FakeProvider(responses=[
        LLMResponse(text=plan_text, stop_reason="end_turn"),
        LLMResponse(text="hello!", stop_reason="end_turn"),
    ])
    agent = PlannerExecutorAgent(
        planner_system="plan",
        executor_system="execute",
        provider=provider,
    )
    result = agent.run("greet the user")

    assert result.success
    plan_steps = [s for s in result.trace if s.kind == "plan"]
    assert plan_steps and plan_steps[0].content["goal"] == "greet user"


def test_planner_executor_missing_plan_tags_fails_clearly() -> None:
    provider = FakeProvider(responses=[
        LLMResponse(text="here you go", stop_reason="end_turn"),
    ])
    agent = PlannerExecutorAgent(
        planner_system="plan",
        executor_system="exec",
        max_replans=0,
        provider=provider,
    )
    result = agent.run("do something")

    assert not result.success
    assert "plan" in (result.error or "").lower()


def test_planner_cache_hit_skips_a_second_planner_call(tmp_path) -> None:
    cache = PlanCache(tmp_path)
    plan_text = '<plan>{"goal": "greet", "steps": ["say hello"]}</plan>'
    first_provider = FakeProvider(responses=[
        LLMResponse(text=plan_text, stop_reason="end_turn"),
        LLMResponse(text="hello", stop_reason="end_turn"),
    ])
    first = PlannerExecutorAgent(
        planner_system="plan", executor_system="execute", provider=first_provider, plan_cache=cache,
    ).run("greet the user")

    second_provider = FakeProvider(responses=[LLMResponse(text="hello again", stop_reason="end_turn")])
    second_agent = PlannerExecutorAgent(
        planner_system="plan", executor_system="execute", provider=second_provider, plan_cache=cache,
    )
    second = second_agent.run("  greet   the user  ")

    assert first.success and second.success
    assert len(first_provider.calls) == 2
    assert len(second_provider.calls) == 1
    assert [step for step in second.trace if step.kind == "plan"][0].metadata["cache"] == "hit"
    entry = next((tmp_path / ".ronin" / "plans").glob("*.json"))
    assert "greet the user" not in entry.read_text(encoding="utf-8")


def test_planner_cache_invalidates_when_repository_changes(tmp_path) -> None:
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    cache = PlanCache(tmp_path)
    first_provider = FakeProvider(responses=[
        LLMResponse(text='<plan>{"goal": "g", "steps": ["one"]}</plan>'),
        LLMResponse(text="done"),
    ])
    PlannerExecutorAgent(planner_system="p", executor_system="e", provider=first_provider,
                         plan_cache=cache).run("change the demo")

    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'changed'\n", encoding="utf-8")
    second_provider = FakeProvider(responses=[
        LLMResponse(text='<plan>{"goal": "g", "steps": ["two"]}</plan>'),
        LLMResponse(text="done"),
    ])
    second = PlannerExecutorAgent(planner_system="p", executor_system="e", provider=second_provider,
                                  plan_cache=cache).run("change the demo")

    assert second.success
    assert len(second_provider.calls) == 2
    assert [step for step in second.trace if step.kind == "plan"][0].metadata["cache"] == "miss"


def test_plan_cache_corruption_is_a_safe_miss_and_retention_is_bounded(tmp_path) -> None:
    cache = PlanCache(tmp_path, max_entries=1)
    task = "first task"
    path = cache.cache_path(task)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")

    assert cache.load(task) is None
    assert cache.store(task, Plan(goal="one", steps=["step"]))
    assert cache.store("second task", Plan(goal="two", steps=["step"]))
    assert len(list(cache.directory.glob("*.json"))) == 1
    stored = json.loads(next(cache.directory.glob("*.json")).read_text(encoding="utf-8"))
    assert stored["version"] == 1


def test_planner_cache_ignores_its_own_untracked_directory_in_git_repo(tmp_path) -> None:
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True,
                       capture_output=True, text=True)

    git("init")
    git("config", "user.name", "Ronin Test")
    git("config", "user.email", "ronin@example.invalid")
    (tmp_path / "app.py").write_text("print('hello')\n", encoding="utf-8")
    git("add", "app.py")
    git("commit", "-m", "initial")

    cache = PlanCache(tmp_path)
    plan = Plan(goal="greet", steps=["say hello"])

    assert cache.store("greet the user", plan)
    assert cache.load("greet the user") == plan
