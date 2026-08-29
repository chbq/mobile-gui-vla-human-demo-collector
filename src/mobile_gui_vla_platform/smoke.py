"""Screenshot -> deterministic tap -> screenshot smoke runner."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from .adb import ADBDeviceAdapter, ADBError
from .contracts import Frame, TapAction
from .paths import PathConfig


def _write_frame(frame: Frame, path: Path) -> dict:
    path.write_bytes(frame.png_bytes)
    metadata = frame.metadata()
    metadata["path"] = path.name
    metadata["sha256"] = hashlib.sha256(frame.png_bytes).hexdigest()
    return metadata


def run_smoke(
    adapter: ADBDeviceAdapter,
    *,
    output_dir: Path,
    action: TapAction,
    settle_seconds: float,
) -> dict:
    if settle_seconds < 0:
        raise ValueError("settle_seconds must be non-negative")
    output_dir.mkdir(parents=True, exist_ok=False)
    total_start = time.monotonic()
    started_wall = datetime.now(timezone.utc).isoformat()

    before = adapter.screenshot()
    before_metadata = _write_frame(before, output_dir / "screen_0.png")

    action_dispatch_ms = adapter.tap(action.x_px, action.y_px)
    settle_start = time.monotonic()
    time.sleep(settle_seconds)
    settle_ms = (time.monotonic() - settle_start) * 1000.0

    after = adapter.screenshot()
    after_metadata = _write_frame(after, output_dir / "screen_1.png")
    total_ms = (time.monotonic() - total_start) * 1000.0

    manifest = {
        "schema_version": "mobile-gui-vla.smoke.v0.1",
        "started_wall_time": started_wall,
        "finished_wall_time": datetime.now(timezone.utc).isoformat(),
        "device": adapter.get_device_info().to_dict(),
        "display": adapter.get_display_info().to_dict(),
        "screen_0": before_metadata,
        "action": action.to_dict(),
        "screen_1": after_metadata,
        "observable_ui_change": before_metadata["sha256"]
        != after_metadata["sha256"],
        "timing_ms": {
            "capture_before_ms": before.capture_ms,
            "action_dispatch_ms": action_dispatch_ms,
            "settle_ms": settle_ms,
            "capture_after_ms": after.capture_ms,
            "total_smoke_ms": total_ms,
        },
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run screenshot -> tap -> screenshot against one real ADB endpoint"
    )
    parser.add_argument("--serial", required=True, help="exact adb serial")
    parser.add_argument("--alias", help="human-friendly device identity")
    parser.add_argument("--x", type=int, required=True, help="tap x in screenshot pixels")
    parser.add_argument("--y", type=int, required=True, help="tap y in screenshot pixels")
    parser.add_argument("--settle-seconds", type=float, default=1.0)
    parser.add_argument("--adb", help="ADB executable path")
    parser.add_argument("--output", type=Path, help="new run directory")
    args = parser.parse_args(argv)

    paths = PathConfig.from_env()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or paths.runtime_root / "runs" / f"adb-smoke-{timestamp}"
    adapter = ADBDeviceAdapter(
        args.serial, alias=args.alias, adb_path=args.adb
    )
    try:
        manifest = run_smoke(
            adapter,
            output_dir=output,
            action=TapAction(args.x, args.y),
            settle_seconds=args.settle_seconds,
        )
    except (ADBError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(manifest, indent=2, sort_keys=True))
    print(f"run_directory={output}")
    return 0 if manifest["observable_ui_change"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
