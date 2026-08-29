"""Append-oriented trajectory persistence and a deliberately bounded runner."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import (
    BackAction,
    CanonicalAction,
    DoneAction,
    Frame,
    SwipeAction,
    TapAction,
    WaitAction,
)
from .model_boundary import (
    DeviceAdapter,
    ModelBoundaryError,
    ModelClient,
    ModelPrediction,
    parse_action_prediction,
)

SCHEMA_VERSION = "mobile-gui-vla.trajectory.v0.2"
TERMINATION_REASONS = {
    "DONE",
    "MAX_STEPS",
    "MODEL_ERROR",
    "ACTION_ERROR",
    "DEVICE_ERROR",
    "HUMAN_STOP",
}
_FRAME_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class TrajectoryRecorder:
    """Persist frames and completed transition lines as soon as they exist."""

    def __init__(
        self,
        output_dir: Path,
        *,
        instruction: str,
        device: Mapping[str, Any],
        model: Mapping[str, Any] | None = None,
        trajectory_id: str | None = None,
        notes: str | None = None,
    ) -> None:
        if not instruction.strip():
            raise ValueError("instruction must not be empty")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self.frames_dir = self.output_dir / "frames"
        self.visual_dir = self.output_dir / "visual"
        self.frames_dir.mkdir()
        self.visual_dir.mkdir()
        self.steps_path = self.output_dir / "steps.jsonl"
        self.steps_path.touch(exist_ok=False)
        now = _utc_now()
        self.summary: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "trajectory_id": trajectory_id or str(uuid.uuid4()),
            "instruction": instruction,
            "device": dict(device),
            "model": dict(model) if model is not None else None,
            "created_at": now,
            "updated_at": now,
            "status": "OPEN",
            "step_count": 0,
            "termination_reason": None,
            "termination_error": None,
            "final_label": None,
            "notes": notes,
            "visual_artifacts": [],
            "latency_claim": "diagnostic_only_if_visual_recording_enabled",
        }
        self._write_summary()

    @property
    def summary_path(self) -> Path:
        return self.output_dir / "trajectory.json"

    def _write_summary(self) -> None:
        temporary = self.summary_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(self.summary, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.summary_path)

    def save_frame(self, frame: Frame) -> dict[str, Any]:
        if not _FRAME_ID.fullmatch(frame.frame_id):
            raise ValueError(f"unsafe frame_id: {frame.frame_id!r}")
        relative_path = Path("frames") / f"{frame.frame_id}.png"
        destination = self.output_dir / relative_path
        digest = _sha256(frame.png_bytes)
        if destination.exists():
            if _sha256(destination.read_bytes()) != digest:
                raise ValueError(
                    f"frame_id {frame.frame_id!r} already exists with different bytes"
                )
        else:
            with destination.open("xb") as stream:
                stream.write(frame.png_bytes)
                stream.flush()
                os.fsync(stream.fileno())
        metadata = frame.metadata()
        metadata.update({"path": relative_path.as_posix(), "sha256": digest})
        return metadata

    def append_transition(self, transition: Mapping[str, Any]) -> None:
        if self.summary["status"] != "OPEN":
            raise RuntimeError("cannot append to a closed trajectory")
        expected_index = self.summary["step_count"]
        if transition.get("step_index") != expected_index:
            raise ValueError(
                f"step_index must be {expected_index}, got {transition.get('step_index')!r}"
            )
        rendered = json.dumps(dict(transition), sort_keys=True, separators=(",", ":"))
        with self.steps_path.open("a", encoding="utf-8") as stream:
            stream.write(rendered + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.summary["step_count"] = expected_index + 1
        self.summary["updated_at"] = _utc_now()
        self._write_summary()

    def add_visual_artifact(self, artifact: Mapping[str, Any]) -> None:
        self.summary["visual_artifacts"].append(dict(artifact))
        self.summary["updated_at"] = _utc_now()
        self.summary["latency_claim"] = "diagnostic_demo_not_clean_latency"
        self._write_summary()

    def finish(
        self,
        *,
        status: str,
        termination_reason: str,
        error: str | None = None,
    ) -> None:
        if status not in {"COMPLETE", "ABORTED", "ERROR"}:
            raise ValueError(f"unsupported terminal status: {status!r}")
        if termination_reason not in TERMINATION_REASONS:
            raise ValueError(f"unsupported termination reason: {termination_reason!r}")
        if self.summary["status"] != "OPEN":
            raise RuntimeError("trajectory is already closed")
        self.summary.update(
            {
                "status": status,
                "termination_reason": termination_reason,
                "termination_error": error,
                "updated_at": _utc_now(),
            }
        )
        self._write_summary()


def _prediction_provenance(
    prediction: ModelPrediction, source_frame: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "prediction_source_frame_id": source_frame["frame_id"],
        "prediction_source_frame_sha256": source_frame["sha256"],
        "raw_model_output": prediction.raw_prediction,
        "model_native_action": (
            dict(prediction.model_native_action)
            if prediction.model_native_action is not None
            else None
        ),
        "model_native_coordinate_space": prediction.model_native_coordinate_space,
        "coordinate_transform": (
            dict(prediction.coordinate_transform)
            if prediction.coordinate_transform is not None
            else None
        ),
        "model_id": prediction.model_id,
        "request_id": prediction.request_id,
    }


def _execute_action(adapter: DeviceAdapter, action: CanonicalAction) -> tuple[float, float]:
    """Return (device dispatch ms, runner-control wait ms)."""

    if isinstance(action, TapAction):
        return adapter.tap(action.x_px, action.y_px), 0.0
    if isinstance(action, SwipeAction):
        return (
            adapter.swipe(
                action.x0_px,
                action.y0_px,
                action.x1_px,
                action.y1_px,
                action.duration_ms,
            ),
            0.0,
        )
    if isinstance(action, BackAction):
        return adapter.back(), 0.0
    if isinstance(action, WaitAction):
        start = time.monotonic()
        time.sleep(action.duration_ms / 1000.0)
        return 0.0, (time.monotonic() - start) * 1000.0
    raise TypeError(f"action is not executable: {type(action).__name__}")


@dataclass(frozen=True)
class TrajectoryRunResult:
    status: str
    termination_reason: str
    step_count: int
    output_dir: Path


def run_trajectory(
    adapter: DeviceAdapter,
    client: ModelClient,
    recorder: TrajectoryRecorder,
    *,
    instruction: str,
    max_steps: int,
    post_action_settle_seconds: float = 0.5,
) -> TrajectoryRunResult:
    """Run a bounded predict/execute/capture loop with no retries or recovery policy."""

    if not instruction.strip():
        raise ValueError("instruction must not be empty")
    if type(max_steps) is not int or max_steps <= 0:
        raise ValueError("max_steps must be a positive integer")
    if post_action_settle_seconds < 0:
        raise ValueError("post_action_settle_seconds must be non-negative")

    try:
        current_frame = adapter.screenshot()
    except Exception as exc:
        recorder.finish(status="ERROR", termination_reason="DEVICE_ERROR", error=str(exc))
        return _result(recorder)
    current_metadata = recorder.save_frame(current_frame)
    history: list[Mapping[str, Any]] = []

    for step_index in range(max_steps):
        step_start = time.monotonic()
        predict_start = time.monotonic()
        try:
            prediction = client.predict(instruction, current_frame, tuple(history))
        except Exception as exc:
            recorder.finish(
                status="ERROR", termination_reason="MODEL_ERROR", error=str(exc)
            )
            return _result(recorder)
        model_predict_ms = (time.monotonic() - predict_start) * 1000.0

        parse_start = time.monotonic()
        try:
            action = parse_action_prediction(prediction, current_frame)
        except ModelBoundaryError as exc:
            recorder.finish(
                status="ERROR", termination_reason="ACTION_ERROR", error=str(exc)
            )
            return _result(recorder)
        parse_ms = (time.monotonic() - parse_start) * 1000.0

        if isinstance(action, DoneAction):
            recorder.finish(status="COMPLETE", termination_reason="DONE")
            return _result(recorder)

        try:
            action_dispatch_ms, control_wait_ms = _execute_action(adapter, action)
            settle_start = time.monotonic()
            if not isinstance(action, WaitAction):
                time.sleep(post_action_settle_seconds)
            settle_ms = (time.monotonic() - settle_start) * 1000.0
            next_frame = adapter.screenshot()
        except Exception as exc:
            recorder.finish(
                status="ERROR", termination_reason="DEVICE_ERROR", error=str(exc)
            )
            return _result(recorder)

        next_metadata = recorder.save_frame(next_frame)
        transition = {
            "step_index": step_index,
            "frame_t": current_metadata,
            "prediction": _prediction_provenance(prediction, current_metadata),
            "canonical_action": action.to_dict(),
            "execution": {"status": "SUCCESS", "error": None},
            "timing_ms": {
                "capture_frame_t_ms": current_frame.capture_ms,
                "model_predict_ms": model_predict_ms,
                "action_parse_validate_ms": parse_ms,
                "action_dispatch_ms": action_dispatch_ms,
                "control_wait_ms": control_wait_ms,
                "post_action_settle_ms": settle_ms,
                "capture_frame_t_plus_1_ms": next_frame.capture_ms,
                "total_transition_ms": (time.monotonic() - step_start) * 1000.0,
            },
            "frame_t_plus_1": next_metadata,
            "human_intervention": None,
            "progress_label": None,
            "failure_label": None,
            "notes": None,
        }
        recorder.append_transition(transition)
        history.append(
            {
                "step_index": step_index,
                "frame_t_id": current_frame.frame_id,
                "frame_t_plus_1_id": next_frame.frame_id,
                "canonical_action": action.to_dict(),
            }
        )
        current_frame = next_frame
        current_metadata = next_metadata

    recorder.finish(status="COMPLETE", termination_reason="MAX_STEPS")
    return _result(recorder)


def _result(recorder: TrajectoryRecorder) -> TrajectoryRunResult:
    return TrajectoryRunResult(
        status=recorder.summary["status"],
        termination_reason=recorder.summary["termination_reason"],
        step_count=recorder.summary["step_count"],
        output_dir=recorder.output_dir,
    )


class FixtureSequenceModelClient:
    """Deterministic scripted client for contract/live I/O evidence, never a model."""

    def __init__(self, actions: Sequence[Mapping[str, Any]]) -> None:
        self._actions = [dict(action) for action in actions]
        self._index = 0

    def predict(
        self,
        instruction: str,
        observation: Frame,
        history: Sequence[Mapping[str, Any]],
    ) -> ModelPrediction:
        del instruction, observation, history
        if self._index >= len(self._actions):
            raise RuntimeError("scripted fixture action sequence exhausted")
        action = self._actions[self._index]
        request_id = f"fixture-step-{self._index}"
        self._index += 1
        return ModelPrediction(
            structured_action=action,
            raw_prediction=json.dumps(action, sort_keys=True),
            model_id="scripted-fixture-not-a-model",
            request_id=request_id,
        )
