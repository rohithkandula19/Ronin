"""``/doctor``'s provider-pricing check: the free→paid fallback guard, surfaced.

The capability/pricing engine is unit-tested in ``tests/providers``; here the concern is
that ``run_doctor`` turns a cost-escalating fallback into a visible ``warn`` (never a
silent swap, never a hard failure) and stays quiet when nothing escalates — and that,
without a router, the check simply does not appear.
"""

from __future__ import annotations

from pathlib import Path

from wire_harness import fake_checkpoint_store, fake_router, git_repo, workspace

from ronin.cli.doctor import CheckStatus, run_doctor
from ronin.cli.spine import Loaded
from ronin.cli.wire import load_workspace
from ronin.providers.router import ModelSpec, Role, RouterConfig

FREE = ModelSpec(name="local", provider="mlx", model="qwen-local")
PAID = ModelSpec(
    name="claude", provider="anthropic", model="c", api_key_env="K", price_in=3.0, price_out=15.0
)


def _loaded(tmp_path: Path) -> Loaded:
    return load_workspace(workspace(git_repo(tmp_path)))


async def test_free_to_paid_fallback_is_a_warning_not_a_failure(tmp_path: Path) -> None:
    config = RouterConfig(
        roles={Role.MAIN: "local"},
        models={"local": FREE, "claude": PAID},
        fallbacks={Role.MAIN: ("claude",)},
    )
    report = await run_doctor(
        _loaded(tmp_path),
        router=fake_router(config),
        environ={"K": "sk-x"},
        checkpoints=fake_checkpoint_store(tmp_path),
    )
    check = report.check("provider pricing")
    assert check is not None
    assert check.status is CheckStatus.WARN
    assert "cost-escalating" in check.detail
    assert "local (free) to claude (paid)" in check.detail
    assert "silently swap" in check.remedy
    assert report.exit_code() == 0  # a warning never breaks the build


async def test_no_escalation_is_ok(tmp_path: Path) -> None:
    config = RouterConfig(roles={Role.MAIN: "local"}, models={"local": FREE})
    report = await run_doctor(
        _loaded(tmp_path),
        router=fake_router(config),
        environ={},
        checkpoints=fake_checkpoint_store(tmp_path),
    )
    check = report.check("provider pricing")
    assert check is not None
    assert check.status is CheckStatus.OK
    assert "1 model(s): 1 free" in check.detail


async def test_without_a_router_there_is_no_pricing_check(tmp_path: Path) -> None:
    report = await run_doctor(_loaded(tmp_path), checkpoints=fake_checkpoint_store(tmp_path))
    assert report.check("provider pricing") is None
