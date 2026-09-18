"""Cross-process, per-device active-session locks."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

if os.name == "nt":
    import msvcrt
else:
    import fcntl


_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class DeviceLockError(RuntimeError):
    pass


def _lock_nonblocking(stream: IO[str]) -> None:
    """Acquire the lock without blocking on Windows or POSIX."""
    if os.name == "nt":
        # msvcrt.locking locks bytes from the current file position and needs
        # the requested byte to exist.
        stream.seek(0)
        if not stream.read(1):
            stream.seek(0)
            stream.write("\0")
            stream.flush()
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return
    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(stream: IO[str]) -> None:
    if os.name == "nt":
        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class DeviceSessionLock:
    def __init__(self, lock_root: Path, device_alias: str, owner: dict[str, Any]) -> None:
        if not _ALIAS.fullmatch(device_alias):
            raise ValueError("device_alias is not safe for a lock filename")
        self.lock_root = Path(lock_root)
        self.device_alias = device_alias
        self.owner = dict(owner)
        self.path = self.lock_root / f"{device_alias}.lock"
        self._stream: IO[str] | None = None

    def acquire(self) -> "DeviceSessionLock":
        if self._stream is not None:
            raise RuntimeError("device lock is already held")
        self.lock_root.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+", encoding="utf-8")
        try:
            _lock_nonblocking(stream)
        except OSError as exc:
            try:
                stream.seek(0)
                current_owner = stream.read().lstrip("\0").strip() or "unknown owner"
            except OSError:
                current_owner = "unknown owner"
            stream.close()
            raise DeviceLockError(
                f"device {self.device_alias!r} already has an active session: "
                f"{current_owner}"
            ) from exc
        payload = {
            "schema_version": "mobile-gui-vla.device-lock.v0.1",
            "device_alias": self.device_alias,
            "acquired_at": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "owner": self.owner,
        }
        stream.seek(0)
        stream.truncate()
        json.dump(payload, stream, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
        self._stream = stream
        return self

    def release(self) -> None:
        if self._stream is None:
            return
        _unlock(self._stream)
        self._stream.close()
        self._stream = None

    def __enter__(self) -> "DeviceSessionLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.release()
