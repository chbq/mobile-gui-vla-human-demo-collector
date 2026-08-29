"""Opt-in raw ADB screenrecord capture for qualitative trajectory evidence."""

from __future__ import annotations

import hashlib
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from .adb import ADBError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_mp4_container(path: Path) -> list[str]:
    """Validate complete top-level ISO BMFF boxes without a media dependency."""

    payload = path.read_bytes()
    boxes: list[str] = []
    offset = 0
    while offset < len(payload):
        remaining = len(payload) - offset
        if remaining < 8:
            raise ValueError(f"truncated MP4 box header at byte {offset}")
        size = int.from_bytes(payload[offset : offset + 4], "big")
        box_type = payload[offset + 4 : offset + 8].decode("ascii", "replace")
        header_size = 8
        if size == 1:
            if remaining < 16:
                raise ValueError(f"truncated extended MP4 box header at byte {offset}")
            size = int.from_bytes(payload[offset + 8 : offset + 16], "big")
            header_size = 16
        elif size == 0:
            size = remaining
        if size < header_size or size > remaining:
            raise ValueError(
                f"invalid MP4 box {box_type!r} size={size} at byte {offset}"
            )
        boxes.append(box_type)
        offset += size

    missing = {"ftyp", "mdat", "moov"} - set(boxes)
    if missing:
        raise ValueError(f"MP4 is missing required top-level boxes: {sorted(missing)}")
    return boxes


def build_visual_artifact_metadata(
    path: Path,
    *,
    started_at: str,
    stopped_at: str,
    remote_path: str,
    requested_size: str | None = None,
    requested_bitrate: int | None = None,
    requested_time_limit_seconds: int | None = None,
) -> dict[str, Any]:
    payload = path.read_bytes()
    if not payload:
        raise ValueError("screen recording is empty")
    boxes = validate_mp4_container(path)
    return {
        "type": "raw_device_screen_recording",
        "capture_method": "adb_shell_screenrecord",
        "evidence_role": "diagnostic_demo_not_clean_latency",
        "host_path": str(path.resolve()),
        "remote_path": remote_path,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "container_validation": "iso_bmff_required_boxes_present",
        "top_level_boxes": boxes,
        "started_at": started_at,
        "stopped_at": stopped_at,
        "requested_size": requested_size,
        "requested_bitrate": requested_bitrate,
        "requested_time_limit_seconds": requested_time_limit_seconds,
    }


class ADBScreenRecorder:
    """Own one bounded adb shell screenrecord process and verified pull."""

    def __init__(
        self,
        *,
        serial: str,
        host_path: Path,
        adb_path: str = "adb",
        size: str | None = None,
        bitrate: int | None = None,
        time_limit_seconds: int | None = 180,
    ) -> None:
        if not serial.strip():
            raise ValueError("ADB serial must not be empty")
        if size is not None and not _valid_size(size):
            raise ValueError("size must be WIDTHxHEIGHT with positive integers")
        if bitrate is not None and bitrate <= 0:
            raise ValueError("bitrate must be positive")
        if time_limit_seconds is not None and not 1 <= time_limit_seconds <= 180:
            raise ValueError("time_limit_seconds must be in [1, 180]")
        self.serial = serial
        self.host_path = Path(host_path)
        self.adb_path = adb_path
        self.size = size
        self.bitrate = bitrate
        self.time_limit_seconds = time_limit_seconds
        self.remote_path = f"/sdcard/gui_vla_{uuid4().hex}.mp4"
        self.process: subprocess.Popen[str] | None = None
        self.started_at: str | None = None
        self.device_pid: str | None = None

    def start(self) -> None:
        if self.process is not None:
            raise RuntimeError("screen recorder already started")
        existing_pids = self._screenrecord_pids(timeout=5.0)
        if existing_pids:
            raise ADBError(
                "refusing to start while device screenrecord is already running: "
                f"{existing_pids}"
            )
        self.host_path.parent.mkdir(parents=True, exist_ok=True)
        if self.host_path.exists():
            raise FileExistsError(self.host_path)
        command = [
            self.adb_path,
            "-s",
            self.serial,
            "shell",
            "screenrecord",
        ]
        if self.size is not None:
            command.extend(["--size", self.size])
        if self.bitrate is not None:
            command.extend(["--bit-rate", str(self.bitrate)])
        if self.time_limit_seconds is not None:
            command.extend(["--time-limit", str(self.time_limit_seconds)])
        command.append(self.remote_path)
        self.started_at = _utc_now()
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise ADBError(f"unable to start adb screenrecord: {exc}") from exc
        self.device_pid = self._wait_for_device_pid(timeout=5.0)

    def stop_and_pull(self, *, timeout: float = 15.0) -> dict[str, Any]:
        if self.process is None or self.started_at is None:
            raise RuntimeError("screen recorder is not running")
        process = self.process
        if process.poll() is None:
            device_pid = self.device_pid or self._wait_for_device_pid(timeout=2.0)
            stop = self._run_adb(["shell", "kill", "-2", device_pid], timeout)
            if stop.returncode != 0:
                raise ADBError(
                    "unable to signal device screenrecord process "
                    f"{device_pid}: {stop.stderr.strip()}"
                )
        try:
            _, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.terminate()
            process.communicate(timeout=5.0)
            raise ADBError("adb screenrecord did not stop within timeout") from exc
        stopped_at = _utc_now()

        pull = self._run_adb(["pull", self.remote_path, str(self.host_path)], timeout)
        if pull.returncode != 0:
            raise ADBError(f"screenrecord pull failed: {pull.stderr.strip()}")
        try:
            artifact = build_visual_artifact_metadata(
                self.host_path,
                started_at=self.started_at,
                stopped_at=stopped_at,
                remote_path=self.remote_path,
                requested_size=self.size,
                requested_bitrate=self.bitrate,
                requested_time_limit_seconds=self.time_limit_seconds,
            )
        except (OSError, ValueError) as exc:
            raise ADBError(f"pulled screen recording is invalid: {exc}") from exc

        remove = self._run_adb(["shell", "rm", "-f", self.remote_path], timeout)
        artifact["remote_removed_after_verified_pull"] = remove.returncode == 0
        artifact["status"] = "COMPLETE"
        artifact["stop_method"] = "device_sigint"
        artifact["device_screenrecord_pid"] = self.device_pid
        artifact["screenrecord_process_returncode"] = process.returncode
        artifact["screenrecord_stderr"] = stderr.strip() or None
        return artifact

    def _screenrecord_pids(self, *, timeout: float) -> list[str]:
        result = self._run_adb(["shell", "pidof", "screenrecord"], timeout)
        if result.returncode not in {0, 1}:
            raise ADBError(f"unable to query screenrecord pid: {result.stderr.strip()}")
        return result.stdout.split()

    def _wait_for_device_pid(self, *, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                _, stderr = self.process.communicate()
                raise ADBError(
                    "device screenrecord exited before startup completed: "
                    f"{stderr.strip()}"
                )
            pids = self._screenrecord_pids(timeout=min(timeout, 5.0))
            if len(pids) == 1:
                return pids[0]
            if len(pids) > 1:
                raise ADBError(f"multiple device screenrecord processes found: {pids}")
            time.sleep(0.1)
        raise ADBError("device screenrecord pid did not appear before timeout")

    def _run_adb(
        self, args: Sequence[str], timeout: float
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self.adb_path, "-s", self.serial, *args],
                capture_output=True,
                check=False,
                text=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ADBError(f"ADB command failed: {args!r}") from exc


def _valid_size(value: str) -> bool:
    parts = value.lower().split("x")
    if len(parts) != 2:
        return False
    try:
        width, height = (int(part) for part in parts)
    except ValueError:
        return False
    return width > 0 and height > 0
