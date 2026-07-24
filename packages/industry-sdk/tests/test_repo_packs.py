"""Validate the real industry-packs/ tree that ships in this repository."""
from __future__ import annotations

from pathlib import Path

from ronin_industry_sdk import PackRegistry, PackStatus, RiskLevel, discover_packs

PACKS_ROOT = Path(__file__).resolve().parents[3] / "industry-packs"  # repo root

INITIAL_WORLDS = {"coding", "education", "healthcare"}


def _registry() -> PackRegistry:
    result = discover_packs(PACKS_ROOT)
    assert result.ok, f"industry-packs tree has invalid packs: {result.errors}"
    return PackRegistry.from_discovery(result)


def test_every_shipped_manifest_validates():
    # Only packs with real policies + evals ship; empty future-world stubs were
    # deleted in the wedge cut rather than shipping 17 one-file manifests.
    reg = _registry()
    assert {m.id for m in reg.list_packs()} == INITIAL_WORLDS


def test_initial_worlds_enabled_futures_disabled():
    reg = _registry()
    for m in reg.list_packs():
        if m.id in INITIAL_WORLDS:
            assert m.status is not PackStatus.disabled, f"{m.id} must be enabled"
        else:
            assert m.status is PackStatus.disabled, (
                f"future world {m.id!r} must stay disabled until its policies, "
                "datasets, and evaluations are complete"
            )


def test_healthcare_is_high_risk_and_fail_closed():
    reg = _registry()
    m = reg.get("healthcare")
    assert m is not None
    assert m.risk_level is RiskLevel.high
    assert "autonomous_diagnosis" in m.blocked_capabilities
    assert "prescription_change" in m.blocked_capabilities
    assert "emergency_dispatch" in m.blocked_capabilities
    assert set(m.required_eval_suites) == {
        "healthcare-grounding",
        "healthcare-safety",
        "healthcare-privacy",
    }


def test_enabled_packs_have_their_policy_files():
    reg = _registry()
    for m in reg.list_packs(include_disabled=False):
        report = reg.health_check(
            m.id,
            # Isolate the policy-file check: treat manifest references as known.
            known_tools=m.allowed_tools,
            known_eval_suites=m.required_eval_suites,
            known_adapters=[m.default_adapter] if m.default_adapter else [],
        )
        assert report.healthy, f"{m.id}: {[i.message for i in report.issues]}"


def test_coding_pack_tools_are_real_runtime_tools():
    """The coding pack may only allow tools the real Ronin runtime actually has."""
    from ronin_cli.code_tools import build_code_tools  # noqa: PLC0415
    from ronin_cli.bg_processes import build_background_tools  # noqa: PLC0415

    real = {t.name for t in build_code_tools(".")}
    real |= {t.name for t in build_background_tools()}
    m = _registry().get("coding")
    assert m is not None
    unknown = set(m.allowed_tools) - real
    assert not unknown, f"coding pack allows tools the runtime lacks: {sorted(unknown)}"
