"""Minimal ADB endpoint discovery and DeviceAdapter v0 backend."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Sequence

from .contracts import DeviceInfo, DisplayInfo, Frame, SwipeAction, TapAction, TypeAction

Runner = Callable[[Sequence[str], float, bool], subprocess.CompletedProcess]


def _default_runner(
    command: Sequence[str], timeout: float, text: bool
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command),
        capture_output=True,
        check=False,
        timeout=timeout,
        text=text,
    )


def _alias_for(serial: str) -> str:
    return f"device-{hashlib.sha256(serial.encode('utf-8')).hexdigest()[:10]}"


def _transport(serial: str, endpoint_type: str) -> str:
    if endpoint_type == "emulator":
        return "EMULATOR"
    if ":" in serial:
        return "NETWORK"
    return "USB"


class ADBError(RuntimeError):
    pass


class ADBDeviceAdapter:
    """The deliberately small Day-1 device interface backed by ADB."""

    def __init__(
        self,
        serial: str,
        *,
        alias: str | None = None,
        adb_path: str | None = None,
        timeout: float = 15.0,
        runner: Runner = _default_runner,
    ) -> None:
        if not serial.strip():
            raise ValueError("ADB serial must not be empty")
        self.serial = serial
        self.alias = alias or _alias_for(serial)
        self.adb_path = adb_path or os.environ.get("ADB", "adb")
        self.timeout = timeout
        self._runner = runner

    def _adb(
        self, args: Sequence[str], *, text: bool = True, check: bool = True
    ) -> subprocess.CompletedProcess:
        command = [self.adb_path, "-s", self.serial, *args]
        try:
            result = self._runner(command, self.timeout, text)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ADBError(f"ADB command failed to start or timed out: {command}") from exc
        if check and result.returncode != 0:
            stderr = result.stderr if text else result.stderr.decode("utf-8", "replace")
            raise ADBError(f"ADB command failed ({result.returncode}): {stderr.strip()}")
        return result

    def _shell_text(self, *args: str) -> str:
        return self._adb(["shell", *args]).stdout.strip()

    def is_alive(self) -> bool:
        result = self._adb(["get-state"], check=False)
        return result.returncode == 0 and result.stdout.strip() == "device"

    def _property(self, name: str) -> str | None:
        value = self._shell_text("getprop", name)
        return value or None

    def get_device_info(self) -> DeviceInfo:
        emulator = self.serial.startswith("emulator-") or self._property(
            "ro.kernel.qemu"
        ) == "1"
        endpoint_type = "emulator" if emulator else "physical"
        state_result = self._adb(["get-state"], check=False)
        state = state_result.stdout.strip() if state_result.returncode == 0 else "unknown"
        return DeviceInfo(
            device_alias=self.alias,
            adb_endpoint=self.serial,
            endpoint_type=endpoint_type,
            connection_state=state,
            manufacturer=self._property("ro.product.manufacturer"),
            model=self._property("ro.product.model"),
            android_version=self._property("ro.build.version.release"),
            api_level=self._property("ro.build.version.sdk"),
            build_fingerprint=self._property("ro.build.fingerprint"),
            transport=_transport(self.serial, endpoint_type),
        )

    @staticmethod
    def _parse_size(output: str) -> tuple[int, int, int | None, int | None]:
        physical = re.search(r"Physical size:\s*(\d+)x(\d+)", output)
        override = re.search(r"Override size:\s*(\d+)x(\d+)", output)
        selected = override or physical or re.search(r"(\d+)x(\d+)", output)
        if selected is None:
            raise ADBError(f"Unable to parse display size from: {output!r}")
        physical_values = (
            (int(physical.group(1)), int(physical.group(2))) if physical else (None, None)
        )
        return (
            int(selected.group(1)),
            int(selected.group(2)),
            physical_values[0],
            physical_values[1],
        )

    def get_display_info(self) -> DisplayInfo:
        width, height, physical_width, physical_height = self._parse_size(
            self._shell_text("wm", "size")
        )
        density_output = self._shell_text("wm", "density")
        densities = re.findall(r"(?:Physical|Override)?\s*density:\s*(\d+)", density_output)
        if not densities:
            densities = re.findall(r"\b(\d+)\b", density_output)
        density = int(densities[-1]) if densities else None

        orientation_output = self._shell_text("dumpsys", "input")
        orientation = self._parse_orientation(orientation_output)
        if orientation is None:
            orientation = self._parse_orientation(
                self._shell_text("dumpsys", "display")
            )
        return DisplayInfo(
            width_px=width,
            height_px=height,
            density_dpi=density,
            orientation=orientation,
            physical_width_px=physical_width,
            physical_height_px=physical_height,
        )

    @staticmethod
    def _parse_orientation(output: str) -> int | None:
        patterns = (
            r"SurfaceOrientation:\s*(\d+)",
            r"Viewport INTERNAL: displayId=0,[^\n]*orientation=(\d+)[^\n]*isActive=\[1\]",
            r"mViewports=\[[^\n]*orientation=(\d+)[^\n]*isActive=true",
            r"mCurrentOrientation=(\d+)",
            r'DisplayDeviceInfo\{"Built-in Screen"[^\n]*rotation (\d+)',
        )
        for pattern in patterns:
            match = re.search(pattern, output)
            if match:
                return int(match.group(1))
        return None

    def screenshot(self) -> Frame:
        display = self.get_display_info()
        start = time.monotonic()
        wall_time = datetime.now(timezone.utc).isoformat()
        result = self._adb(["exec-out", "screencap", "-p"], text=False)
        end = time.monotonic()
        payload = bytes(result.stdout)
        if len(payload) < 24 or payload[:8] != b"\x89PNG\r\n\x1a\n":
            raise ADBError("screencap did not return a valid PNG stream")
        png_width, png_height = struct.unpack(">II", payload[16:24])
        if (png_width, png_height) != (display.width_px, display.height_px):
            display = DisplayInfo(
                width_px=png_width,
                height_px=png_height,
                density_dpi=display.density_dpi,
                orientation=display.orientation,
                physical_width_px=display.physical_width_px,
                physical_height_px=display.physical_height_px,
            )
        return Frame(
            frame_id=str(uuid.uuid4()),
            capture_wall_time=wall_time,
            capture_monotonic_time=start,
            capture_ms=(end - start) * 1000.0,
            width_px=display.width_px,
            height_px=display.height_px,
            orientation=display.orientation,
            density_dpi=display.density_dpi,
            device_alias=self.alias,
            adb_endpoint=self.serial,
            png_bytes=payload,
        )

    def tap(self, x_px: int, y_px: int) -> float:
        action = TapAction(x_px=x_px, y_px=y_px)
        action.validate(self.get_display_info())
        start = time.monotonic()
        self._adb(["shell", "input", "tap", str(x_px), str(y_px)])
        return (time.monotonic() - start) * 1000.0

    def swipe(
        self,
        x0_px: int,
        y0_px: int,
        x1_px: int,
        y1_px: int,
        duration_ms: int,
    ) -> float:
        action = SwipeAction(x0_px, y0_px, x1_px, y1_px, duration_ms)
        action.validate(self.get_display_info())
        start = time.monotonic()
        self._adb(
            [
                "shell",
                "input",
                "swipe",
                str(x0_px),
                str(y0_px),
                str(x1_px),
                str(y1_px),
                str(duration_ms),
            ]
        )
        return (time.monotonic() - start) * 1000.0

    def back(self) -> float:
        start = time.monotonic()
        self._adb(["shell", "input", "keyevent", "BACK"])
        return (time.monotonic() - start) * 1000.0

    def home(self) -> float:
        start = time.monotonic()
        self._adb(["shell", "input", "keyevent", "HOME"])
        return (time.monotonic() - start) * 1000.0

    def type_text(self, text: str) -> float:
        action = TypeAction(text)
        action.validate()
        encoded = _format_adb_input_text(text)
        start = time.monotonic()
        self._adb(["shell", "input", "text", encoded])
        return (time.monotonic() - start) * 1000.0


def _format_adb_input_text(text: str) -> str:
    """Encode validated printable ASCII for Android's ``input text`` command."""

    escaped = text.replace("\\", "\\\\")
    for character in ";|`'\"&<>()#$":
        escaped = escaped.replace(character, "\\" + character)
    return escaped.replace(" ", "%s")


def discover_endpoints(
    *,
    adb_path: str | None = None,
    shareable: bool = False,
    runner: Runner = _default_runner,
) -> list[dict]:
    executable = adb_path or os.environ.get("ADB", "adb")
    try:
        result = runner([executable, "devices", "-l"], 15.0, True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ADBError(f"Unable to execute {executable!r}") from exc
    if result.returncode != 0:
        raise ADBError(result.stderr.strip() or "adb devices failed")

    endpoints: list[dict] = []
    for line in result.stdout.splitlines()[1:]:
        if not line.strip() or line.startswith("*"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        serial, state = fields[:2]
        alias = _alias_for(serial)
        record: dict = {
            "device_alias": alias,
            "adb_endpoint": alias if shareable else serial,
            "connection_state": state,
        }
        if state == "device":
            try:
                info = ADBDeviceAdapter(
                    serial,
                    alias=alias,
                    adb_path=executable,
                    runner=runner,
                ).get_device_info()
                record.update(info.to_dict())
                if shareable:
                    record["adb_endpoint"] = alias
                display = ADBDeviceAdapter(
                    serial,
                    alias=alias,
                    adb_path=executable,
                    runner=runner,
                ).get_display_info()
                record["display"] = display.to_dict()
            except ADBError as exc:
                record["query_error"] = str(exc)
        endpoints.append(record)
    return endpoints


def discovery_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enumerate real Android ADB endpoints")
    parser.add_argument("--adb", default=None, help="ADB executable path")
    parser.add_argument(
        "--shareable",
        action="store_true",
        help="replace serials with stable hashed aliases",
    )
    parser.add_argument("--output", type=Path, help="optional JSON output path")
    args = parser.parse_args(argv)
    exit_code = 0
    try:
        endpoints = discover_endpoints(adb_path=args.adb, shareable=args.shareable)
        payload = {
            "schema_version": "mobile-gui-vla.devices.v0.1",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "gate": {
                "status": "READY" if endpoints else "BLOCKED",
                "reason": (
                    f"{len(endpoints)} ADB endpoint(s) visible"
                    if endpoints
                    else "adb ran successfully but no Android endpoint is visible"
                ),
            },
            "endpoints": endpoints,
        }
    except ADBError as exc:
        payload = {
            "schema_version": "mobile-gui-vla.devices.v0.1",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "gate": {"status": "BLOCKED", "reason": str(exc)},
            "endpoints": [],
        }
        exit_code = 2
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if exit_code:
        print(f"ADB discovery failed: {payload['gate']['reason']}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(discovery_main())
