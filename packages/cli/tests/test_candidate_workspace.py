"""Candidate checkout lifecycle and Docker-only verification tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ronin_cli.candidate_workspace import CandidateWorkspaceService, docker_argv
from ronin_cli.mission_models import MissionSpec
from ronin_cli.mission_store import MissionStore


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _repo(root: Path) -> None:
    _git(root, "init")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Ronin tests")
    (root / "sample.py").write_text("VALUE = 'base'\n", encoding="utf-8")
    _git(root, "add", "sample.py")
    _git(root, "commit", "-m", "initial")


def _mission(root: Path) -> str:
    return MissionStore(root).create(
        MissionSpec(title="Verify candidate", issue_text="Run candidate checks in Docker."),
    ).id


def test_candidate_workspace_uses_a_detached_checkout_and_destroy_is_explicit(tmp_path) -> None:
    _repo(tmp_path)
    service = CandidateWorkspaceService(tmp_path)
    candidate = service.create(_mission(tmp_path), image="python:3.14-alpine")
    workspace = Path(candidate.path)

    assert workspace.is_dir()
    assert candidate.base_revision
    (workspace / "sample.py").write_text("VALUE = 'candidate'\n", encoding="utf-8")
    (workspace / "new.py").write_text("NEW = True\n", encoding="utf-8")
    diff = service.diff(candidate.id)
    assert "candidate" in diff
    assert "new.py" in diff
    assert (tmp_path / "sample.py").read_text(encoding="utf-8") == "VALUE = 'base'\n"

    destroyed = service.destroy(candidate.id)
    assert destroyed.status == "destroyed"
    assert not workspace.exists()
    assert service.destroy(candidate.id).status == "destroyed"


def test_candidate_command_uses_fixed_docker_sandbox_without_host_fallback(tmp_path) -> None:
    _repo(tmp_path)
    service = CandidateWorkspaceService(tmp_path)
    candidate = service.create(_mission(tmp_path), image="python:3.14-alpine")
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout="passed\n", stderr="")

    result = service.run_command(candidate.id, "pytest -q", timeout=30, run_fn=fake_run)
    argv = captured["argv"]
    assert result.passed
    assert result.stdout == "passed\n"
    assert argv == docker_argv(candidate, "pytest -q")
    assert "--network" in argv and "none" in argv
    assert ["--cap-drop", "ALL"] == argv[argv.index("--cap-drop"):argv.index("--cap-drop") + 2]
    assert ["--security-opt", "no-new-privileges"] == argv[
        argv.index("--security-opt"):argv.index("--security-opt") + 2
    ]
    assert "sh" in argv and argv[-1] == "pytest -q"


def test_candidate_command_refuses_host_execution_without_an_image(tmp_path) -> None:
    _repo(tmp_path)
    service = CandidateWorkspaceService(tmp_path)
    candidate = service.create(_mission(tmp_path))

    with pytest.raises(ValueError, match="refusing host execution"):
        service.run_command(candidate.id, "pytest -q")


def test_candidate_service_rejects_path_escape_and_invalid_timeout(tmp_path) -> None:
    _repo(tmp_path)
    service = CandidateWorkspaceService(tmp_path)
    candidate = service.create(_mission(tmp_path), image="python:3.14-alpine")

    with pytest.raises(ValueError, match="invalid candidate workspace id"):
        service.load("../" + candidate.id)
    with pytest.raises(ValueError, match="timeout"):
        service.run_command(candidate.id, "pytest -q", timeout=0)


def test_candidate_service_refuses_metadata_pointing_outside_its_owned_checkout(tmp_path) -> None:
    _repo(tmp_path)
    service = CandidateWorkspaceService(tmp_path)
    candidate = service.create(_mission(tmp_path), image="python:3.14-alpine")
    record_path = tmp_path / ".ronin" / "candidate-workspaces" / f"{candidate.id}.json"
    raw = record_path.read_text(encoding="utf-8")
    record_path.write_text(raw.replace(candidate.path, str(tmp_path / "workspace")), encoding="utf-8")

    with pytest.raises(ValueError, match="outside Ronin"):
        service.diff(candidate.id)
    record_path.write_text(raw, encoding="utf-8")
    service.destroy(candidate.id)
