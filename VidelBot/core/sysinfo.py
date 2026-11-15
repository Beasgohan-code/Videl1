"""
Container-aware system resource telemetry (cgroup v2 / cgroup v1 + psutil fallback).

On managed container platforms (Docker, Render, Railway, Koyeb, Fly.io, Kubernetes),
`psutil.virtual_memory()` and `os.cpu_count()` report the underlying host machine
rather than the container's quota. This module inspects cgroup v2 and cgroup v1
limits first and falls back to `psutil` / `os.sched_getaffinity`, providing accurate
CPU, RAM, process RSS, and disk metrics for `/status`, `/stats`, `/health`,
`encoder_threads()`, and the `/admin` web console.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import psutil

_MB = 1024 * 1024
_UNBOUNDED_BYTES = 1 << 60


def _read_text(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def cgroup_cpu_quota() -> float | None:
    """Return effective cgroup CPU quota (e.g. `0.5`, `2.0`) or `None` when unlimited."""
    v2 = _read_text("/sys/fs/cgroup/cpu.max")
    if v2:
        parts = v2.split()
        if len(parts) == 2 and parts[0] != "max":
            try:
                quota, period = int(parts[0]), int(parts[1])
                if quota > 0 and period > 0:
                    return quota / period
            except ValueError:
                pass
    q1 = _read_text("/sys/fs/cgroup/cpu/cpu.cfs_quota_us")
    p1 = _read_text("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if q1 and p1:
        try:
            quota, period = int(q1), int(p1)
            if quota > 0 and period > 0:
                return quota / period
        except ValueError:
            pass
    return None


def cgroup_memory_limit_bytes() -> int | None:
    """Return container RAM limit in bytes if bounded by cgroup v2 or v1, else `None`."""
    for p in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        raw = _read_text(p)
        if not raw or raw == "max":
            continue
        try:
            val = int(raw)
            if 0 < val < _UNBOUNDED_BYTES:
                return val
        except ValueError:
            continue
    return None


def cgroup_memory_current_bytes() -> int | None:
    """Return current container RAM usage in bytes if exposed by cgroup v2 or v1."""
    for p in ("/sys/fs/cgroup/memory.current", "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
        raw = _read_text(p)
        if not raw:
            continue
        try:
            val = int(raw)
            if val >= 0:
                return val
        except ValueError:
            continue
    return None


def effective_cpu_count() -> int:
    """Effective integer CPU core count respecting cgroup quotas and CPU affinity."""
    cg = cgroup_cpu_quota()
    if cg and cg > 0:
        return max(1, int(round(cg)))
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def process_rss_bytes() -> int:
    try:
        return int(psutil.Process().memory_info().rss)
    except Exception:
        return 0


def system_snapshot(disk_path: str = ".") -> dict[str, Any]:
    """Return a unified host + container resource snapshot."""
    vm = psutil.virtual_memory()
    cg_limit = cgroup_memory_limit_bytes()
    cg_used = cgroup_memory_current_bytes()
    rss = process_rss_bytes()

    if cg_limit and cg_limit < vm.total:
        mem_total = cg_limit
        mem_used = min(cg_limit, cg_used if cg_used is not None else rss)
        mem_pct = round((mem_used * 100.0 / mem_total), 1) if mem_total else 0.0
        containerized_mem = True
    else:
        mem_total = int(vm.total)
        mem_used = int(vm.used)
        mem_pct = round(float(vm.percent), 1)
        containerized_mem = False

    cg_cpu = cgroup_cpu_quota()
    cpus = effective_cpu_count()
    cpu_pct = round(float(psutil.cpu_percent(interval=None)), 1)

    target_path = disk_path if os.path.exists(disk_path) else "."
    try:
        du = psutil.disk_usage(target_path)
        disk_free = int(du.free)
        disk_total = int(du.total)
        disk_pct = round(float(du.percent), 1)
    except Exception:
        disk_free = disk_total = 0
        disk_pct = 0.0

    try:
        net = psutil.net_io_counters()
        bytes_sent = int(net.bytes_sent)
        bytes_recv = int(net.bytes_recv)
    except Exception:
        bytes_sent = bytes_recv = 0

    ram_mb = mem_total // _MB
    return {
        "cpu_percent": cpu_pct,
        "cpu_cores": cpus,
        "cpu_quota": round(cg_cpu, 2) if cg_cpu else None,
        "mem_percent": mem_pct,
        "mem_used_bytes": mem_used,
        "mem_total_bytes": mem_total,
        "rss_bytes": rss,
        "container_mem": containerized_mem,
        "disk_free_bytes": disk_free,
        "disk_total_bytes": disk_total,
        "disk_percent": disk_pct,
        "net_sent_bytes": bytes_sent,
        "net_recv_bytes": bytes_recv,
        "is_low_end": cpus <= 2 or (0 < ram_mb <= 1536),
    }


def memory_pressure(threshold_pct: float = 90.0) -> tuple[bool, float]:
    """Return `(is_over_threshold, current_mem_percent)` using cgroup-aware container RAM."""
    if threshold_pct <= 0:
        return False, 0.0
    try:
        vm = psutil.virtual_memory()
        cg_limit = cgroup_memory_limit_bytes()
        cg_used = cgroup_memory_current_bytes()
        if cg_limit and cg_limit < vm.total:
            used = min(cg_limit, cg_used if cg_used is not None else process_rss_bytes())
            pct = round((used * 100.0 / cg_limit), 1) if cg_limit else 0.0
        else:
            pct = round(float(vm.percent), 1)
        return pct >= float(threshold_pct), pct
    except Exception:
        return False, 0.0

