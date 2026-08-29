"""Reproducible, best-effort host capability inventory."""

from __future__ import annotations

import argparse
import grp
import json
import os
import platform
import pwd
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .paths import PathConfig


def _command(command: Sequence[str], timeout: float = 15.0) -> dict[str, Any]:
    executable = shutil.which(command[0])
    if executable is None:
        return {"available": False, "command": list(command)}
    try:
        result = subprocess.run(
            [executable, *command[1:]],
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "available": True,
            "command": list(command),
            "error": type(exc).__name__,
        }
    return {
        "available": True,
        "command": list(command),
        "returncode": result.returncode,
        "stdout": result.stdout.strip()[:100_000],
        "stderr": result.stderr.strip()[:20_000],
    }


def _tool(name: str, relative: str | None = None) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    android_home = os.environ.get("ANDROID_HOME")
    if android_home and relative:
        candidate = Path(android_home) / relative
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _os_release() -> dict[str, str]:
    result: dict[str, str] = {}
    raw = _read_text(Path("/etc/os-release")) or ""
    for line in raw.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result[key] = value.strip().strip('"')
    return result


def _memory() -> dict[str, int]:
    values: dict[str, int] = {}
    raw = _read_text(Path("/proc/meminfo")) or ""
    for line in raw.splitlines():
        match = re.match(r"([^:]+):\s+(\d+)\s+kB", line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    return {
        "total_bytes": values.get("MemTotal", 0),
        "available_bytes": values.get("MemAvailable", 0),
        "swap_total_bytes": values.get("SwapTotal", 0),
        "swap_free_bytes": values.get("SwapFree", 0),
    }


def _cpu() -> dict[str, Any]:
    raw = _read_text(Path("/proc/cpuinfo")) or ""
    model_match = re.search(r"^(?:model name|Hardware)\s*:\s*(.+)$", raw, re.M)
    flags_match = re.search(r"^(?:flags|Features)\s*:\s*(.+)$", raw, re.M)
    flags = set(flags_match.group(1).split()) if flags_match else set()
    lscpu = _command(["lscpu"])
    topology: dict[str, int] = {}
    field_names = {
        "Socket(s)": "sockets",
        "Core(s) per socket": "cores_per_socket",
        "Thread(s) per core": "threads_per_core",
    }
    for line in lscpu.get("stdout", "").splitlines():
        if ":" not in line:
            continue
        name, value = (part.strip() for part in line.split(":", 1))
        if name in field_names and value.isdigit():
            topology[field_names[name]] = int(value)
    return {
        "architecture": platform.machine(),
        "model": model_match.group(1).strip() if model_match else None,
        "logical_cpu_count": os.cpu_count(),
        "topology": topology,
        "virtualization_flags": sorted(flags.intersection({"vmx", "svm"})),
        "lscpu": lscpu,
    }


def _identity_and_kvm(emulator: str | None) -> tuple[dict[str, Any], dict[str, str]]:
    uid = os.getuid()
    username = pwd.getpwuid(uid).pw_name
    group_names = sorted({grp.getgrgid(gid).gr_name for gid in os.getgroups()})
    kvm = Path("/dev/kvm")
    details: dict[str, Any] = {
        "exists": kvm.exists(),
        "readable": os.access(kvm, os.R_OK),
        "writable": os.access(kvm, os.W_OK),
        "current_user": username,
        "groups": group_names,
    }
    if not kvm.exists():
        return details, {"status": "BLOCKED", "reason": "/dev/kvm does not exist"}
    if not (details["readable"] and details["writable"]):
        return details, {
            "status": "BLOCKED",
            "reason": "/dev/kvm is not readable and writable by the current user",
        }
    if emulator is None:
        return details, {
            "status": "NOT_TESTED",
            "reason": "/dev/kvm is usable but Android emulator is not installed or exposed",
        }
    check = _command([emulator, "-accel-check"], timeout=30.0)
    details["emulator_accel_check"] = check
    combined = f"{check.get('stdout', '')}\n{check.get('stderr', '')}".lower()
    if check.get("returncode") == 0 and (
        "usable" in combined or "installed" in combined or "kvm" in combined
    ):
        return details, {
            "status": "READY",
            "reason": "/dev/kvm usable; emulator acceleration check passed",
        }
    return details, {
        "status": "BLOCKED",
        "reason": "emulator acceleration check did not pass",
    }


def collect_inventory(paths: PathConfig) -> dict[str, Any]:
    adb = _tool("adb", "platform-tools/adb")
    emulator = _tool("emulator", "emulator/emulator")
    sdkmanager = _tool("sdkmanager")
    kvm, avd_gate = _identity_and_kvm(emulator)
    container_raw = _read_text(Path("/proc/1/cgroup")) or ""

    android: dict[str, Any] = {
        "environment": {
            key: os.environ.get(key)
            for key in ("ANDROID_HOME", "ANDROID_USER_HOME", "ANDROID_AVD_HOME")
        },
        "adb": _command([adb, "version"]) if adb else {"available": False},
        "emulator": (
            _command([emulator, "-version"]) if emulator else {"available": False}
        ),
        "avds": (
            _command([emulator, "-list-avds"]) if emulator else {"available": False}
        ),
        "sdkmanager_installed": (
            _command([sdkmanager, "--list_installed"], timeout=30.0)
            if sdkmanager
            else {"available": False}
        ),
    }
    return {
        "schema_version": "mobile-gui-vla.host-inventory.v0.1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "host": {
            "hostname": socket.gethostname(),
            "kernel": platform.release(),
            "distribution": _os_release(),
            "machine_context": _command(["systemd-detect-virt"]),
            "container_markers_present": any(
                marker in container_raw for marker in ("docker", "containerd", "kubepods")
            ),
        },
        "cpu": _cpu(),
        "memory": _memory(),
        "storage": {
            "filesystems": _command(["df", "-PT"]),
            "block_devices": _command(
                ["lsblk", "-J", "-o", "NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,ROTA,MODEL"]
            ),
            "runtime_candidate": str(paths.runtime_root),
            "runtime_filesystem": _command(["df", "-PT", str(paths.runtime_root.parent)]),
        },
        "virtualization": kvm,
        "gpu": {
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "nvidia_smi": _command(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total,driver_version",
                    "--format=csv,noheader,nounits",
                ]
            ),
        },
        "android": android,
        "network": {
            "interfaces": _command(["ip", "-brief", "address"]),
            "default_route": _command(["ip", "route", "show", "default"]),
        },
        "time": {
            "wall_clock_utc": datetime.now(timezone.utc).isoformat(),
            "timezone": datetime.now().astimezone().tzname(),
            "timedatectl": _command(
                [
                    "timedatectl",
                    "show",
                    "--property=Timezone",
                    "--property=NTPSynchronized",
                    "--property=NTP",
                ]
            ),
        },
        "paths": {
            "project_root": str(paths.project_root),
            "runtime_root": str(paths.runtime_root),
            "cache_root": str(paths.cache_root),
            "data_root": str(paths.data_root),
            "shared_root": str(paths.shared_root) if paths.shared_root else None,
        },
        "avd_acceleration": avd_gate,
    }


def _gib(value: int) -> str:
    return f"{value / (1024 ** 3):.1f} GiB"


def _stdout(record: dict[str, Any], fallback: str = "unavailable") -> str:
    value = record.get("stdout") or record.get("stderr") or fallback
    return "\n".join(line.rstrip() for line in value.splitlines()).strip()


def render_report(inventory: dict[str, Any]) -> str:
    host = inventory["host"]
    cpu = inventory["cpu"]
    memory = inventory["memory"]
    avd = inventory["avd_acceleration"]
    distro = host["distribution"].get("PRETTY_NAME", "unknown")
    kvm = inventory["virtualization"]
    return f"""# Reference host inventory

Generated by `gui-vla-inventory` at `{inventory['captured_at']}`.

## Host and compute

- Host: `{host['hostname']}`
- OS: `{distro}`
- Kernel: `{host['kernel']}`
- Machine context: `{_stdout(host['machine_context'])}`
- Architecture: `{cpu['architecture']}`
- CPU: `{cpu['model'] or 'unknown'}`
- Logical CPUs: `{cpu['logical_cpu_count']}`
- Sockets / cores per socket / threads per core: `{cpu['topology'].get('sockets', 'unknown')}` / `{cpu['topology'].get('cores_per_socket', 'unknown')}` / `{cpu['topology'].get('threads_per_core', 'unknown')}`
- Virtualization flags: `{', '.join(cpu['virtualization_flags']) or 'none observed'}`
- RAM total / available: `{_gib(memory['total_bytes'])}` / `{_gib(memory['available_bytes'])}`
- Swap total / free: `{_gib(memory['swap_total_bytes'])}` / `{_gib(memory['swap_free_bytes'])}`

## Storage

- Candidate local runtime: `{inventory['storage']['runtime_candidate']}`

```text
{_stdout(inventory['storage']['runtime_filesystem'])}
```

## GPU inventory (informational only)

`CUDA_VISIBLE_DEVICES={inventory['gpu']['cuda_visible_devices']}`

```text
{_stdout(inventory['gpu']['nvidia_smi'])}
```

GPU availability is not an emulator or DeviceAdapter gate.

## Virtualization and Android tooling

- `/dev/kvm` exists: `{kvm['exists']}`
- `/dev/kvm` readable/writable: `{kvm['readable']}` / `{kvm['writable']}`
- Current user: `{kvm['current_user']}`
- Groups: `{', '.join(kvm['groups'])}`

ADB:

```text
{_stdout(inventory['android']['adb'])}
```

Emulator:

```text
{_stdout(inventory['android']['emulator'])}
```

Configured AVDs:

```text
{_stdout(inventory['android']['avds'], 'none')}
```

Installed Android packages:

```text
{_stdout(inventory['android']['sdkmanager_installed'])}
```

## Network and time

Default route:

```text
{_stdout(inventory['network']['default_route'])}
```

- Local timezone: `{inventory['time']['timezone']}`
- UTC wall clock: `{inventory['time']['wall_clock_utc']}`

Time synchronization:

```text
{_stdout(inventory['time']['timedatectl'])}
```

## Eligibility conclusion

```text
AVD_ACCELERATION: {avd['status']}
REASON: {avd['reason']}
```

This report determines current host capability only. It does not repair KVM,
install Android tooling, or claim that an Android endpoint exists.
"""


def _safe_host_alias(hostname: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", hostname).strip("-") or "host"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture Mobile GUI-VLA host inventory")
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--report-output", type=Path)
    args = parser.parse_args(argv)

    paths = PathConfig.from_env()
    inventory = collect_inventory(paths)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    json_output = args.json_output or paths.runtime_root / "inventory" / timestamp / "host.json"
    report_output = args.report_output or paths.project_root / "docs" / (
        f"host_inventory_{_safe_host_alias(inventory['host']['hostname'])}.md"
    )
    json_output.parent.mkdir(parents=True, exist_ok=True)
    report_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    report_output.write_text(render_report(inventory), encoding="utf-8")
    print(f"json={json_output}")
    print(f"report={report_output}")
    print(
        "AVD_ACCELERATION="
        f"{inventory['avd_acceleration']['status']}: "
        f"{inventory['avd_acceleration']['reason']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
