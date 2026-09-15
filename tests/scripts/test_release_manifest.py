"""``scripts/release_manifest.py`` — the version maths every release runs through.

Ported out of ``packages/cli/tests`` along with the module itself. Release tooling
is *build* tooling: it belongs beside ``sync_typecheck_paths.py`` and
``generate_readme_stats.py`` rather than inside a shipped CLI, and keeping it in v1
meant the build could not outlive v1.

Two things were deliberately left behind in the move.

``test_release_cli_synchronizes_the_fixed_manifest`` drove ``ronin1 dev release``,
a second entry point onto the same functions that ``scripts/release.sh`` already
called directly. The command is gone and the duplication with it; the functions it
exercised are covered below without a CLI in the way.

The docstring-tool tests that shared a file with these stay in v1, because
``ronin_cli.docstring`` is a v1 feature and this module never depended on it — the
two were only ever neighbours in one file.

Everything here is offline: tmp trees, and a ``PATH`` of fake ``git``/``uv`` for the
two tests that drive the real shell script.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from release_manifest import (  # noqa: E402  (path juggling must precede the import)
    CLI_INTERNAL_DISTRIBUTIONS,
    RELEASE_PACKAGE_DIRS,
    bump_version,
    current_version,
    find_version_files,
    prepare_release,
    release_artifact_errors,
    release_packages,
    release_validation_errors,
    release_version_files,
    set_version_in_text,
    validate_release,
    validate_release_artifacts,
    version_from_tag,
)

_NAMES = {
    "packages/agent-patterns": "ronin-agent-patterns",
    "packages/eval-suite": "ronin-eval-suite",
    "packages/memory": "ronin-memory",
    "packages/hardening": "ronin-hardening",
    "packages/mcp-servers": "ronin-mcp-servers",
    "packages/cli": "ronin-cli",
    "packages/relay": "ronin-relay",
}


def _write_project(path: Path, name: str, version: str, dependencies: tuple[str, ...] = ()) -> None:
    block = "\n".join(f'  "{dependency}",' for dependency in dependencies)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'[project]\nname = "{name}"\nversion = "{version}"\ndependencies = [{block}\n]\n',
        encoding="utf-8",
    )


def _release_tree(tmp_path: Path, version: str = "1.0.0") -> Path:
    """A miniature repository shaped like the real release manifest."""
    _write_project(tmp_path / "pyproject.toml", "ronin", version)
    for directory in RELEASE_PACKAGE_DIRS:
        dependencies = tuple(f"{name}=={version}" for name in CLI_INTERNAL_DISTRIBUTIONS)
        _write_project(
            tmp_path / directory / "pyproject.toml",
            _NAMES[directory],
            version,
            dependencies if directory == "packages/cli" else (),
        )
    for package in ("packages/cli/src/ronin_cli", "packages/relay/src/ronin_relay"):
        (tmp_path / package).mkdir(parents=True)
        (tmp_path / package / "__init__.py").write_text(
            f'__version__ = "{version}"\n', encoding="utf-8"
        )
    return tmp_path


# --------------------------------------------------------------------------- #
# a tag becomes a version
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("tag", "version"),
    [("v1.2.3", "1.2.3"), ("v1.2.3-rc.4", "1.2.3rc4"), ("v1.2.3-b.4", "1.2.3b4")],
)
def test_version_from_tag_supports_final_and_prerelease(tag: str, version: str) -> None:
    assert version_from_tag(tag) == version


def test_version_from_tag_refuses_ambiguous_tags() -> None:
    with pytest.raises(ValueError, match="invalid release tag"):
        version_from_tag("release-1.2.3")


@pytest.mark.parametrize(
    ("current", "kind", "expected"),
    [
        ("1.2.3", "patch", "1.2.4"),
        ("1.2.3", "minor", "1.3.0"),
        ("1.2.3", "major", "2.0.0"),
        ("0.57.0", "minor", "0.58.0"),
    ],
)
def test_bump_version(current: str, kind: str, expected: str) -> None:
    assert bump_version(current, kind) == expected


def test_a_bump_refuses_nonsense_rather_than_guessing() -> None:
    with pytest.raises(ValueError):
        bump_version("not-semver", "patch")
    with pytest.raises(ValueError):
        bump_version("1.2.3", "huge")


# --------------------------------------------------------------------------- #
# rewriting a version in place
# --------------------------------------------------------------------------- #


def test_set_version_in_a_dunder() -> None:
    out, count = set_version_in_text('__version__ = "1.2.3"\nother = 1\n', "1.2.4")
    assert count == 1
    assert '__version__ = "1.2.4"' in out


def test_set_version_in_a_pyproject() -> None:
    out, count = set_version_in_text('[project]\nname = "x"\nversion = "0.1.0"\n', "0.2.0")
    assert count == 1
    assert 'version = "0.2.0"' in out


def test_find_and_current_version(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "__init__.py").write_text('__version__ = "3.4.5"\n', encoding="utf-8")
    files = find_version_files(tmp_path)
    assert any(path.name == "__init__.py" for path in files)
    assert current_version(files) == "3.4.5"


# --------------------------------------------------------------------------- #
# the manifest moves as one, or the release is wrong
# --------------------------------------------------------------------------- #


def test_release_validation_catches_package_and_dependency_drift(tmp_path: Path) -> None:
    """A package left behind at the old version is the failure this exists to catch."""
    root = _release_tree(tmp_path)
    assert release_validation_errors(root, "v1.0.0") == []

    cli_file = root / "packages/cli/pyproject.toml"
    cli_file.write_text(
        cli_file.read_text(encoding="utf-8").replace("ronin-memory==1.0.0", "ronin-memory==0.9.0"),
        encoding="utf-8",
    )
    memory_file = root / "packages/memory/pyproject.toml"
    memory_file.write_text(
        memory_file.read_text(encoding="utf-8").replace('version = "1.0.0"', 'version = "0.9.0"'),
        encoding="utf-8",
    )

    errors = release_validation_errors(root, "v1.0.0")
    assert any("packages/memory/pyproject.toml declares 0.9.0" in error for error in errors)
    assert any("ronin-memory==1.0.0" in error for error in errors)
    with pytest.raises(ValueError, match="release validation failed"):
        validate_release(root, "v1.0.0")


def test_prepare_release_updates_only_the_release_manifest(tmp_path: Path) -> None:
    """An app that is not published must not have its version moved under it."""
    root = _release_tree(tmp_path)
    unrelated = root / "apps/demo/pyproject.toml"
    _write_project(unrelated, "ronin-demo", "0.0.1")

    changed = prepare_release(root, "1.1.0rc2")

    assert set(changed) == set(release_version_files(root))
    assert release_validation_errors(root, "v1.1.0-rc.2") == []
    assert 'version = "0.0.1"' in unrelated.read_text(encoding="utf-8")


def test_release_artifact_validation_requires_a_wheel_and_sdist_per_package(
    tmp_path: Path,
) -> None:
    root = _release_tree(tmp_path)
    dist = root / "dist"
    dist.mkdir()
    for package in release_packages(root):
        prefix = f"{package.name.replace('-', '_')}-1.0.0"
        (dist / f"{prefix}-py3-none-any.whl").touch()
        (dist / f"{prefix}.tar.gz").touch()

    assert release_artifact_errors(root, "v1.0.0", dist) == []
    validate_release_artifacts(root, "v1.0.0", dist)

    (dist / "ronin_cli-1.0.0.tar.gz").unlink()
    assert release_artifact_errors(root, "v1.0.0", dist) == ["missing sdist for ronin-cli 1.0.0"]


# --------------------------------------------------------------------------- #
# the shell script that drives it
# --------------------------------------------------------------------------- #


def _fake_command(directory: Path, name: str, source: str) -> None:
    command = directory / name
    command.write_text(source, encoding="utf-8")
    command.chmod(0o755)


def _release_env(tmp_path: Path, *, git_status: str = "") -> tuple[dict[str, str], Path]:
    """A ``PATH`` where ``git`` and ``uv`` are scripted, so the real release.sh runs offline."""
    commands = tmp_path / "commands"
    commands.mkdir()
    uv_log = tmp_path / "uv.log"
    _fake_command(
        commands,
        "git",
        """#!/usr/bin/env bash
if [ "$1" = "status" ] && [ "$2" = "--porcelain" ]; then
  printf '%s' "${FAKE_GIT_STATUS:-}"
  exit 0
fi
if [ "$1" = "status" ] && [ "$2" = "--short" ]; then
  exit 0
fi
if [ "$1" = "rev-parse" ] && [ "$2" = "--abbrev-ref" ]; then
  printf '%s\\n' main
  exit 0
fi
exit 99
""",
    )
    _fake_command(
        commands,
        "uv",
        """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_UV_LOG"
if [ "$1" = "run" ] && [ "$2" = "--frozen" ] && [ "$3" = "pytest" ]; then
  printf '7 passed\\n'
  exit 0
fi
exit 98
""",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{commands}{os.pathsep}{env['PATH']}",
            "FAKE_GIT_STATUS": git_status,
            "FAKE_UV_LOG": str(uv_log),
        }
    )
    return env, uv_log


def test_release_script_dry_run_does_not_change_sources(tmp_path: Path) -> None:
    env, _log = _release_env(tmp_path)
    watched = [REPO_ROOT / "CHANGELOG.md", *release_version_files(REPO_ROOT)]
    before = {path: path.read_text(encoding="utf-8") for path in watched}

    result = subprocess.run(
        ["bash", "scripts/release.sh", "1.2.3", "--dry-run"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "dry run complete" in result.stdout
    assert {path: path.read_text(encoding="utf-8") for path in watched} == before


def test_release_script_refuses_a_dirty_tree_before_tests(tmp_path: Path) -> None:
    """Refused *before* the suite runs: a dirty tree wastes the slowest step otherwise."""
    env, uv_log = _release_env(tmp_path, git_status=" M src/ronin/cli/main.py\n")

    result = subprocess.run(
        ["bash", "scripts/release.sh", "1.2.3", "--dry-run"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "working tree is not clean" in result.stderr
    assert not uv_log.exists(), "the test suite must not have been reached"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__])
