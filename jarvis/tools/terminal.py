"""A deliberately narrow "run a terminal command" tool.

This is the tool users expect an agent to have and the one most likely to be
abused, so it is an *allowlist of read-only programs*, not a shell. There is no
interpreter in the path: the string is tokenised with :mod:`shlex`, the program
must be on the allowlist, and each program declares which arguments it accepts.
"""

from __future__ import annotations

import re
import shlex
from typing import Any

from jarvis.tools import shell
from jarvis.tools.permissions import PermissionLevel
from jarvis.tools.registry import tool

#: Programs Jarvis may run this way, with a pattern each argument must match.
#: Every entry is read-only: none of them can modify the machine.
SAFE_COMMANDS: dict[str, re.Pattern[str]] = {
    "ipconfig": re.compile(r"^/(all|displaydns)$", re.IGNORECASE),
    "hostname": re.compile(r"^$"),
    "whoami": re.compile(r"^/(user|groups|upn)$", re.IGNORECASE),
    "systeminfo": re.compile(r"^$"),
    "tasklist": re.compile(r"^(/svc|/v|/fi|[A-Za-z0-9 _=*\"]+)$"),
    "netstat": re.compile(r"^-[anob]{1,4}$", re.IGNORECASE),
    "nslookup": re.compile(r"^[A-Za-z0-9.\-_]{1,253}$"),
    "ping": re.compile(r"^(-n|[1-9]|[A-Za-z0-9.\-_]{1,253})$"),
    "git": re.compile(r"^(status|log|branch|diff|remote|show|-v|--oneline|-n|\d+)$"),
    "docker": re.compile(r"^(ps|images|version|info|-a|--all)$"),
    "kubectl": re.compile(r"^(get|pods|nodes|services|version|-A|-o|wide|json)$"),
}

#: Rejected outright, before tokenising -- no shell is involved, but a command
#: containing these is a sign the model is trying to chain or redirect.
_FORBIDDEN_CHARACTERS = frozenset("&|;><`$\n\r")

_MAX_ARGUMENTS = 6
_TIMEOUT = 25.0


class UnsafeCommandError(PermissionError):
    """Raised when a requested command is not on the read-only allowlist."""


@tool(
    description=(
        "Run one read-only diagnostic command and return its output. Allowed "
        "programs: ipconfig, hostname, whoami, systeminfo, tasklist, netstat, "
        "nslookup, ping, git, docker, kubectl. Command chaining, redirection "
        "and any program that can change the system are rejected."
    ),
    permission=PermissionLevel.LOW_RISK,
    tags=("terminal",),
    untrusted_output=True,
)
def run_safe_command(command: str) -> dict[str, Any]:
    argv = validate_command(command)
    result = shell.run(argv, timeout=_TIMEOUT)
    return {
        "command": " ".join(argv),
        "exit_code": result.exit_code,
        "ok": result.ok,
        "timed_out": result.timed_out,
        "output": result.stdout or result.stderr,
    }


def validate_command(command: str) -> list[str]:
    """Tokenise and vet a command string, or raise :class:`UnsafeCommandError`."""
    text = command.strip()
    if not text:
        raise ValueError("command must not be empty")
    if _FORBIDDEN_CHARACTERS & set(text):
        raise UnsafeCommandError(
            "command chaining, redirection and substitution are not allowed"
        )

    try:
        argv = shlex.split(text, posix=False)
    except ValueError as exc:
        raise ValueError(f"could not parse command: {exc}") from exc
    if not argv:
        raise ValueError("command must not be empty")

    program = argv[0].lower().removesuffix(".exe")
    if program not in SAFE_COMMANDS:
        raise UnsafeCommandError(
            f"'{program}' is not a read-only command Jarvis is allowed to run; "
            f"allowed: {', '.join(sorted(SAFE_COMMANDS))}"
        )

    arguments = [arg.strip('"') for arg in argv[1:]]
    if len(arguments) > _MAX_ARGUMENTS:
        raise UnsafeCommandError(f"too many arguments for '{program}'")

    pattern = SAFE_COMMANDS[program]
    for argument in arguments:
        if not pattern.match(argument):
            raise UnsafeCommandError(
                f"argument {argument!r} is not permitted for '{program}'"
            )
    return [program, *arguments]
