from ronin_agent_patterns.task_engine import decompose, pack, risk_for, role_for


def test_decompose_labels_and_dedupes() -> None:
    plan = decompose("Add the parser. Add the parser\nThen test the parser\nThen delete the old table")
    assert plan.goal.startswith("Add the parser")
    assert len(plan.steps) == 3
    assert plan.steps[0].startswith("[implement/low]")
    assert plan.steps[1].startswith("[test/low]")
    assert plan.steps[2].startswith("[implement/high]")
    assert role_for("review the diff") == "review"
    assert risk_for("deploy to production") == "high"


def test_pack_stops_at_the_budget() -> None:
    assert pack(["aa", "bbb", "cccc"], 6) == ["aa", "bbb"]
    assert pack(["abcdef"], 3) == ["abc"]
