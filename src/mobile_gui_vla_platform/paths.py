"""Explicit runtime paths with environment-variable overrides."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PathConfig:
    project_root: Path
    runtime_root: Path
    cache_root: Path
    data_root: Path
    shared_root: Path | None

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> "PathConfig":
        discovered = project_root or Path(__file__).resolve().parents[2]
        root = Path(os.environ.get("GUI_VLA_ROOT", discovered)).expanduser().resolve()
        shared = os.environ.get("GUI_VLA_SHARED_ROOT")
        return cls(
            project_root=discovered.resolve(),
            runtime_root=Path(
                os.environ.get("GUI_VLA_RUNTIME_ROOT", root / "runtime")
            ).expanduser().resolve(),
            cache_root=Path(
                os.environ.get("GUI_VLA_CACHE_ROOT", root / "cache")
            ).expanduser().resolve(),
            data_root=Path(
                os.environ.get("GUI_VLA_DATA_ROOT", root / "data")
            ).expanduser().resolve(),
            shared_root=Path(shared).expanduser().resolve() if shared else None,
        )

    def ensure_runtime_dirs(self) -> None:
        for path in (self.runtime_root, self.cache_root, self.data_root):
            path.mkdir(parents=True, exist_ok=True)
