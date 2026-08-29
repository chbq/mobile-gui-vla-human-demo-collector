"""Bounded preview encoding and visual-stability helpers."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO

from PIL import Image


PREVIEW_MAX_SIZE = (432, 960)
SIGNATURE_SIZE = (54, 120)


@lru_cache(maxsize=16)
def preview_jpeg(png_bytes: bytes) -> bytes:
    """Return a small browser preview while raw evidence stays full-resolution PNG."""

    with Image.open(BytesIO(png_bytes)) as image:
        converted = image.convert("RGB")
        converted.thumbnail(PREVIEW_MAX_SIZE, Image.Resampling.BILINEAR)
        output = BytesIO()
        converted.save(output, format="JPEG", quality=68, optimize=True)
        return output.getvalue()


def visual_signature(png_bytes: bytes) -> bytes:
    """Return a fixed-size grayscale signature for bounded stability checks."""

    with Image.open(BytesIO(png_bytes)) as image:
        return image.convert("L").resize(
            SIGNATURE_SIZE, Image.Resampling.BILINEAR
        ).tobytes()


def mean_absolute_delta(left: bytes, right: bytes) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("visual signatures must have the same non-zero length")
    return sum(abs(a - b) for a, b in zip(left, right)) / len(left)
