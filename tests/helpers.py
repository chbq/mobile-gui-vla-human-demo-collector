from __future__ import annotations

import struct
from pathlib import Path

from mobile_gui_vla_platform import DeviceInfo, DisplayInfo, Frame


class FakeAdapter:
    def __init__(self, alias: str = "avd-p0") -> None:
        self.alias = alias
        self.capture_index = 0
        self.calls = []

    def get_device_info(self) -> DeviceInfo:
        return DeviceInfo(
            device_alias=self.alias,
            adb_endpoint="emulator-fixture",
            endpoint_type="emulator",
            connection_state="device",
            manufacturer="fixture",
            model="fixture-avd",
            android_version="13",
            api_level="33",
            build_fingerprint="fixture/fingerprint",
            transport="EMULATOR",
        )

    def get_display_info(self) -> DisplayInfo:
        return DisplayInfo(1080, 2400, 420, 0, 1080, 2400)

    def screenshot(self) -> Frame:
        self.capture_index += 1
        png = (
            b"\x89PNG\r\n\x1a\n"
            + b"\x00" * 8
            + struct.pack(">II", 1080, 2400)
            + bytes([self.capture_index])
        )
        return Frame(
            frame_id=f"fixture-frame-{self.capture_index}",
            capture_wall_time=f"2026-08-27T00:00:0{self.capture_index}+00:00",
            capture_monotonic_time=float(self.capture_index),
            capture_ms=1.0,
            width_px=1080,
            height_px=2400,
            orientation=0,
            density_dpi=420,
            device_alias=self.alias,
            adb_endpoint="emulator-fixture",
            png_bytes=png,
        )

    def tap(self, x, y):
        self.calls.append(("tap", x, y))
        return 1.0

    def swipe(self, x0, y0, x1, y1, duration):
        self.calls.append(("swipe", x0, y0, x1, y1, duration))
        return 2.0

    def type_text(self, text):
        self.calls.append(("type", text))
        return 3.0

    def back(self):
        self.calls.append(("back",))
        return 4.0

    def home(self):
        self.calls.append(("home",))
        return 5.0


def service(root: Path, adapter: FakeAdapter | None = None):
    from mobile_gui_vla_data_lab.collector import CollectionService

    selected = adapter or FakeAdapter()
    return CollectionService(
        artifact_root=root,
        device_factories={"avd-p0": lambda: selected},
        platform_dependency={
            "base_commit": "0eb713a412ff97a66f282dbb36c09130b8b8897f",
            "dependency_commit": "4f8f08482391ff9da004742a5199af8936160ee0",
        },
        post_action_settle_seconds=0,
        stable_capture_max_samples=1,
    )


def task(task_id: str = "fixture-task", data_class: str = "normal"):
    return {
        "task_id": task_id,
        "instruction": "Perform a benign fixture task.",
        "task_family": "navigation",
        "capability_labels": ["tap"],
        "data_class": data_class,
    }


def token(session):
    return {
        "frame_id": session.current_metadata["frame_id"],
        "frame_sha256": session.current_metadata["sha256"],
    }
