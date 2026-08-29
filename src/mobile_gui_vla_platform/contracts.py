"""Small public contracts at the device/model boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, TypeAlias


MAX_SWIPE_DURATION_MS = 10_000
MAX_WAIT_DURATION_MS = 60_000
MAX_TYPE_TEXT_LENGTH = 1_024


@dataclass(frozen=True)
class DisplayInfo:
    width_px: int
    height_px: int
    density_dpi: int | None
    orientation: int | None
    physical_width_px: int | None = None
    physical_height_px: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DeviceInfo:
    device_alias: str
    adb_endpoint: str
    endpoint_type: str
    connection_state: str
    manufacturer: str | None
    model: str | None
    android_version: str | None
    api_level: str | None
    build_fingerprint: str | None
    transport: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Frame:
    frame_id: str
    capture_wall_time: str
    capture_monotonic_time: float
    capture_ms: float
    width_px: int
    height_px: int
    orientation: int | None
    density_dpi: int | None
    device_alias: str
    adb_endpoint: str
    png_bytes: bytes

    def metadata(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("png_bytes")
        return result


@dataclass(frozen=True)
class TapAction:
    x_px: int
    y_px: int

    def validate(self, display: DisplayInfo) -> None:
        if type(self.x_px) is not int or type(self.y_px) is not int:
            raise ValueError("x_px and y_px must be integers")
        if not 0 <= self.x_px < display.width_px:
            raise ValueError(
                f"x_px={self.x_px} is outside [0, {display.width_px})"
            )
        if not 0 <= self.y_px < display.height_px:
            raise ValueError(
                f"y_px={self.y_px} is outside [0, {display.height_px})"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"type": "tap", "x_px": self.x_px, "y_px": self.y_px}


@dataclass(frozen=True)
class SwipeAction:
    x0_px: int
    y0_px: int
    x1_px: int
    y1_px: int
    duration_ms: int

    def validate(self, display: DisplayInfo) -> None:
        if any(
            type(value) is not int
            for value in (
                self.x0_px,
                self.y0_px,
                self.x1_px,
                self.y1_px,
                self.duration_ms,
            )
        ):
            raise ValueError("swipe coordinates and duration_ms must be integers")
        for name, value, limit in (
            ("x0_px", self.x0_px, display.width_px),
            ("y0_px", self.y0_px, display.height_px),
            ("x1_px", self.x1_px, display.width_px),
            ("y1_px", self.y1_px, display.height_px),
        ):
            if not 0 <= value < limit:
                raise ValueError(f"{name}={value} is outside [0, {limit})")
        if not 1 <= self.duration_ms <= MAX_SWIPE_DURATION_MS:
            raise ValueError(
                "duration_ms must be in "
                f"[1, {MAX_SWIPE_DURATION_MS}] for swipe"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "swipe",
            "x0_px": self.x0_px,
            "y0_px": self.y0_px,
            "x1_px": self.x1_px,
            "y1_px": self.y1_px,
            "duration_ms": self.duration_ms,
        }


@dataclass(frozen=True)
class BackAction:
    def to_dict(self) -> dict[str, str]:
        return {"type": "key", "key": "BACK"}


@dataclass(frozen=True)
class HomeAction:
    def to_dict(self) -> dict[str, str]:
        return {"type": "key", "key": "HOME"}


@dataclass(frozen=True)
class TypeAction:
    text: str

    def validate(self) -> None:
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        if not self.text:
            raise ValueError("text must not be empty")
        if len(self.text) > MAX_TYPE_TEXT_LENGTH:
            raise ValueError(
                f"text must contain at most {MAX_TYPE_TEXT_LENGTH} characters"
            )
        if any(
            ord(character) < 0x20 or ord(character) > 0x7E
            for character in self.text
        ):
            raise ValueError("text must contain printable ASCII only")

    def to_dict(self) -> dict[str, Any]:
        return {"type": "type", "text": self.text}


@dataclass(frozen=True)
class WaitAction:
    duration_ms: int

    def validate(self) -> None:
        if type(self.duration_ms) is not int:
            raise ValueError("wait duration_ms must be an integer")
        if not 0 <= self.duration_ms <= MAX_WAIT_DURATION_MS:
            raise ValueError(
                "duration_ms must be in "
                f"[0, {MAX_WAIT_DURATION_MS}] for wait"
            )

    def to_dict(self) -> dict[str, Any]:
        return {"type": "wait", "duration_ms": self.duration_ms}


@dataclass(frozen=True)
class DoneAction:
    def to_dict(self) -> dict[str, str]:
        return {"type": "done"}


CanonicalAction: TypeAlias = (
    TapAction
    | SwipeAction
    | TypeAction
    | BackAction
    | HomeAction
    | WaitAction
    | DoneAction
)
