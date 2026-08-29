"""Bounded API-33 P0 reference collection through the collector backend."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from mobile_gui_vla_platform import ADBDeviceAdapter

from .collector import CollectionService
from .export import build_manifest, export_model_neutral
from .qa import validate_all


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(dict(payload), stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _token(session) -> dict[str, str]:
    return {
        "frame_id": session.current_metadata["frame_id"],
        "frame_sha256": session.current_metadata["sha256"],
    }


def _request(action: Mapping[str, Any], session) -> dict[str, Any]:
    result = {**_token(session), "type": action["type"]}
    action_type = action["type"]
    if action_type == "tap":
        result.update(
            {
                "x": action["x_px"],
                "y": action["y_px"],
                "viewport": {
                    "x": 0,
                    "y": 0,
                    "width": session.current_frame.width_px,
                    "height": session.current_frame.height_px,
                },
            }
        )
    elif action_type == "swipe":
        result.update(
            {
                "x0": action["x0_px"],
                "y0": action["y0_px"],
                "x1": action["x1_px"],
                "y1": action["y1_px"],
                "duration_ms": action["duration_ms"],
                "viewport": {
                    "x": 0,
                    "y": 0,
                    "width": session.current_frame.width_px,
                    "height": session.current_frame.height_px,
                },
            }
        )
    elif action_type == "type":
        result["text"] = action["text"]
    elif action_type == "wait":
        result["duration_ms"] = action["duration_ms"]
    result["actor_source"] = "scripted_fixture"
    result["actor_role"] = action.get("actor_role", "reference")
    if "intervention" in action:
        result["intervention"] = action["intervention"]
    if "model_proposal" in action:
        result["model_proposal"] = action["model_proposal"]
    return result


def _reset_settings(adb_path: str, serial: str) -> None:
    result = subprocess.run(
        [
            adb_path,
            "-s",
            serial,
            "shell",
            "am",
            "start",
            "-W",
            "-a",
            "android.settings.SETTINGS",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    if result.returncode != 0 or "Status: ok" not in result.stdout:
        raise RuntimeError(f"failed to reset Settings: {result.stderr or result.stdout}")
    time.sleep(0.5)
    for _ in range(3):
        scroll = subprocess.run(
            [
                adb_path,
                "-s",
                serial,
                "shell",
                "input",
                "swipe",
                "540",
                "700",
                "540",
                "2200",
                "250",
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=15,
        )
        if scroll.returncode != 0:
            raise RuntimeError(f"failed to restore Settings scroll: {scroll.stderr}")
        time.sleep(0.2)
    time.sleep(0.5)


def run_p0(
    *,
    artifact_root: Path,
    plans_path: Path,
    adb_path: str,
    serial: str,
    device_alias: str,
    platform_base: str,
    platform_dependency: str,
    rerun_tasks: set[str] | None = None,
) -> dict[str, Any]:
    payload = json.loads(Path(plans_path).read_text(encoding="utf-8"))
    plans = payload.get("plans")
    if not isinstance(plans, list) or len(plans) != 10:
        raise ValueError("P0 plan must contain exactly 10 reviewed reference tasks")
    adapter = ADBDeviceAdapter(
        serial,
        alias=device_alias,
        adb_path=adb_path,
    )
    device = adapter.get_device_info()
    if device.endpoint_type != "emulator" or device.connection_state != "device":
        raise RuntimeError("P0 is restricted to a ready emulator endpoint")
    service = CollectionService(
        artifact_root=artifact_root,
        device_factories={device_alias: lambda: adapter},
        platform_dependency={
            "base_commit": platform_base,
            "dependency_commit": platform_dependency,
        },
        allow_natural_model=False,
        post_action_settle_seconds=0.75,
    )
    results = []
    rerun_tasks = set(rerun_tasks or ())
    known_task_ids = {plan["task_id"] for plan in plans}
    unknown_reruns = rerun_tasks - known_task_ids
    if unknown_reruns:
        raise ValueError(f"unknown --rerun-task values: {sorted(unknown_reruns)}")
    existing_by_task: dict[str, tuple[Path, dict[str, Any]]] = {}
    for annotation_path in sorted((Path(artifact_root) / "annotations").glob("*.json")):
        existing = json.loads(annotation_path.read_text(encoding="utf-8"))
        existing_task = existing.get("task", {}).get("task_id")
        if (
            isinstance(existing_task, str)
            and existing.get("selection_status", "active") == "active"
        ):
            if existing_task in existing_by_task:
                raise RuntimeError(f"duplicate existing P0 task record: {existing_task}")
            existing_by_task[existing_task] = (annotation_path, existing)
    for plan in plans:
        superseded: tuple[Path, dict[str, Any]] | None = None
        if plan["task_id"] in rerun_tasks and plan["task_id"] in existing_by_task:
            superseded = existing_by_task.pop(plan["task_id"])
            superseded[1]["selection_status"] = "superseded"
            superseded[1]["superseded_at"] = _utc_now()
            superseded[1]["superseded_reason"] = (
                "post-action screenshot captured before UI settle"
            )
            _atomic_json(superseded[0], superseded[1])
        if plan["task_id"] in existing_by_task:
            existing = existing_by_task[plan["task_id"]][1]
            raw_dir = Path(existing["raw_trajectory_uri"])
            raw_summary = json.loads(
                (raw_dir / "trajectory.json").read_text(encoding="utf-8")
            )
            results.append(
                {
                    "task_id": plan["task_id"],
                    "trajectory_id": existing["trajectory_id"],
                    "step_count": raw_summary["step_count"],
                    "training_eligible": existing["training_eligible"],
                    "resumed_existing": True,
                }
            )
            continue
        _reset_settings(adb_path, serial)
        session = service.start_session(
            device_alias=device_alias,
            task=plan,
            collector_id="scripted-p0-reference",
            collection_mode="scripted_schema_test",
            app={
                "package_name": "com.android.settings",
                "version_name": None,
                "version_code": None,
                "locale": "en-US",
            },
            provenance={
                "origin": "api33_scripted_p0_reference",
                "official_eval_case": False,
                "split_group_id": plan["task_id"],
                "task_setup": "launch Settings homepage and scroll list to top before raw capture",
            },
            training_eligible=False,
        )
        for action in plan["actions"]:
            service.execute(session.session_id, _request(action, session))
        record = service.finalize(
            session.session_id,
            outcome="success",
            note=(
                "Scripted P0 reference fixture for collector/schema/UX calibration; "
                "not human-authored and not training eligible."
            ),
        )
        if superseded is not None:
            superseded[1]["superseded_by"] = record["trajectory_id"]
            _atomic_json(superseded[0], superseded[1])
        results.append(
            {
                "task_id": plan["task_id"],
                "trajectory_id": record["trajectory_id"],
                "step_count": len(session.transitions),
                "training_eligible": record["training_eligible"],
                "resumed_existing": False,
            }
        )
    qa = validate_all(artifact_root)
    manifest = build_manifest(
        artifact_root,
        dataset_version="p0-reference-v0.1",
        include_non_training=True,
        manifest_role="reference_fixture",
    )
    receipt = export_model_neutral(
        manifest,
        output_path=artifact_root / "exports" / "p0-reference-v0.1.jsonl",
    )
    summary = {
        "schema_version": "mobile-gui-vla.p0-run-summary.v0.1",
        "completed_at": _utc_now(),
        "device_alias": device_alias,
        "endpoint_type": device.endpoint_type,
        "api_level": device.api_level,
        "task_count": len(results),
        "results": results,
        "qa": qa,
        "reference_manifest": str(manifest),
        "model_neutral_export": receipt,
        "training_boundary": {
            "training_eligible_count": qa["training_eligible"],
            "reason": "P0 was scripted reference collection; P1 human collection not yet available",
        },
    }
    path = Path(artifact_root) / "p0-run-summary.json"
    _atomic_json(path, summary)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--plans", type=Path, required=True)
    parser.add_argument("--adb", required=True)
    parser.add_argument("--adb-server-socket", default=None)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--device-alias", required=True)
    parser.add_argument("--platform-base", required=True)
    parser.add_argument("--platform-dependency", required=True)
    parser.add_argument("--rerun-task", action="append", default=[])
    args = parser.parse_args(argv)
    if args.adb_server_socket:
        os.environ["ADB_SERVER_SOCKET"] = args.adb_server_socket
    summary = run_p0(
        artifact_root=args.artifact_root,
        plans_path=args.plans,
        adb_path=args.adb,
        serial=args.serial,
        device_alias=args.device_alias,
        platform_base=args.platform_base,
        platform_dependency=args.platform_dependency,
        rerun_tasks=set(args.rerun_task),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
