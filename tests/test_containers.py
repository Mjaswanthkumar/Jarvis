from __future__ import annotations

import json
from typing import Any

import pytest

from jarvis.tools import REGISTRY, containers
from jarvis.tools.shell import CommandResult


def fake_shell(responses: dict[str, tuple[int, str]]):
    """Return a shell.run replacement keyed by a substring of the command."""

    def _run(argv: list[str], **_: Any) -> CommandResult:
        command = " ".join(argv)
        for needle, (code, out) in responses.items():
            if needle in command:
                return CommandResult(
                    command=command,
                    exit_code=code,
                    stdout=out if code == 0 else "",
                    stderr="" if code == 0 else out,
                )
        raise AssertionError(f"unexpected command: {command}")

    return _run


DOCKER_PS = "\n".join(
    json.dumps(row)
    for row in [
        {
            "ID": "abc123",
            "Names": "api",
            "Image": "api:latest",
            "State": "running",
            "Status": "Up 2 hours",
            "Ports": "0.0.0.0:8000->8000/tcp",
        },
        {
            "ID": "def456",
            "Names": "db",
            "Image": "postgres:16",
            "State": "exited",
            "Status": "Exited (0) 1 hour ago",
            "Ports": "",
        },
    ]
)


@pytest.mark.parametrize("bad", ["-rf /", "a b", "--privileged", "", "x" * 200])
def test_container_names_with_flags_or_spaces_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        containers._safe_name(bad, "container")


def test_valid_container_names_pass() -> None:
    assert containers._safe_name("my-api_1.2", "container") == "my-api_1.2"


def test_docker_status_parses_versions_and_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        containers.shell,
        "run",
        fake_shell(
            {
                "docker version": (
                    0,
                    json.dumps(
                        {
                            "Client": {"Version": "27.0.3"},
                            "Server": {"Version": "27.0.3"},
                        }
                    ),
                ),
                "docker ps": (0, DOCKER_PS),
            }
        ),
    )
    result = REGISTRY.execute("docker_status")
    assert result.ok, result.error
    assert result.data["daemon_running"] is True
    assert result.data["containers_total"] == 2
    assert result.data["containers_running"] == 1
    assert result.data["running_names"] == ["api"]


def test_docker_status_reports_a_stopped_daemon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        containers.shell,
        "run",
        fake_shell({"docker version": (1, "error during connect: daemon not running")}),
    )
    result = REGISTRY.execute("docker_status")
    assert result.ok, result.error
    assert result.data["daemon_running"] is False


def test_docker_containers_filters_stopped_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def _run(argv: list[str], **_: Any) -> CommandResult:
        seen.append(" ".join(argv))
        return CommandResult(command="", exit_code=0, stdout=DOCKER_PS, stderr="")

    monkeypatch.setattr(containers.shell, "run", _run)
    REGISTRY.execute("docker_containers")
    assert "--all" not in seen[0]
    REGISTRY.execute("docker_containers", {"include_stopped": True})
    assert "--all" in seen[1]


def test_docker_logs_reports_a_missing_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        containers.shell,
        "run",
        fake_shell({"docker logs": (1, "Error: No such container: ghost")}),
    )
    result = REGISTRY.execute("docker_logs", {"container": "ghost"})
    assert not result.ok
    assert "no such container" in (result.error or "")


def test_k8s_status_without_a_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        containers.shell,
        "run",
        fake_shell({"kubectl config": (1, "error: current-context is not set")}),
    )
    result = REGISTRY.execute("k8s_status")
    assert result.ok, result.error
    assert result.data["configured"] is False


def test_k8s_status_summarises_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    nodes = json.dumps(
        {
            "items": [
                {
                    "metadata": {"name": "node-1"},
                    "status": {
                        "conditions": [{"type": "Ready", "status": "True"}],
                        "nodeInfo": {"kubeletVersion": "v1.30.2"},
                    },
                },
                {
                    "metadata": {"name": "node-2"},
                    "status": {
                        "conditions": [{"type": "Ready", "status": "False"}],
                        "nodeInfo": {"kubeletVersion": "v1.30.2"},
                    },
                },
            ]
        }
    )
    monkeypatch.setattr(
        containers.shell,
        "run",
        fake_shell(
            {"kubectl config": (0, "docker-desktop"), "kubectl get nodes": (0, nodes)}
        ),
    )
    result = REGISTRY.execute("k8s_status")
    assert result.ok, result.error
    assert result.data["context"] == "docker-desktop"
    assert result.data["nodes_ready"] == 1


def test_k8s_pods_summarises_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    pods = json.dumps(
        {
            "items": [
                {
                    "metadata": {"name": "api-0", "namespace": "default"},
                    "spec": {"nodeName": "node-1"},
                    "status": {
                        "phase": "Running",
                        "containerStatuses": [
                            {"ready": True, "restartCount": 2},
                            {"ready": False, "restartCount": 0},
                        ],
                    },
                }
            ]
        }
    )
    monkeypatch.setattr(
        containers.shell, "run", fake_shell({"kubectl get pods": (0, pods)})
    )
    result = REGISTRY.execute("k8s_pods")
    assert result.ok, result.error
    pod = result.data[0]
    assert pod["ready"] == "1/2"
    assert pod["restarts"] == 2
    assert pod["phase"] == "Running"


def test_k8s_logs_rejects_injected_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    result = REGISTRY.execute(
        "k8s_logs", {"pod": "api-0", "namespace": "--all-namespaces"}
    )
    assert not result.ok
    assert "invalid namespace name" in (result.error or "")


def test_missing_cli_is_reported_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker not installed must read as a clear message, not a crash."""
    from jarvis.tools.shell import ExecutableNotFound

    def _run(argv: list[str], **_: Any) -> CommandResult:
        raise ExecutableNotFound("'docker' is not installed or not on PATH")

    monkeypatch.setattr(containers.shell, "run", _run)
    result = REGISTRY.execute("docker_images")
    assert not result.ok
    assert "not installed" in (result.error or "")
