from __future__ import annotations

import pytest

from jarvis.tools import REGISTRY
from jarvis.tools.terminal import UnsafeCommandError, validate_command


@pytest.mark.parametrize(
    "command",
    [
        "ipconfig /all",
        "hostname",
        "whoami /user",
        "tasklist /svc",
        "netstat -an",
        "git status",
        "docker ps -a",
        "nslookup example.com",
    ],
)
def test_read_only_commands_are_accepted(command: str) -> None:
    assert validate_command(command)[0] == command.split()[0]


@pytest.mark.parametrize(
    "command",
    [
        "del C:\\Windows\\System32",
        "rm -rf /",
        "shutdown /s",
        "format C:",
        "reg delete HKLM\\Software",
        "powershell -c whoami",
        "cmd /c dir",
        "curl http://evil.test",
        "taskkill /f /im explorer.exe",
        "net user hacker /add",
    ],
)
def test_destructive_and_unknown_programs_are_rejected(command: str) -> None:
    with pytest.raises(UnsafeCommandError):
        validate_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "hostname && del important.txt",
        "hostname | shutdown",
        "hostname; rm file",
        "hostname > out.txt",
        "hostname `whoami`",
        "hostname $(whoami)",
        "hostname\nshutdown /s",
    ],
)
def test_chaining_redirection_and_substitution_are_rejected(command: str) -> None:
    with pytest.raises(UnsafeCommandError):
        validate_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "git push --force",
        "git commit -m x",
        "docker rm -f api",
        "docker run ubuntu",
        "kubectl delete pod api",
        "ipconfig /release",
        "ipconfig /flushdns",
    ],
)
def test_state_changing_subcommands_are_rejected(command: str) -> None:
    """An allowed program must still not reach a destructive subcommand."""
    with pytest.raises(UnsafeCommandError):
        validate_command(command)


def test_empty_command_is_rejected() -> None:
    with pytest.raises(ValueError):
        validate_command("   ")


def test_too_many_arguments_are_rejected() -> None:
    with pytest.raises(UnsafeCommandError):
        validate_command("ping -n 1 -n 1 -n 1 -n 1")


def test_exe_suffix_is_normalised() -> None:
    assert validate_command("hostname.exe") == ["hostname"]


def test_run_safe_command_executes_and_returns_output() -> None:
    result = REGISTRY.execute("run_safe_command", {"command": "hostname"})
    assert result.ok, result.error
    assert result.data["ok"] is True
    assert result.data["output"].strip()


def test_run_safe_command_surfaces_rejections_as_errors() -> None:
    result = REGISTRY.execute("run_safe_command", {"command": "shutdown /s"})
    assert not result.ok
    assert "not a read-only command" in (result.error or "")
