"""System / PC health tools: CPU, memory, disk, battery, network, processes."""

from __future__ import annotations

import platform
import socket
import time
from datetime import datetime, timezone
from typing import Any

import psutil

from jarvis.tools.permissions import PermissionLevel
from jarvis.tools.registry import tool

_BYTES_PER_GB = 1024**3


def _gb(value: float) -> float:
    return round(value / _BYTES_PER_GB, 2)


@tool(
    description="Overall PC health snapshot: CPU, memory, disks, battery, uptime. "
    "Use this for general questions like 'how is my PC doing?'.",
    permission=PermissionLevel.READ_ONLY,
    tags=("system",),
)
def system_health() -> dict[str, Any]:
    boot = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
    return {
        "host": platform.node(),
        "os": f"{platform.system()} {platform.release()}",
        "cpu": cpu_info(),
        "memory": memory_info(),
        "disks": disk_usage(),
        "battery": battery_status(),
        "uptime_hours": round(
            (datetime.now(tz=timezone.utc) - boot).total_seconds() / 3600, 2
        ),
    }


@tool(
    description="Current CPU load: overall percent, per-core percent, core counts "
    "and frequency.",
    permission=PermissionLevel.READ_ONLY,
    tags=("system",),
)
def cpu_info() -> dict[str, Any]:
    per_core = psutil.cpu_percent(interval=0.3, percpu=True)
    freq = psutil.cpu_freq()
    return {
        "percent": round(sum(per_core) / len(per_core), 1) if per_core else 0.0,
        "per_core_percent": per_core,
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "frequency_mhz": round(freq.current, 0) if freq else None,
    }


@tool(
    description="RAM and swap usage in GB plus used percentage.",
    permission=PermissionLevel.READ_ONLY,
    tags=("system",),
)
def memory_info() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "total_gb": _gb(vm.total),
        "used_gb": _gb(vm.used),
        "available_gb": _gb(vm.available),
        "percent": vm.percent,
        "swap_total_gb": _gb(swap.total),
        "swap_percent": swap.percent,
    }


@tool(
    description="Disk usage for every mounted drive, in GB and percent.",
    permission=PermissionLevel.READ_ONLY,
    tags=("system",),
)
def disk_usage() -> list[dict[str, Any]]:
    disks: list[dict[str, Any]] = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue  # empty CD/card readers raise here on Windows
        disks.append(
            {
                "device": part.device,
                "mountpoint": part.mountpoint,
                "fstype": part.fstype,
                "total_gb": _gb(usage.total),
                "used_gb": _gb(usage.used),
                "free_gb": _gb(usage.free),
                "percent": usage.percent,
            }
        )
    return disks


@tool(
    description="Battery percentage, charging state and estimated minutes left. "
    "Returns present=false on desktops.",
    permission=PermissionLevel.READ_ONLY,
    tags=("system",),
)
def battery_status() -> dict[str, Any]:
    battery = getattr(psutil, "sensors_battery", lambda: None)()
    if battery is None:
        return {"present": False}
    secs = battery.secsleft
    minutes_left: int | None
    if secs in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) or secs < 0:
        minutes_left = None
    else:
        minutes_left = int(secs // 60)
    return {
        "present": True,
        "percent": round(battery.percent, 1),
        "plugged_in": battery.power_plugged,
        "minutes_left": minutes_left,
    }


@tool(
    description="Network status: hostname, local IP, per-interface addresses and "
    "bytes sent/received since boot.",
    permission=PermissionLevel.READ_ONLY,
    tags=("system", "network"),
)
def network_info() -> dict[str, Any]:
    counters = psutil.net_io_counters()
    stats = psutil.net_if_stats()
    interfaces: list[dict[str, Any]] = []
    for name, addrs in psutil.net_if_addrs().items():
        ipv4 = [a.address for a in addrs if a.family == socket.AF_INET]
        if not ipv4:
            continue
        interfaces.append(
            {
                "name": name,
                "addresses": ipv4,
                "up": bool(stats[name].is_up) if name in stats else None,
                "speed_mbps": stats[name].speed if name in stats else None,
            }
        )
    return {
        "hostname": socket.gethostname(),
        "local_ip": _primary_ip(),
        "interfaces": interfaces,
        "bytes_sent_gb": _gb(counters.bytes_sent),
        "bytes_recv_gb": _gb(counters.bytes_recv),
    }


def _primary_ip() -> str | None:
    """Best-effort local IP, without sending any traffic."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        return sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()


@tool(
    description="List running processes sorted by CPU or memory usage. "
    "Optionally filter by a case-insensitive name substring.",
    permission=PermissionLevel.READ_ONLY,
    tags=("system", "process"),
    untrusted_output=True,
)
def list_processes(
    sort_by: str = "cpu", limit: int = 10, name_contains: str | None = None
) -> list[dict[str, Any]]:
    if sort_by not in ("cpu", "memory"):
        raise ValueError("sort_by must be 'cpu' or 'memory'")
    limit = max(1, min(limit, 50))
    needle = (name_contains or "").lower()

    procs: list[psutil.Process] = []
    for proc in psutil.process_iter(["name"]):
        if needle and needle not in (proc.info["name"] or "").lower():
            continue
        try:
            proc.cpu_percent(None)  # prime the per-process CPU counter
            procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if sort_by == "cpu":
        time.sleep(0.3)  # sample window for meaningful CPU percentages

    rows: list[dict[str, Any]] = []
    cores = psutil.cpu_count(logical=True) or 1
    for proc in procs:
        try:
            with proc.oneshot():
                rows.append(
                    {
                        "pid": proc.pid,
                        "name": proc.name(),
                        "cpu_percent": round(proc.cpu_percent(None) / cores, 1),
                        "memory_mb": round(proc.memory_info().rss / (1024**2), 1),
                        "status": proc.status(),
                    }
                )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    key = "cpu_percent" if sort_by == "cpu" else "memory_mb"
    rows.sort(key=lambda r: r[key], reverse=True)
    return rows[:limit]
