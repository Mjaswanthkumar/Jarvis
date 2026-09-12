"""Docker and Kubernetes inspection tools (read-only).

Container and cluster names are validated before they reach the CLI, so a
model-supplied string can never turn into an extra argument or a flag.
"""

from __future__ import annotations

import json
import re
from typing import Any

from jarvis.tools import shell
from jarvis.tools.permissions import PermissionLevel
from jarvis.tools.registry import tool

_DOCKER_TIMEOUT = 25.0
_KUBECTL_TIMEOUT = 25.0

#: Container / pod / namespace names: no flags, no separators, no spaces.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_name(value: str, kind: str) -> str:
    cleaned = value.strip()
    if not _SAFE_NAME.match(cleaned):
        raise ValueError(f"invalid {kind} name: {value!r}")
    return cleaned


# ---------------------------------------------------------------- docker ----
@tool(
    description=(
        "Docker overview: whether the daemon is running, version, and how many "
        "containers are running vs stopped."
    ),
    permission=PermissionLevel.READ_ONLY,
    tags=("docker",),
)
def docker_status() -> dict[str, Any]:
    version = shell.run(
        ["docker", "version", "--format", "{{json .}}"], timeout=_DOCKER_TIMEOUT
    )
    if not version.ok:
        return {
            "installed": True,
            "daemon_running": False,
            "error": (version.stderr or version.stdout).splitlines()[:2],
        }

    payload = json.loads(version.stdout)
    containers = _docker_ps(all_containers=True)
    running = [c for c in containers if c["state"] == "running"]
    return {
        "installed": True,
        "daemon_running": True,
        "client_version": payload.get("Client", {}).get("Version"),
        "server_version": (payload.get("Server") or {}).get("Version"),
        "containers_total": len(containers),
        "containers_running": len(running),
        "running_names": [c["name"] for c in running][:20],
    }


@tool(
    description=(
        "List Docker containers with image, state, status and published ports. "
        "Set include_stopped=true to also list exited containers."
    ),
    permission=PermissionLevel.READ_ONLY,
    tags=("docker",),
)
def docker_containers(
    include_stopped: bool = False, limit: int = 25
) -> list[dict[str, Any]]:
    return _docker_ps(all_containers=include_stopped)[: max(1, min(limit, 100))]


def _docker_ps(*, all_containers: bool) -> list[dict[str, Any]]:
    argv = ["docker", "ps", "--format", "{{json .}}"]
    if all_containers:
        argv.append("--all")
    result = shell.run(argv, timeout=_DOCKER_TIMEOUT).raise_for_status()

    containers: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        containers.append(
            {
                "id": row.get("ID"),
                "name": row.get("Names"),
                "image": row.get("Image"),
                "state": (row.get("State") or "").lower(),
                "status": row.get("Status"),
                "ports": row.get("Ports"),
            }
        )
    return containers


@tool(
    description=(
        "Tail the logs of one Docker container by name or id. Use when asked "
        "why a container is failing."
    ),
    permission=PermissionLevel.READ_ONLY,
    tags=("docker",),
)
def docker_logs(container: str, tail: int = 100) -> dict[str, Any]:
    name = _safe_name(container, "container")
    tail = max(1, min(tail, 1000))
    result = shell.run(
        ["docker", "logs", "--tail", str(tail), name], timeout=_DOCKER_TIMEOUT
    )
    if not result.ok and "No such container" in result.stderr:
        raise LookupError(f"no such container: {container}")
    # Docker writes container stderr to our stderr; both streams are the log.
    return {
        "container": name,
        "lines": tail,
        "logs": "\n".join(filter(None, [result.stdout, result.stderr])),
    }


@tool(
    description="List local Docker images with tag, size and age.",
    permission=PermissionLevel.READ_ONLY,
    tags=("docker",),
)
def docker_images(limit: int = 25) -> list[dict[str, Any]]:
    result = shell.run(
        ["docker", "images", "--format", "{{json .}}"], timeout=_DOCKER_TIMEOUT
    ).raise_for_status()
    images = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        images.append(
            {
                "repository": row.get("Repository"),
                "tag": row.get("Tag"),
                "size": row.get("Size"),
                "created": row.get("CreatedSince"),
            }
        )
    return images[: max(1, min(limit, 100))]


# ------------------------------------------------------------ kubernetes ----
@tool(
    description=(
        "Kubernetes cluster overview: current context, node readiness and "
        "namespace count. Reports cleanly when no cluster is reachable."
    ),
    permission=PermissionLevel.READ_ONLY,
    tags=("kubernetes",),
)
def k8s_status() -> dict[str, Any]:
    context = shell.run(
        ["kubectl", "config", "current-context"], timeout=_KUBECTL_TIMEOUT
    )
    if not context.ok:
        return {"configured": False, "reachable": False, "error": "no kubectl context"}

    nodes = shell.run(["kubectl", "get", "nodes", "-o", "json"], timeout=_KUBECTL_TIMEOUT)
    if not nodes.ok:
        return {
            "configured": True,
            "context": context.stdout.strip(),
            "reachable": False,
            "error": (nodes.stderr or nodes.stdout).splitlines()[:2],
        }

    payload = json.loads(nodes.stdout)
    rows = []
    for item in payload.get("items", []):
        conditions = {
            c["type"]: c["status"] for c in item["status"].get("conditions", [])
        }
        rows.append(
            {
                "name": item["metadata"]["name"],
                "ready": conditions.get("Ready") == "True",
                "version": item["status"]["nodeInfo"]["kubeletVersion"],
            }
        )
    return {
        "configured": True,
        "reachable": True,
        "context": context.stdout.strip(),
        "nodes": rows,
        "nodes_ready": sum(1 for row in rows if row["ready"]),
    }


@tool(
    description=(
        "List Kubernetes pods with phase, ready containers, restart count and "
        "node. Defaults to the current namespace."
    ),
    permission=PermissionLevel.READ_ONLY,
    tags=("kubernetes",),
)
def k8s_pods(
    namespace: str | None = None, all_namespaces: bool = False, limit: int = 30
) -> list[dict[str, Any]]:
    argv = ["kubectl", "get", "pods", "-o", "json"]
    if all_namespaces:
        argv.append("--all-namespaces")
    elif namespace:
        argv.extend(["-n", _safe_name(namespace, "namespace")])

    result = shell.run(argv, timeout=_KUBECTL_TIMEOUT).raise_for_status()
    payload = json.loads(result.stdout)

    pods = []
    for item in payload.get("items", []):
        statuses = item.get("status", {}).get("containerStatuses", []) or []
        pods.append(
            {
                "name": item["metadata"]["name"],
                "namespace": item["metadata"]["namespace"],
                "phase": item["status"].get("phase"),
                "ready": f"{sum(1 for s in statuses if s.get('ready'))}/{len(statuses)}",
                "restarts": sum(s.get("restartCount", 0) for s in statuses),
                "node": item["spec"].get("nodeName"),
            }
        )
    return pods[: max(1, min(limit, 100))]


@tool(
    description="Tail the logs of one Kubernetes pod.",
    permission=PermissionLevel.READ_ONLY,
    tags=("kubernetes",),
)
def k8s_logs(
    pod: str,
    namespace: str | None = None,
    tail: int = 100,
    previous: bool = False,
) -> dict[str, Any]:
    name = _safe_name(pod, "pod")
    tail = max(1, min(tail, 1000))
    argv = ["kubectl", "logs", name, f"--tail={tail}"]
    if namespace:
        argv.extend(["-n", _safe_name(namespace, "namespace")])
    if previous:
        argv.append("--previous")

    result = shell.run(argv, timeout=_KUBECTL_TIMEOUT)
    if not result.ok and "NotFound" in result.stderr:
        raise LookupError(f"no such pod: {pod}")
    return {
        "pod": name,
        "namespace": namespace,
        "previous": previous,
        "logs": result.stdout or result.stderr,
    }
