"""Strict model-to-device boundary for one pixel-space tap."""

from __future__ import annotations

import hashlib
import time
from dataclasses import asdict, dataclass
from typing import Any, Mapping, Protocol, Sequence

from .contracts import (
    BackAction,
    CanonicalAction,
    DisplayInfo,
    DoneAction,
    Frame,
    SwipeAction,
    TapAction,
    WaitAction,
)


class ModelBoundaryError(ValueError):
    """Raised when a model prediction is unsafe or outside the frozen contract."""


@dataclass(frozen=True)
class ModelPrediction:
    """Raw and structured output retained at the model boundary."""

    structured_action: Mapping[str, Any]
    raw_prediction: str | None = None
    model_native_action: Mapping[str, Any] | None = None
    model_native_coordinate_space: Mapping[str, Any] | str | None = None
    coordinate_transform: Mapping[str, Any] | None = None
    model_id: str | None = None
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "raw_prediction": self.raw_prediction,
            "raw_model_output": self.raw_prediction,
            "structured_action": dict(self.structured_action),
            "model_native_action": (
                dict(self.model_native_action)
                if self.model_native_action is not None
                else None
            ),
            "model_native_coordinate_space": self.model_native_coordinate_space,
            "coordinate_transform": (
                dict(self.coordinate_transform)
                if self.coordinate_transform is not None
                else None
            ),
            "model_id": self.model_id,
            "request_id": self.request_id,
        }


class ModelClient(Protocol):
    """Small interface implemented later by a model-specific adapter."""

    def predict(
        self,
        instruction: str,
        observation: Frame,
        history: Sequence[Mapping[str, Any]],
    ) -> ModelPrediction: ...


class DeviceAdapter(Protocol):
    """The device methods required by the bounded runner."""

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

    def back(self) -> float: ...

    def home(self) -> float: ...

    def type_text(self, text: str) -> float: ...


def _require_exact_fields(
    structured: Mapping[str, Any], expected_fields: set[str], action_name: str
) -> None:
    actual_fields = set(structured)
    missing = expected_fields - actual_fields
    extra = actual_fields - expected_fields
    if missing or extra:
        raise ModelBoundaryError(
            f"{action_name} fields must be exactly {sorted(expected_fields)}; "
            f"missing={sorted(missing, key=repr)}, extra={sorted(extra, key=repr)}"
        )


def _require_int(value: Any, field: str) -> int:
    if type(value) is not int:
        raise ModelBoundaryError(f"{field} must be an integer")
    return value


def _frame_display(observation: Frame) -> DisplayInfo:
    return DisplayInfo(
        width_px=observation.width_px,
        height_px=observation.height_px,
        density_dpi=observation.density_dpi,
        orientation=observation.orientation,
    )


def parse_action_prediction(
    prediction: ModelPrediction,
    observation: Frame,
) -> CanonicalAction:
    """Parse the strict canonical action union v0.2 without repair/conversion."""

    if not isinstance(prediction, ModelPrediction):
        raise ModelBoundaryError("ModelClient.predict() must return ModelPrediction")
    structured = prediction.structured_action
    if not isinstance(structured, Mapping):
        raise ModelBoundaryError("structured_action must be a mapping")

    action_type = structured.get("type")
    try:
        if action_type == "tap":
            _require_exact_fields(structured, {"type", "x_px", "y_px"}, "tap")
            action: CanonicalAction = TapAction(
                _require_int(structured["x_px"], "x_px"),
                _require_int(structured["y_px"], "y_px"),
            )
            action.validate(_frame_display(observation))
            return action

        if action_type == "swipe":
            fields = {
                "type",
                "x0_px",
                "y0_px",
                "x1_px",
                "y1_px",
                "duration_ms",
            }
            _require_exact_fields(structured, fields, "swipe")
            action = SwipeAction(
                _require_int(structured["x0_px"], "x0_px"),
                _require_int(structured["y0_px"], "y0_px"),
                _require_int(structured["x1_px"], "x1_px"),
                _require_int(structured["y1_px"], "y1_px"),
                _require_int(structured["duration_ms"], "duration_ms"),
            )
            action.validate(_frame_display(observation))
            return action

        if action_type == "key":
            _require_exact_fields(structured, {"type", "key"}, "key")
            if structured["key"] != "BACK":
                raise ModelBoundaryError("model-facing key action is restricted to BACK")
            return BackAction()

        if action_type == "wait":
            _require_exact_fields(structured, {"type", "duration_ms"}, "wait")
            action = WaitAction(
                _require_int(structured["duration_ms"], "duration_ms")
            )
            action.validate()
            return action

        if action_type == "done":
            _require_exact_fields(structured, {"type"}, "done")
            return DoneAction()
    except ValueError as exc:
        raise ModelBoundaryError(str(exc)) from exc

    raise ModelBoundaryError(f"unsupported model action type: {action_type!r}")


def parse_tap_prediction(
    prediction: ModelPrediction,
    observation: Frame,
) -> TapAction:
    """Parse exactly one original-screenshot-pixel tap without conversion."""

    if not isinstance(prediction, ModelPrediction):
        raise ModelBoundaryError("ModelClient.predict() must return ModelPrediction")
    structured = prediction.structured_action
    if not isinstance(structured, Mapping):
        raise ModelBoundaryError("structured_action must be a mapping")
    if structured.get("type") != "tap":
        raise ModelBoundaryError(
            f"unsupported model action type: {structured.get('type')!r}"
        )
    action = parse_action_prediction(prediction, observation)
    assert isinstance(action, TapAction)
    return action


@dataclass(frozen=True)
class OneStepTiming:
    model_predict_ms: float
    action_parse_validate_ms: float
    action_dispatch_ms: float
    settle_ms: float
    total_step_ms: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class OneStepResult:
    instruction: str
    observation: Frame
    prediction: ModelPrediction
    action: TapAction
    next_observation: Frame
    timing: OneStepTiming

    def to_manifest(self) -> dict[str, Any]:
        before = self.observation.metadata()
        before["sha256"] = hashlib.sha256(self.observation.png_bytes).hexdigest()
        after = self.next_observation.metadata()
        after["sha256"] = hashlib.sha256(
            self.next_observation.png_bytes
        ).hexdigest()
        return {
            "schema_version": "mobile-gui-vla.model-boundary-step.v0.1",
            "instruction": self.instruction,
            "coordinate_contract": "original_screenshot_pixels",
            "observation": before,
            "prediction": self.prediction.to_dict(),
            "canonical_action": self.action.to_dict(),
            "next_observation": after,
            "observable_ui_change": before["sha256"] != after["sha256"],
            "timing_ms": {
                "capture_before_ms": self.observation.capture_ms,
                **self.timing.to_dict(),
                "capture_after_ms": self.next_observation.capture_ms,
            },
        }


def run_one_step(
    adapter: DeviceAdapter,
    client: ModelClient,
    *,
    instruction: str,
    history: Sequence[Mapping[str, Any]] = (),
    settle_seconds: float = 1.0,
) -> OneStepResult:
    """Run screenshot -> predict -> validate -> tap -> settle -> screenshot."""

    if not instruction.strip():
        raise ValueError("instruction must not be empty")
    if settle_seconds < 0:
        raise ValueError("settle_seconds must be non-negative")

    total_start = time.monotonic()
    observation = adapter.screenshot()

    predict_start = time.monotonic()
    prediction = client.predict(instruction, observation, history)
    model_predict_ms = (time.monotonic() - predict_start) * 1000.0

    parse_start = time.monotonic()
    action = parse_tap_prediction(prediction, observation)
    action_parse_validate_ms = (time.monotonic() - parse_start) * 1000.0

    action_dispatch_ms = adapter.tap(action.x_px, action.y_px)
    settle_start = time.monotonic()
    time.sleep(settle_seconds)
    settle_ms = (time.monotonic() - settle_start) * 1000.0

    next_observation = adapter.screenshot()
    total_step_ms = (time.monotonic() - total_start) * 1000.0
    return OneStepResult(
        instruction=instruction,
        observation=observation,
        prediction=prediction,
        action=action,
        next_observation=next_observation,
        timing=OneStepTiming(
            model_predict_ms=model_predict_ms,
            action_parse_validate_ms=action_parse_validate_ms,
            action_dispatch_ms=action_dispatch_ms,
            settle_ms=settle_ms,
            total_step_ms=total_step_ms,
        ),
    )


@dataclass(frozen=True)
class FixtureTapModelClient:
    """Deterministic contract fixture; this is not GUI-VLA inference."""

    x_px: int
    y_px: int

    def predict(
        self,
        instruction: str,
        observation: Frame,
        history: Sequence[Mapping[str, Any]],
    ) -> ModelPrediction:
        del instruction, observation, history
        return ModelPrediction(
            structured_action={"type": "tap", "x_px": self.x_px, "y_px": self.y_px},
            raw_prediction=(
                f'fixture_static_tap(type="tap", x_px={self.x_px}, y_px={self.y_px})'
            ),
        )
