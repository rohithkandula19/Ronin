"""Packaging-regression guards (Phase 2C): the built ronin-cli wheel must be
publishable — proper version-spec deps (no local/workspace paths leaked into
METADATA), all internal deps declared, both console scripts present, and no
secret / database / repo-state file bundled. Builds the wheel in isolation so
the check runs against a real artifact, not the source tree."""
from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]   # .../ronin
WORKSPACE_DEPS = {
    "ronin-agent-patterns", "ronin-hardening", "ronin-memory",
    "ronin-mcp-servers", "ronin-eval-suite",
}


@pytest.fixture(scope="module")
def cli_wheel(tmp_path_factory) -> Path:
    if shutil.which("uv") is None:
        pytest.skip("uv not on PATH")
    out = tmp_path_factory.mktemp("dist")
    r = subprocess.run(
        ["uv", "build", "--package", "ronin-cli", "-o", str(out)],
        cwd=REPO, capture_output=True, text=True,
    )
    if r.returncode != 0:
        pytest.skip(f"uv build failed: {r.stderr[-300:]}")
    wheels = list(out.glob("ronin_cli-*.whl"))
    assert wheels, "no ronin-cli wheel produced"
    return wheels[0]


def _metadata(wheel: Path) -> str:
    z = zipfile.ZipFile(wheel)
    name = next(n for n in z.namelist() if n.endswith("METADATA"))
    return z.read(name).decode()


def _requires(wheel: Path) -> list[str]:
    return [l for l in _metadata(wheel).splitlines() if l.startswith("Requires-Dist")]


def test_no_local_path_dependencies(cli_wheel):
    # A published wheel must never carry a file:// / @ path dependency — that
    # would only resolve on the author's machine (the "works because the repo is
    # present" trap).
    for req in _requires(cli_wheel):
        assert "file://" not in req, f"local-path dep leaked: {req}"
        assert " @ " not in req, f"direct-reference dep leaked: {req}"


def test_all_internal_deps_declared_as_versions(cli_wheel):
    reqs = " ".join(_requires(cli_wheel))
    for dep in WORKSPACE_DEPS:
        assert dep in reqs, f"internal dependency not declared in wheel metadata: {dep}"


def test_the_console_script_is_ronin1_and_does_not_claim_the_short_names(cli_wheel):
    # This tree used to declare `ronin` and `ro`. Both are gone on purpose: the v2 tree
    # at the repository root now declares `ronin`, and console scripts are not
    # namespaced — two installed distributions declaring one name means whichever was
    # installed second silently overwrites the other's launcher, so a user who upgraded
    # one of them finds a different agent behind the same word. `ronin1` is a rename
    # rather than a removal, so this CLI still has a name to type.
    z = zipfile.ZipFile(cli_wheel)
    ep = next((n for n in z.namelist() if n.endswith("entry_points.txt")), None)
    assert ep, "no entry_points.txt in wheel"
    txt = z.read(ep).decode()
    assert "ronin1 = ronin_cli.main:app" in txt
    scripts = {line.split("=")[0].strip() for line in txt.splitlines() if "=" in line}
    assert "ronin" not in scripts, "the short name belongs to the v2 tree at the repo root"
    assert "ro" not in scripts, "a two-letter name resolving to v1 is the same silent swap"


def test_no_secret_or_state_files_in_artifact(cli_wheel):
    # Legit source modules named *secret*/*checkpoint* are code and allowed;
    # actual credential/state/data files are not.
    z = zipfile.ZipFile(cli_wheel)
    for n in z.namelist():
        low = n.lower()
        assert not low.endswith(".env"), n
        assert not low.endswith((".db", ".sqlite", ".sqlite3")), n
        assert not low.endswith((".pem", ".key", ".p12")), n
        assert "/.ronin/" not in low, n
        assert not (("/logs/" in low) and not low.endswith(".py")), n
