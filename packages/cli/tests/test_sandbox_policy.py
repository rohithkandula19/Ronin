from __future__ import annotations

from ronin_cli.sandbox_policy import inspect_sandbox_policy


def test_local_policy_is_explicit_host_execution() -> None:
    status = inspect_sandbox_policy(None)
    assert status.mode == "local"
    assert status.status == "host"
    assert status.fail_closed is False


def test_unavailable_docker_and_invalid_spec_fail_closed() -> None:
    docker = inspect_sandbox_policy("docker:agent", executable=lambda _name: None)
    assert docker.mode == "docker"
    assert docker.status == "blocked"
    assert docker.fail_closed is True

    invalid = inspect_sandbox_policy("not-a-backend")
    assert invalid.status == "blocked"
    assert invalid.fail_closed is True
