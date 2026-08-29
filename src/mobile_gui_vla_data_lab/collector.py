"""Human collector service above the pinned Platform recorder and adapter."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from mobile_gui_vla_platform import (
    BackAction,
    HomeAction,
    SwipeAction,
    TapAction,
    TrajectoryRecorder,
    TypeAction,
    WaitAction,
)
from mobile_gui_vla_platform.contracts import CanonicalAction, Frame

from .coordinates import ImageViewport, map_drag, map_pointer
from .images import mean_absolute_delta, visual_signature
from .locking import DeviceSessionLock


OUTCOMES = {"success", "partial", "failure", "aborted", "env_error"}
COLLECTION_MODES = {
    "human_demo",
    "model_with_human_intervention",
    "scripted_schema_test",
}
ACTOR_SOURCES = {"human", "model", "scripted_fixture"}
INTERVENTION_KINDS = {"preventive_override", "post_error_takeover"}
INTERVENTION_REASONS = {
    "grounding",
    "wrong_action",
    "history_state",
    "loop",
    "ambiguity",
    "risk",
    "other",
}


class DeviceAdapter(Protocol):
    def get_device_info(self): ...

    def get_display_info(self): ...

    def screenshot(self) -> Frame: ...

    def tap(self, x_px: int, y_px: int) -> float: ...

    def swipe(
        self,
        x0_px: int,
        y0_px: int,
        x1_px: int,
        y1_px: int,
        duration_ms: int,
    ) -> float: ...

    def type_text(self, text: str) -> float: ...

    def back(self) -> float: ...

    def home(self) -> float: ...


class CollectionError(RuntimeError):
    pass


@dataclass
class PreviewFrame:
    frame: Frame
    metadata: dict[str, Any]
    saved_metadata: dict[str, Any] | None = None
    cached_monotonic_time: float = field(default_factory=time.monotonic)


@dataclass
class PreparationWorkspace:
    device_alias: str
    adapter: DeviceAdapter
    preview_frames: OrderedDict[str, PreviewFrame] = field(default_factory=OrderedDict)
    current_preview_id: str | None = None
    mutex: threading.Lock = field(default_factory=threading.Lock, repr=False)
    preview_mutex: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def preview_state(self, preview_id: str | None = None) -> dict[str, Any]:
        with self.preview_mutex:
            selected_id = preview_id or self.current_preview_id
            if selected_id is None or selected_id not in self.preview_frames:
                raise CollectionError("device has no available preparation preview")
            selected = self.preview_frames[selected_id]
            return {
                "preview_id": selected_id,
                "frame_id": selected.metadata["frame_id"],
                "sha256": selected.metadata["sha256"],
                "width_px": selected.frame.width_px,
                "height_px": selected.frame.height_px,
                "orientation": selected.frame.orientation,
                "capture_wall_time": selected.frame.capture_wall_time,
                "url": (
                    f"/api/devices/{self.device_alias}/previews/{selected_id}"
                ),
            }


@dataclass
class ActionOperation:
    operation_id: str
    scope: str
    state: str = "PENDING"
    stage: str = "QUEUED"
    preview: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: _utc_now())
    mutex: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def public_state(self) -> dict[str, Any]:
        with self.mutex:
            return {
                "operation_id": self.operation_id,
                "scope": self.scope,
                "state": self.state,
                "stage": self.stage,
                "preview": self.preview,
                "result": self.result,
                "error": self.error,
                "created_at": self.created_at,
            }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(dict(payload), stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _task_record(task: Mapping[str, Any]) -> dict[str, Any]:
    task_id = task.get("task_id")
    instruction = task.get("instruction")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task_id must be a non-empty string")
    if not isinstance(instruction, str) or not instruction.strip():
        raise ValueError("instruction must be a non-empty string")
    labels = task.get("capability_labels", [])
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise ValueError("capability_labels must be a list of strings")
    data_class = task.get("data_class", "normal")
    if data_class not in {"normal", "recovery", "ambiguous", "risk_ood"}:
        raise ValueError("unsupported data_class")
    return {
        "task_id": task_id.strip(),
        "template_id": task.get("template_id"),
        "instruction": instruction.strip(),
        "task_family": task.get("task_family", "unspecified"),
        "capability_labels": labels,
        "expected_decision": task.get("expected_decision"),
        "data_class": data_class,
    }


@dataclass
class CollectionSession:
    session_id: str
    trajectory_id: str
    device_alias: str
    task: dict[str, Any]
    collection_mode: str
    collector_id: str
    model_id: str | None
    adapter: DeviceAdapter
    recorder: TrajectoryRecorder
    device_lock: DeviceSessionLock
    current_frame: Frame
    current_metadata: dict[str, Any]
    raw_dir: Path
    annotation_path: Path
    platform_dependency: dict[str, str]
    app: dict[str, Any]
    provenance: dict[str, Any]
    training_eligible_requested: bool
    transitions: list[dict[str, Any]] = field(default_factory=list)
    preview_frames: OrderedDict[str, PreviewFrame] = field(default_factory=OrderedDict)
    current_preview_id: str | None = None
    pending_operation_id: str | None = None
    state: str = "ACTIVE"
    mutex: threading.Lock = field(default_factory=threading.Lock, repr=False)
    preview_mutex: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def public_state(self) -> dict[str, Any]:
        preview = self.preview_state()
        return {
            "session_id": self.session_id,
            "trajectory_id": self.trajectory_id,
            "device_alias": self.device_alias,
            "task": self.task,
            "collection_mode": self.collection_mode,
            "state": self.state,
            "step_count": len(self.transitions),
            "pending_operation_id": self.pending_operation_id,
            "frame": {
                **preview,
                "raw_url": f"/api/sessions/{self.session_id}/frame",
            },
        }

    def preview_state(self, preview_id: str | None = None) -> dict[str, Any]:
        with self.preview_mutex:
            selected_id = preview_id or self.current_preview_id
            if selected_id is None or selected_id not in self.preview_frames:
                raise CollectionError("session has no available preview frame")
            selected = self.preview_frames[selected_id]
            return {
                "preview_id": selected_id,
                "frame_id": selected.metadata["frame_id"],
                "sha256": selected.metadata["sha256"],
                "width_px": selected.frame.width_px,
                "height_px": selected.frame.height_px,
                "orientation": selected.frame.orientation,
                "capture_wall_time": selected.frame.capture_wall_time,
                "url": f"/api/sessions/{self.session_id}/previews/{selected_id}",
            }


class CollectionService:
    def __init__(
        self,
        *,
        artifact_root: Path,
        device_factories: Mapping[str, Callable[[], DeviceAdapter]],
        platform_dependency: Mapping[str, str],
        allow_natural_model: bool = False,
        post_action_settle_seconds: float = 0.5,
        stable_capture_max_samples: int = 5,
        stable_capture_interval_seconds: float = 0.12,
        stable_visual_delta: float = 1.5,
        preview_cache_size: int = 4,
        preview_min_interval_seconds: float = 0.08,
    ) -> None:
        if post_action_settle_seconds < 0:
            raise ValueError("post_action_settle_seconds must be non-negative")
        if type(stable_capture_max_samples) is not int or stable_capture_max_samples < 1:
            raise ValueError("stable_capture_max_samples must be a positive integer")
        if stable_capture_interval_seconds < 0:
            raise ValueError("stable_capture_interval_seconds must be non-negative")
        if stable_visual_delta < 0:
            raise ValueError("stable_visual_delta must be non-negative")
        if type(preview_cache_size) is not int or preview_cache_size < 2:
            raise ValueError("preview_cache_size must be at least 2")
        if preview_min_interval_seconds < 0:
            raise ValueError("preview_min_interval_seconds must be non-negative")
        self.artifact_root = Path(artifact_root)
        self.device_factories = dict(device_factories)
        self.platform_dependency = dict(platform_dependency)
        self.allow_natural_model = allow_natural_model
        self.post_action_settle_seconds = post_action_settle_seconds
        self.stable_capture_max_samples = stable_capture_max_samples
        self.stable_capture_interval_seconds = stable_capture_interval_seconds
        self.stable_visual_delta = stable_visual_delta
        self.preview_cache_size = preview_cache_size
        self.preview_min_interval_seconds = preview_min_interval_seconds
        self._sessions: dict[str, CollectionSession] = {}
        self._preparations: dict[str, PreparationWorkspace] = {}
        self._operations: dict[str, ActionOperation] = {}
        self._device_pending_operations: dict[str, str] = {}
        self._guard = threading.RLock()

    def list_devices(self) -> list[dict[str, str]]:
        return [
            {"device_alias": alias, "status": "configured"}
            for alias in sorted(self.device_factories)
        ]

    def capture_preparation_preview(self, device_alias: str) -> dict[str, Any]:
        workspace = self._preparation_workspace(device_alias)
        requested_at = time.monotonic()
        with workspace.mutex, DeviceSessionLock(
            self.artifact_root / "locks",
            device_alias,
            {"mode": "unrecorded_preparation", "pid": os.getpid()},
        ):
            cached_id = self._fresh_preparation_preview_id(workspace, requested_at)
            if cached_id is not None:
                return {"preview": workspace.preview_state(cached_id)}
            frame = workspace.adapter.screenshot()
            preview_id = self._cache_preparation_preview(workspace, frame)
            return {"preview": workspace.preview_state(preview_id)}

    def _fresh_preparation_preview_id(
        self, workspace: PreparationWorkspace, requested_at: float
    ) -> str | None:
        with workspace.preview_mutex:
            preview_id = workspace.current_preview_id
            if preview_id is None:
                return None
            preview = workspace.preview_frames.get(preview_id)
            if preview is None:
                return None
            if preview.cached_monotonic_time >= requested_at:
                return preview_id
            if (
                time.monotonic() - preview.cached_monotonic_time
                > self.preview_min_interval_seconds
            ):
                return None
            return preview_id

    def preparation_preview_png(self, device_alias: str, preview_id: str) -> bytes:
        workspace = self._preparation_workspace(device_alias)
        with workspace.preview_mutex:
            selected = workspace.preview_frames.get(preview_id)
            if selected is None:
                raise CollectionError("unknown or expired preparation preview_id")
            return bytes(selected.frame.png_bytes)

    def execute_preparation(
        self, device_alias: str, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        workspace = self._preparation_workspace(device_alias)
        with workspace.mutex, DeviceSessionLock(
            self.artifact_root / "locks",
            device_alias,
            {"mode": "unrecorded_preparation", "pid": os.getpid()},
        ):
            preview_id = request.get("preview_id")
            if not isinstance(preview_id, str):
                raise CollectionError("preparation preview_id must be a string")
            selected = workspace.preview_frames.get(preview_id)
            if selected is None:
                raise CollectionError("unknown or expired preparation preview_id")
            if request.get("frame_id") != selected.metadata["frame_id"]:
                raise CollectionError("stale or missing preparation frame_id")
            if request.get("frame_sha256") != selected.metadata["sha256"]:
                raise CollectionError("stale or missing preparation frame_sha256")
            action, coordinate_provenance = _action_from_request(
                request, selected.frame
            )
            self._dispatch_unrecorded(workspace.adapter, action)
            frame = workspace.adapter.screenshot()
            next_preview_id = self._cache_preparation_preview(workspace, frame)
            return {
                "recorded": False,
                "canonical_action": action.to_dict(),
                "coordinate_provenance": coordinate_provenance,
                "capture_policy": "unrecorded_immediate_feedback",
                "preview": workspace.preview_state(next_preview_id),
            }

    def start_preparation_execute(
        self, device_alias: str, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        self._preparation_workspace(device_alias)
        operation = ActionOperation(str(uuid.uuid4()), "preparation")
        with self._guard:
            pending_id = self._device_pending_operations.get(device_alias)
            if pending_id is not None:
                pending = self._operations.get(pending_id)
                if pending is not None and pending.state not in {"COMPLETE", "ERROR"}:
                    raise CollectionError("device already has an action in progress")
            self._operations[operation.operation_id] = operation
            self._device_pending_operations[device_alias] = operation.operation_id
        thread = threading.Thread(
            target=self._run_preparation_operation,
            args=(operation, device_alias, dict(request)),
            daemon=True,
            name=f"collector-preparation-{operation.operation_id[:8]}",
        )
        thread.start()
        return operation.public_state()

    def _run_preparation_operation(
        self,
        operation: ActionOperation,
        device_alias: str,
        request: Mapping[str, Any],
    ) -> None:
        with operation.mutex:
            operation.state = "RUNNING"
            operation.stage = "DISPATCHING"
        try:
            result = self.execute_preparation(device_alias, request)
            with operation.mutex:
                operation.result = result
                operation.preview = result["preview"]
                operation.stage = "READY"
                operation.state = "COMPLETE"
        except Exception as exc:
            with operation.mutex:
                operation.error = str(exc)
                operation.stage = "FAILED"
                operation.state = "ERROR"
        finally:
            with self._guard:
                if self._device_pending_operations.get(device_alias) == operation.operation_id:
                    self._device_pending_operations.pop(device_alias, None)

    def _preparation_workspace(self, device_alias: str) -> PreparationWorkspace:
        if device_alias not in self.device_factories:
            raise CollectionError(f"unknown configured device alias: {device_alias!r}")
        with self._guard:
            workspace = self._preparations.get(device_alias)
            if workspace is None:
                workspace = PreparationWorkspace(
                    device_alias=device_alias,
                    adapter=self.device_factories[device_alias](),
                )
                self._preparations[device_alias] = workspace
            return workspace

    def _cache_preparation_preview(
        self, workspace: PreparationWorkspace, frame: Frame
    ) -> str:
        metadata = frame.metadata()
        metadata["sha256"] = _sha256_bytes(frame.png_bytes)
        preview_id = str(uuid.uuid4())
        with workspace.preview_mutex:
            workspace.preview_frames[preview_id] = PreviewFrame(
                frame=frame, metadata=metadata
            )
            workspace.current_preview_id = preview_id
            while len(workspace.preview_frames) > self.preview_cache_size:
                workspace.preview_frames.popitem(last=False)
        return preview_id

    @staticmethod
    def _dispatch_unrecorded(adapter: DeviceAdapter, action: CanonicalAction) -> None:
        if isinstance(action, TapAction):
            adapter.tap(action.x_px, action.y_px)
        elif isinstance(action, SwipeAction):
            adapter.swipe(
                action.x0_px,
                action.y0_px,
                action.x1_px,
                action.y1_px,
                action.duration_ms,
            )
        elif isinstance(action, TypeAction):
            adapter.type_text(action.text)
        elif isinstance(action, BackAction):
            adapter.back()
        elif isinstance(action, HomeAction):
            adapter.home()
        elif isinstance(action, WaitAction):
            time.sleep(action.duration_ms / 1000.0)
        else:
            raise TypeError(f"unsupported preparation action: {type(action).__name__}")

    def start_session(
        self,
        *,
        device_alias: str,
        task: Mapping[str, Any],
        collector_id: str,
        collection_mode: str = "human_demo",
        model_id: str | None = None,
        app: Mapping[str, Any] | None = None,
        provenance: Mapping[str, Any] | None = None,
        training_eligible: bool = True,
    ) -> CollectionSession:
        if device_alias not in self.device_factories:
            raise CollectionError(f"unknown configured device alias: {device_alias!r}")
        if not isinstance(collector_id, str) or not collector_id.strip():
            raise ValueError("collector_id must be a non-empty pseudonymous identifier")
        if collection_mode not in COLLECTION_MODES:
            raise ValueError("unsupported collection_mode")
        if collection_mode == "model_with_human_intervention" and not self.allow_natural_model:
            raise CollectionError("natural model intervention is not authorized")
        task_record = _task_record(task)
        session_id = str(uuid.uuid4())
        trajectory_id = str(uuid.uuid4())
        lock = DeviceSessionLock(
            self.artifact_root / "locks",
            device_alias,
            {
                "session_id": session_id,
                "trajectory_id": trajectory_id,
                "collector_id": collector_id,
            },
        ).acquire()
        recorder: TrajectoryRecorder | None = None
        try:
            adapter = self.device_factories[device_alias]()
            device_info = adapter.get_device_info().to_dict()
            display_info = adapter.get_display_info().to_dict()
            device_info["display"] = display_info
            raw_dir = self.artifact_root / "raw" / trajectory_id
            recorder = TrajectoryRecorder(
                raw_dir,
                instruction=task_record["instruction"],
                device=device_info,
                model={"model_id": model_id} if model_id is not None else None,
                trajectory_id=trajectory_id,
                notes="Platform-owned immutable raw evidence; Data Lab annotation is separate",
            )
            frame = adapter.screenshot()
            metadata = recorder.save_frame(frame)
            session = CollectionSession(
                session_id=session_id,
                trajectory_id=trajectory_id,
                device_alias=device_alias,
                task=task_record,
                collection_mode=collection_mode,
                collector_id=collector_id.strip(),
                model_id=model_id,
                adapter=adapter,
                recorder=recorder,
                device_lock=lock,
                current_frame=frame,
                current_metadata=metadata,
                raw_dir=raw_dir,
                annotation_path=self.artifact_root / "annotations" / f"{trajectory_id}.json",
                platform_dependency=self.platform_dependency,
                app=dict(app or {}),
                provenance=dict(provenance or {}),
                training_eligible_requested=bool(training_eligible),
            )
            self._cache_preview(session, frame, saved_metadata=metadata)
        except Exception:
            if recorder is not None and recorder.summary["status"] == "OPEN":
                recorder.finish(
                    status="ERROR",
                    termination_reason="DEVICE_ERROR",
                    error="session initialization failed",
                )
            lock.release()
            raise
        with self._guard:
            self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> CollectionSession:
        with self._guard:
            session = self._sessions.get(session_id)
        if session is None:
            raise CollectionError("unknown session_id")
        return session

    def get_operation(self, operation_id: str) -> dict[str, Any]:
        with self._guard:
            operation = self._operations.get(operation_id)
        if operation is None:
            raise CollectionError("unknown operation_id")
        return operation.public_state()

    def start_execute(
        self, session_id: str, request: Mapping[str, Any]
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        operation = ActionOperation(str(uuid.uuid4()), "trajectory")
        with self._guard:
            if session.pending_operation_id is not None:
                pending = self._operations.get(session.pending_operation_id)
                if pending is not None and pending.state not in {"COMPLETE", "ERROR"}:
                    raise CollectionError("session already has an action in progress")
            session.pending_operation_id = operation.operation_id
            self._operations[operation.operation_id] = operation
        thread = threading.Thread(
            target=self._run_session_operation,
            args=(operation, session, dict(request)),
            daemon=True,
            name=f"collector-action-{operation.operation_id[:8]}",
        )
        thread.start()
        return operation.public_state()

    def _run_session_operation(
        self,
        operation: ActionOperation,
        session: CollectionSession,
        request: Mapping[str, Any],
    ) -> None:
        with operation.mutex:
            operation.state = "RUNNING"
            operation.stage = "DISPATCHING"

        def feedback(frame: Frame) -> None:
            preview_id = self._cache_preview(session, frame)
            preview = session.preview_state(preview_id)
            with operation.mutex:
                operation.preview = preview
                operation.stage = "CAPTURING_STABLE_FRAME"

        try:
            result = self.execute(
                session.session_id,
                request,
                feedback_callback=feedback,
            )
            with self._guard:
                session.pending_operation_id = None
            result["session"]["pending_operation_id"] = None
            with operation.mutex:
                operation.result = result
                operation.preview = result["session"]["frame"]
                operation.stage = "READY"
                operation.state = "COMPLETE"
        except Exception as exc:
            with self._guard:
                session.pending_operation_id = None
            with operation.mutex:
                operation.error = str(exc)
                operation.stage = "FAILED"
                operation.state = "ERROR"

    def current_png(self, session_id: str) -> bytes:
        session = self.get_session(session_id)
        with session.mutex:
            return bytes(session.current_frame.png_bytes)

    def capture_preview(self, session_id: str) -> dict[str, Any]:
        session = self.get_session(session_id)
        requested_at = time.monotonic()
        with session.mutex:
            if session.state != "ACTIVE":
                raise CollectionError("session is not active")
            cached_id = self._fresh_preview_id(session, requested_at)
            if cached_id is not None:
                return {"preview": session.preview_state(cached_id)}
            frame = session.adapter.screenshot()
            preview_id = self._cache_preview(session, frame)
            return {"preview": session.preview_state(preview_id)}

    def _fresh_preview_id(
        self, session: CollectionSession, requested_at: float
    ) -> str | None:
        with session.preview_mutex:
            preview_id = session.current_preview_id
            if preview_id is None:
                return None
            preview = session.preview_frames.get(preview_id)
            if preview is None:
                return None
            if preview.cached_monotonic_time >= requested_at:
                return preview_id
            if (
                time.monotonic() - preview.cached_monotonic_time
                > self.preview_min_interval_seconds
            ):
                return None
            return preview_id

    def preview_png(self, session_id: str, preview_id: str) -> bytes:
        session = self.get_session(session_id)
        with session.preview_mutex:
            selected = session.preview_frames.get(preview_id)
            if selected is None:
                raise CollectionError("unknown or expired preview_id")
            return bytes(selected.frame.png_bytes)

    def execute(
        self,
        session_id: str,
        request: Mapping[str, Any],
        *,
        feedback_callback: Callable[[Frame], None] | None = None,
    ) -> dict[str, Any]:
        session = self.get_session(session_id)
        with session.mutex:
            if session.state != "ACTIVE":
                raise CollectionError("session is not active")
            observation_refresh = self._bind_action_frame(session, request)
            action, coordinate_provenance = _action_from_request(
                request,
                session.current_frame,
            )
            actor_source = request.get("actor_source", "human")
            if actor_source not in ACTOR_SOURCES:
                raise ValueError("unsupported actor_source")
            if actor_source == "model" and not self.allow_natural_model:
                raise CollectionError("natural model action is not authorized")
            if (
                actor_source == "scripted_fixture"
                and session.collection_mode != "scripted_schema_test"
            ):
                raise CollectionError("scripted_fixture actor requires scripted_schema_test")
            actor_role = request.get("actor_role")
            if actor_source == "scripted_fixture":
                if actor_role not in {"human", "model", "reference"}:
                    raise ValueError(
                        "scripted_fixture requires actor_role human, model, or reference"
                    )
            elif actor_role is not None:
                raise ValueError("actor_role is only valid for scripted_fixture")
            intervention = _normalize_intervention(request.get("intervention"))
            proposal = _normalize_proposal(
                request.get("model_proposal"),
                source_frame=session.current_metadata,
                allow_natural_model=self.allow_natural_model,
            )
            self._validate_intervention_context(
                session,
                actor_source,
                actor_role,
                intervention,
                proposal,
            )
            transition = self._execute_transition(
                session,
                action,
                actor_source=actor_source,
                actor_role=actor_role,
                intervention=intervention,
                model_proposal=proposal,
                coordinate_provenance=coordinate_provenance,
                note=request.get("note"),
                post_action_settle_seconds=self.post_action_settle_seconds,
                observation_refresh=observation_refresh,
                feedback_callback=feedback_callback,
            )
            return {
                "transition": transition,
                "session": session.public_state(),
            }

    def finalize(
        self,
        session_id: str,
        *,
        outcome: str,
        failure_family: str | None = None,
        contains_sensitive_data: bool = False,
        redaction_status: str = "clean",
        note: str | None = None,
    ) -> dict[str, Any]:
        if outcome not in OUTCOMES:
            raise ValueError("unsupported outcome")
        if redaction_status not in {"clean", "redacted", "quarantine"}:
            raise ValueError("unsupported redaction_status")
        if contains_sensitive_data and redaction_status == "clean":
            raise ValueError("sensitive data cannot be marked clean")
        session = self.get_session(session_id)
        with session.mutex:
            if session.state != "ACTIVE":
                raise CollectionError("session is not active")
            if outcome == "env_error":
                session.recorder.finish(
                    status="ERROR",
                    termination_reason="DEVICE_ERROR",
                    error=note,
                )
            elif outcome == "aborted":
                session.recorder.finish(
                    status="ABORTED",
                    termination_reason="HUMAN_STOP",
                    error=note,
                )
            else:
                session.recorder.finish(status="COMPLETE", termination_reason="DONE")
            training_eligible = (
                session.training_eligible_requested
                and session.collection_mode != "scripted_schema_test"
                and bool(session.transitions)
                and outcome in {"success", "partial"}
                and not contains_sensitive_data
                and redaction_status != "quarantine"
                and not bool(session.provenance.get("official_eval_case", False))
            )
            record = {
                "schema_version": "mobile-gui-vla.data-record.v0.1",
                "trajectory_id": session.trajectory_id,
                "raw_trajectory_uri": str(session.raw_dir),
                "task": {
                    key: value
                    for key, value in session.task.items()
                    if key != "data_class"
                },
                "data_class": session.task["data_class"],
                "app": {
                    "package_name": session.app.get("package_name"),
                    "version_name": session.app.get("version_name"),
                    "version_code": session.app.get("version_code"),
                    "locale": session.app.get("locale"),
                },
                "collection": {
                    "collector_id": session.collector_id,
                    "session_id": session.session_id,
                    "collection_mode": session.collection_mode,
                    "model_id": session.model_id,
                    "device_alias": session.device_alias,
                },
                "provenance": {
                    "origin": session.provenance.get("origin", "local_pilot"),
                    "official_eval_case": bool(
                        session.provenance.get("official_eval_case", False)
                    ),
                    "split_group_id": session.provenance.get(
                        "split_group_id", session.task.get("template_id")
                    ),
                    "task_setup": session.provenance.get("task_setup"),
                    "platform_dependency": session.platform_dependency,
                },
                "outcome": {
                    "status": outcome,
                    "failure_family": failure_family,
                    "note": note,
                },
                "privacy": {
                    "contains_sensitive_data": bool(contains_sensitive_data),
                    "redaction_status": redaction_status,
                },
                "interventions": [
                    transition["human_intervention"]
                    for transition in session.transitions
                    if transition["human_intervention"] is not None
                ],
                "training_eligible": training_eligible,
                "created_at": session.recorder.summary["created_at"],
                "finalized_at": _utc_now(),
            }
            _atomic_json(session.annotation_path, record)
            session.state = "FINALIZED"
            with session.preview_mutex:
                session.preview_frames.clear()
                session.current_preview_id = None
            session.device_lock.release()
            return record

    def _cache_preview(
        self,
        session: CollectionSession,
        frame: Frame,
        *,
        saved_metadata: dict[str, Any] | None = None,
    ) -> str:
        metadata = frame.metadata()
        metadata["sha256"] = _sha256_bytes(frame.png_bytes)
        preview_id = str(uuid.uuid4())
        with session.preview_mutex:
            session.preview_frames[preview_id] = PreviewFrame(
                frame=frame,
                metadata=metadata,
                saved_metadata=saved_metadata,
            )
            session.current_preview_id = preview_id
            while len(session.preview_frames) > self.preview_cache_size:
                session.preview_frames.popitem(last=False)
        return preview_id

    def _bind_action_frame(
        self, session: CollectionSession, request: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        preview_id = request.get("preview_id")
        if preview_id is None:
            self._require_current_frame(session, request)
            return None
        if not isinstance(preview_id, str):
            raise CollectionError("preview_id must be a string")
        selected = session.preview_frames.get(preview_id)
        if selected is None:
            raise CollectionError("unknown or expired preview_id")
        if request.get("frame_id") != selected.metadata["frame_id"]:
            raise CollectionError("stale or missing preview frame_id")
        if request.get("frame_sha256") != selected.metadata["sha256"]:
            raise CollectionError("stale or missing preview frame_sha256")
        previous = session.current_metadata
        if selected.saved_metadata is None:
            selected.saved_metadata = session.recorder.save_frame(selected.frame)
        session.current_frame = selected.frame
        session.current_metadata = selected.saved_metadata
        session.current_preview_id = preview_id
        if (
            previous.get("frame_id") == selected.saved_metadata.get("frame_id")
            and previous.get("sha256") == selected.saved_metadata.get("sha256")
        ):
            return None
        return {
            "kind": "live_preview_selected_as_action_source",
            "previous_recorded_frame": {
                "frame_id": previous.get("frame_id"),
                "sha256": previous.get("sha256"),
            },
            "selected_preview": {
                "preview_id": preview_id,
                "frame_id": selected.saved_metadata["frame_id"],
                "sha256": selected.saved_metadata["sha256"],
                "capture_wall_time": selected.frame.capture_wall_time,
                "capture_monotonic_time": selected.frame.capture_monotonic_time,
            },
        }

    @staticmethod
    def _require_current_frame(
        session: CollectionSession, request: Mapping[str, Any]
    ) -> None:
        if request.get("frame_id") != session.current_metadata["frame_id"]:
            raise CollectionError("stale or missing frame_id")
        if request.get("frame_sha256") != session.current_metadata["sha256"]:
            raise CollectionError("stale or missing frame_sha256")

    @staticmethod
    def _validate_intervention_context(
        session: CollectionSession,
        actor_source: str,
        actor_role: str | None,
        intervention: dict[str, Any] | None,
        proposal: dict[str, Any] | None,
    ) -> None:
        if intervention is None:
            if proposal is not None:
                raise ValueError("model_proposal requires intervention metadata")
            return
        human_side = actor_source == "human" or (
            actor_source == "scripted_fixture" and actor_role == "human"
        )
        if not human_side:
            raise ValueError(
                "intervention transition must be human-authored or a human-role fixture"
            )
        if intervention["kind"] == "preventive_override":
            if proposal is None or proposal["executed"]:
                raise ValueError(
                    "preventive_override requires a retained unexecuted model proposal"
                )
        if intervention["kind"] == "post_error_takeover":
            trigger = intervention.get("trigger_step_index")
            if type(trigger) is not int or not 0 <= trigger < len(session.transitions):
                raise ValueError("post_error_takeover requires a prior trigger step")
            trigger_actor = session.transitions[trigger]["actor"]
            if trigger_actor["source"] == "model":
                return
            if not (
                trigger_actor["source"] == "scripted_fixture"
                and trigger_actor.get("simulated_role") == "model"
            ):
                raise ValueError("post_error trigger step must be model or fixture actor")

    def _execute_transition(
        self,
        session: CollectionSession,
        action: CanonicalAction,
        *,
        actor_source: str,
        actor_role: str | None,
        intervention: dict[str, Any] | None,
        model_proposal: dict[str, Any] | None,
        coordinate_provenance: dict[str, Any] | None,
        note: Any,
        post_action_settle_seconds: float,
        observation_refresh: dict[str, Any] | None,
        feedback_callback: Callable[[Frame], None] | None,
    ) -> dict[str, Any]:
        step_index = len(session.transitions)
        source_frame = session.current_frame
        source_metadata = session.current_metadata
        start = time.monotonic()
        dispatch_start = time.monotonic()
        control_wait_ms = 0.0
        if isinstance(action, TapAction):
            dispatch_ms = session.adapter.tap(action.x_px, action.y_px)
        elif isinstance(action, SwipeAction):
            dispatch_ms = session.adapter.swipe(
                action.x0_px,
                action.y0_px,
                action.x1_px,
                action.y1_px,
                action.duration_ms,
            )
        elif isinstance(action, TypeAction):
            dispatch_ms = session.adapter.type_text(action.text)
        elif isinstance(action, BackAction):
            dispatch_ms = session.adapter.back()
        elif isinstance(action, HomeAction):
            dispatch_ms = session.adapter.home()
        elif isinstance(action, WaitAction):
            wait_start = time.monotonic()
            time.sleep(action.duration_ms / 1000.0)
            control_wait_ms = (time.monotonic() - wait_start) * 1000.0
            dispatch_ms = 0.0
        else:
            raise TypeError(f"unsupported collector action: {type(action).__name__}")
        dispatch_envelope_ms = (time.monotonic() - dispatch_start) * 1000.0
        settle_start = time.monotonic()
        feedback_frame: Frame | None = None
        if not isinstance(action, WaitAction):
            feedback_frame = session.adapter.screenshot()
            if feedback_callback is not None:
                feedback_callback(feedback_frame)
            remaining_settle = post_action_settle_seconds - (
                time.monotonic() - settle_start
            )
            if remaining_settle > 0:
                time.sleep(remaining_settle)
        next_frame, stability = self._capture_stable_frame(
            session.adapter,
            initial_frame=feedback_frame,
            on_sample=feedback_callback,
        )
        settle_ms = (time.monotonic() - settle_start) * 1000.0
        next_metadata = session.recorder.save_frame(next_frame)
        transition = {
            "step_index": step_index,
            "frame_t": source_metadata,
            "actor": {
                "source": actor_source,
                "collector_id": session.collector_id if actor_source == "human" else None,
                "simulated_role": actor_role,
            },
            "model_proposal": model_proposal,
            "canonical_action": action.to_dict(),
            "coordinate_provenance": coordinate_provenance,
            "observation_refresh": observation_refresh,
            "execution": {"status": "SUCCESS", "error": None},
            "timing_ms": {
                "capture_frame_t_ms": source_frame.capture_ms,
                "action_dispatch_ms": dispatch_ms,
                "dispatch_envelope_ms": dispatch_envelope_ms,
                "control_wait_ms": control_wait_ms,
                "post_action_settle_ms": settle_ms,
                "capture_frame_t_plus_1_ms": next_frame.capture_ms,
                "total_transition_ms": (time.monotonic() - start) * 1000.0,
                "stable_capture_samples": stability["sample_count"],
                "stable_capture_final_delta": stability["final_delta"],
            },
            "stable_capture": stability,
            "frame_t_plus_1": next_metadata,
            "human_intervention": intervention,
            "progress_label": None,
            "failure_label": None,
            "notes": note if isinstance(note, str) else None,
        }
        session.recorder.append_transition(transition)
        session.transitions.append(transition)
        session.current_frame = next_frame
        session.current_metadata = next_metadata
        self._cache_preview(session, next_frame, saved_metadata=next_metadata)
        return transition

    def _capture_stable_frame(
        self,
        adapter: DeviceAdapter,
        *,
        initial_frame: Frame | None = None,
        on_sample: Callable[[Frame], None] | None = None,
    ) -> tuple[Frame, dict[str, Any]]:
        previous_signature = (
            visual_signature(initial_frame.png_bytes)
            if initial_frame is not None and self.stable_capture_max_samples > 1
            else None
        )
        final_delta: float | None = None
        frame = initial_frame
        stable = self.stable_capture_max_samples == 1
        sample_count = 1 if initial_frame is not None else 0
        for sample_index in range(sample_count, self.stable_capture_max_samples):
            if sample_count > 0 or sample_index > 0:
                time.sleep(self.stable_capture_interval_seconds)
            frame = adapter.screenshot()
            sample_count += 1
            if on_sample is not None:
                on_sample(frame)
            if self.stable_capture_max_samples == 1:
                break
            signature = visual_signature(frame.png_bytes)
            if previous_signature is not None:
                final_delta = mean_absolute_delta(previous_signature, signature)
                if final_delta <= self.stable_visual_delta:
                    stable = True
                    break
            previous_signature = signature
        if frame is None:
            raise AssertionError("stable capture produced no frame")
        return frame, {
            "policy": "bounded_consecutive_visual_stability",
            "sample_count": sample_count,
            "max_samples": self.stable_capture_max_samples,
            "interval_ms": self.stable_capture_interval_seconds * 1000.0,
            "visual_delta_threshold": self.stable_visual_delta,
            "final_delta": final_delta,
            "stable": stable,
        }


def _viewport(value: Any) -> ImageViewport:
    if not isinstance(value, Mapping):
        raise ValueError("viewport must be an object")
    try:
        return ImageViewport(
            x=value["x"],
            y=value["y"],
            width=value["width"],
            height=value["height"],
        )
    except KeyError as exc:
        raise ValueError(f"viewport is missing {exc.args[0]}") from exc


def _action_from_request(
    request: Mapping[str, Any], frame: Frame
) -> tuple[CanonicalAction, dict[str, Any] | None]:
    action_type = request.get("type")
    if action_type == "tap":
        mapped = map_pointer(
            pointer_x=request.get("x"),
            pointer_y=request.get("y"),
            viewport=_viewport(request.get("viewport")),
            frame_width_px=frame.width_px,
            frame_height_px=frame.height_px,
            orientation=frame.orientation,
        )
        action = TapAction(mapped.x_px, mapped.y_px)
        action.validate(_display_for_frame(frame))
        return action, mapped.provenance()
    if action_type == "swipe":
        start, end = map_drag(
            start_x=request.get("x0"),
            start_y=request.get("y0"),
            end_x=request.get("x1"),
            end_y=request.get("y1"),
            viewport=_viewport(request.get("viewport")),
            frame_width_px=frame.width_px,
            frame_height_px=frame.height_px,
            orientation=frame.orientation,
        )
        duration_ms = request.get("duration_ms", 300)
        action = SwipeAction(
            start.x_px,
            start.y_px,
            end.x_px,
            end.y_px,
            duration_ms,
        )
        action.validate(_display_for_frame(frame))
        return action, {
            "kind": "css_contain_drag_to_original_frame_pixels",
            "start": start.provenance(),
            "end": end.provenance(),
        }
    if action_type == "type":
        action = TypeAction(request.get("text"))
        action.validate()
        return action, None
    if action_type == "back":
        return BackAction(), None
    if action_type == "home":
        return HomeAction(), None
    if action_type == "wait":
        action = WaitAction(request.get("duration_ms", 500))
        action.validate()
        return action, None
    raise ValueError(f"unsupported action type: {action_type!r}")


def _display_for_frame(frame: Frame):
    from mobile_gui_vla_platform import DisplayInfo

    return DisplayInfo(
        width_px=frame.width_px,
        height_px=frame.height_px,
        density_dpi=frame.density_dpi,
        orientation=frame.orientation,
    )


def _normalize_intervention(value: Any) -> dict[str, Any] | None:
    if value in (None, False):
        return None
    if not isinstance(value, Mapping):
        raise ValueError("intervention must be an object")
    kind = value.get("kind")
    reason = value.get("reason")
    if kind not in INTERVENTION_KINDS:
        raise ValueError("unsupported intervention kind")
    if reason not in INTERVENTION_REASONS:
        raise ValueError("unsupported intervention reason")
    trigger = value.get("trigger_step_index")
    if trigger is not None and type(trigger) is not int:
        raise ValueError("trigger_step_index must be an integer or null")
    return {
        "active": True,
        "kind": kind,
        "trigger_step_index": trigger,
        "trigger_failure_family": value.get("trigger_failure_family"),
        "reason": reason,
    }


def _normalize_proposal(
    value: Any,
    *,
    source_frame: Mapping[str, Any],
    allow_natural_model: bool,
) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("model_proposal must be an object")
    source = value.get("source", "scripted_fixture")
    if source == "model" and not allow_natural_model:
        raise CollectionError("natural model proposal is not authorized")
    if source not in {"model", "scripted_fixture"}:
        raise ValueError("proposal source must be model or scripted_fixture")
    action = value.get("structured_action")
    if not isinstance(action, Mapping):
        raise ValueError("model_proposal.structured_action must be an object")
    return {
        "source": source,
        "model_id": value.get("model_id", "scripted-fixture-not-a-model"),
        "request_id": value.get("request_id"),
        "raw_output": value.get("raw_output"),
        "structured_action": dict(action),
        "executed": bool(value.get("executed", False)),
        "source_frame_id": source_frame["frame_id"],
        "source_frame_sha256": source_frame["sha256"],
    }
