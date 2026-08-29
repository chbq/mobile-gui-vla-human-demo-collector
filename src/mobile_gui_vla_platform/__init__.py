"""Portable Android device I/O for Mobile GUI-VLA experiments."""

from .adb import ADBDeviceAdapter, discover_endpoints
from .contracts import (
    BackAction,
    CanonicalAction,
    DeviceInfo,
    DisplayInfo,
    DoneAction,
    Frame,
    HomeAction,
    SwipeAction,
    TapAction,
    TypeAction,
    WaitAction,
)
from .model_boundary import (
    FixtureTapModelClient,
    ModelBoundaryError,
    ModelClient,
    ModelPrediction,
    OneStepResult,
    parse_action_prediction,
    parse_tap_prediction,
    run_one_step,
)
from .trajectory import (
    FixtureSequenceModelClient,
    TrajectoryRecorder,
    TrajectoryRunResult,
    run_trajectory,
)

__all__ = [
    "ADBDeviceAdapter",
    "BackAction",
    "CanonicalAction",
    "DeviceInfo",
    "DisplayInfo",
    "DoneAction",
    "Frame",
    "HomeAction",
    "FixtureTapModelClient",
    "FixtureSequenceModelClient",
    "ModelBoundaryError",
    "ModelClient",
    "ModelPrediction",
    "OneStepResult",
    "SwipeAction",
    "TapAction",
    "TypeAction",
    "TrajectoryRecorder",
    "TrajectoryRunResult",
    "WaitAction",
    "discover_endpoints",
    "parse_action_prediction",
    "parse_tap_prediction",
    "run_one_step",
    "run_trajectory",
]

__version__ = "0.2.0"
