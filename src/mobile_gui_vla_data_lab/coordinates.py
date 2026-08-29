"""Strict browser-to-captured-frame coordinate transforms."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ImageViewport:
    """CSS viewport containing an aspect-ratio-preserved screenshot image."""

    x: float
    y: float
    width: float
    height: float

    def validate(self) -> None:
        values = (self.x, self.y, self.width, self.height)
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
            raise ValueError("viewport values must be finite numbers")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("viewport width and height must be positive")


@dataclass(frozen=True)
class RenderedImageRect:
    x: float
    y: float
    width: float
    height: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class MappedPoint:
    x_px: int
    y_px: int
    rendered_image_rect: RenderedImageRect
    pointer_x: float
    pointer_y: float
    frame_width_px: int
    frame_height_px: int
    orientation: int | None

    def provenance(self) -> dict[str, Any]:
        return {
            "kind": "css_contain_to_original_frame_pixels",
            "rendered_image_rect": self.rendered_image_rect.to_dict(),
            "pointer": {"x": self.pointer_x, "y": self.pointer_y},
            "original_frame": {
                "width_px": self.frame_width_px,
                "height_px": self.frame_height_px,
                "orientation": self.orientation,
            },
            "result": {"x_px": self.x_px, "y_px": self.y_px},
        }


def rendered_image_rect(
    frame_width_px: int,
    frame_height_px: int,
    viewport: ImageViewport,
) -> RenderedImageRect:
    _validate_frame_size(frame_width_px, frame_height_px)
    viewport.validate()
    scale = min(viewport.width / frame_width_px, viewport.height / frame_height_px)
    width = frame_width_px * scale
    height = frame_height_px * scale
    return RenderedImageRect(
        x=viewport.x + (viewport.width - width) / 2.0,
        y=viewport.y + (viewport.height - height) / 2.0,
        width=width,
        height=height,
    )


def map_pointer(
    *,
    pointer_x: float,
    pointer_y: float,
    viewport: ImageViewport,
    frame_width_px: int,
    frame_height_px: int,
    orientation: int | None,
) -> MappedPoint:
    if any(
        type(value) not in (int, float) or not math.isfinite(value)
        for value in (pointer_x, pointer_y)
    ):
        raise ValueError("pointer coordinates must be finite numbers")
    image = rendered_image_rect(frame_width_px, frame_height_px, viewport)
    if not image.x <= pointer_x < image.x + image.width:
        raise ValueError("pointer x is outside the rendered screenshot")
    if not image.y <= pointer_y < image.y + image.height:
        raise ValueError("pointer y is outside the rendered screenshot")
    x_px = int((pointer_x - image.x) * frame_width_px / image.width)
    y_px = int((pointer_y - image.y) * frame_height_px / image.height)
    if not (0 <= x_px < frame_width_px and 0 <= y_px < frame_height_px):
        raise ValueError("mapped pointer is outside the original frame")
    return MappedPoint(
        x_px=x_px,
        y_px=y_px,
        rendered_image_rect=image,
        pointer_x=float(pointer_x),
        pointer_y=float(pointer_y),
        frame_width_px=frame_width_px,
        frame_height_px=frame_height_px,
        orientation=orientation,
    )


def map_drag(
    *,
    start_x: float,
    start_y: float,
    end_x: float,
    end_y: float,
    viewport: ImageViewport,
    frame_width_px: int,
    frame_height_px: int,
    orientation: int | None,
) -> tuple[MappedPoint, MappedPoint]:
    common = {
        "viewport": viewport,
        "frame_width_px": frame_width_px,
        "frame_height_px": frame_height_px,
        "orientation": orientation,
    }
    return (
        map_pointer(pointer_x=start_x, pointer_y=start_y, **common),
        map_pointer(pointer_x=end_x, pointer_y=end_y, **common),
    )


def _validate_frame_size(width: int, height: int) -> None:
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError("frame dimensions must be positive integers")
