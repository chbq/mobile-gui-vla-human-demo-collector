"""Reload-based raw trajectory, annotation, privacy, and train-safety QA."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


SENSITIVE_TEXT = re.compile(
    r"\b(password|passwd|credit\s*card|cvv|authentication\s*code|private\s*message)\b",
    re.IGNORECASE,
)
LONG_NUMBER = re.compile(r"(?<!\d)\d{13,19}(?!\d)")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(dict(payload), stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _read_json(path: Path, errors: list[str], label: str) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"{label} is not parseable: {exc}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label} must be a JSON object")
        return None
    return value


def _read_steps(path: Path, errors: list[str]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        errors.append(f"steps.jsonl cannot be read: {exc}")
        return steps
    for line_number, line in enumerate(lines, 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"steps.jsonl line {line_number} is invalid JSON: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"steps.jsonl line {line_number} is not an object")
            continue
        steps.append(value)
    return steps


def _validate_frame(
    raw_dir: Path,
    frame: Any,
    *,
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(frame, Mapping):
        errors.append(f"{label} metadata is missing")
        return
    relative = frame.get("path")
    if not isinstance(relative, str):
        errors.append(f"{label}.path is missing")
        return
    candidate = (raw_dir / relative).resolve()
    try:
        candidate.relative_to(raw_dir.resolve())
    except ValueError:
        errors.append(f"{label}.path escapes the raw trajectory directory")
        return
    if not candidate.is_file():
        errors.append(f"{label} file does not exist")
        return
    expected = frame.get("sha256")
    actual = _sha256_path(candidate)
    if expected != actual:
        errors.append(f"{label} SHA-256 mismatch")
    for dimension in ("width_px", "height_px"):
        if type(frame.get(dimension)) is not int or frame[dimension] <= 0:
            errors.append(f"{label}.{dimension} must be a positive integer")


def _validate_action(step: Mapping[str, Any], errors: list[str]) -> None:
    action = step.get("canonical_action")
    if not isinstance(action, Mapping):
        errors.append("canonical_action is missing")
        return
    source = step.get("frame_t")
    width = source.get("width_px") if isinstance(source, Mapping) else None
    height = source.get("height_px") if isinstance(source, Mapping) else None
    action_type = action.get("type")
    if action_type == "tap":
        _coordinate(action, "x_px", width, errors)
        _coordinate(action, "y_px", height, errors)
    elif action_type == "swipe":
        _coordinate(action, "x0_px", width, errors)
        _coordinate(action, "x1_px", width, errors)
        _coordinate(action, "y0_px", height, errors)
        _coordinate(action, "y1_px", height, errors)
        duration = action.get("duration_ms")
        if type(duration) is not int or not 1 <= duration <= 10_000:
            errors.append("swipe duration_ms is invalid")
    elif action_type == "type":
        text = action.get("text")
        if (
            not isinstance(text, str)
            or not text
            or len(text) > 1_024
            or any(ord(character) < 0x20 or ord(character) > 0x7E for character in text)
        ):
            errors.append("type text violates the printable ASCII pilot contract")
    elif action_type == "key":
        if action.get("key") not in {"BACK", "HOME"}:
            errors.append("key action is not BACK or HOME")
    elif action_type == "wait":
        duration = action.get("duration_ms")
        if type(duration) is not int or not 0 <= duration <= 60_000:
            errors.append("wait duration_ms is invalid")
    else:
        errors.append(f"unsupported canonical action type: {action_type!r}")


def _coordinate(
    action: Mapping[str, Any], field: str, limit: Any, errors: list[str]
) -> None:
    value = action.get(field)
    if type(value) is not int or type(limit) is not int or not 0 <= value < limit:
        errors.append(f"{field} is outside the source frame")


def _validate_actor_and_intervention(
    steps: list[dict[str, Any]], errors: list[str]
) -> None:
    for index, step in enumerate(steps):
        actor = step.get("actor")
        source = actor.get("source") if isinstance(actor, Mapping) else None
        if source not in {"human", "model", "scripted_fixture"}:
            errors.append(f"step {index} actor provenance is missing or invalid")
        intervention = step.get("human_intervention")
        if intervention is None:
            continue
        if not isinstance(intervention, Mapping) or intervention.get("active") is not True:
            errors.append(f"step {index} intervention metadata is invalid")
            continue
        simulated_role = actor.get("simulated_role") if isinstance(actor, Mapping) else None
        if source != "human" and not (
            source == "scripted_fixture" and simulated_role == "human"
        ):
            errors.append(
                f"step {index} intervention action is not human-authored or a human-role fixture"
            )
        kind = intervention.get("kind")
        if kind == "preventive_override":
            proposal = step.get("model_proposal")
            if not isinstance(proposal, Mapping) or proposal.get("executed") is not False:
                errors.append(
                    f"step {index} preventive override lacks unexecuted proposal"
                )
        elif kind == "post_error_takeover":
            trigger = intervention.get("trigger_step_index")
            if type(trigger) is not int or not 0 <= trigger < index:
                errors.append(f"step {index} post-error trigger is invalid")
            else:
                trigger_actor = steps[trigger].get("actor")
                trigger_source = (
                    trigger_actor.get("source")
                    if isinstance(trigger_actor, Mapping)
                    else None
                )
                trigger_role = (
                    trigger_actor.get("simulated_role")
                    if isinstance(trigger_actor, Mapping)
                    else None
                )
                if trigger_source != "model" and not (
                    trigger_source == "scripted_fixture" and trigger_role == "model"
                ):
                    errors.append(
                        f"step {index} post-error trigger does not preserve model/fixture action"
                    )
        else:
            errors.append(f"step {index} intervention kind is invalid")


def _validate_observation_refresh(
    refresh: Any,
    *,
    previous_next: Mapping[str, Any],
    source: Mapping[str, Any],
    step_index: int,
    errors: list[str],
) -> None:
    if not isinstance(refresh, Mapping) or refresh.get("kind") != (
        "live_preview_selected_as_action_source"
    ):
        errors.append(
            f"step {step_index} source discontinuity lacks live-preview provenance"
        )
        return
    previous = refresh.get("previous_recorded_frame")
    selected = refresh.get("selected_preview")
    if not isinstance(previous, Mapping) or (
        previous.get("frame_id") != previous_next.get("frame_id")
        or previous.get("sha256") != previous_next.get("sha256")
    ):
        errors.append(f"step {step_index} preview provenance has the wrong prior frame")
    if not isinstance(selected, Mapping) or (
        selected.get("frame_id") != source.get("frame_id")
        or selected.get("sha256") != source.get("sha256")
        or not isinstance(selected.get("preview_id"), str)
    ):
        errors.append(
            f"step {step_index} preview provenance does not bind the action source"
        )


def privacy_findings(record: Mapping[str, Any], steps: Iterable[Mapping[str, Any]]) -> list[str]:
    texts: list[str] = []
    task = record.get("task")
    if isinstance(task, Mapping) and isinstance(task.get("instruction"), str):
        texts.append(task["instruction"])
    outcome = record.get("outcome")
    if isinstance(outcome, Mapping) and isinstance(outcome.get("note"), str):
        texts.append(outcome["note"])
    for step in steps:
        action = step.get("canonical_action")
        if isinstance(action, Mapping) and action.get("type") == "type":
            if isinstance(action.get("text"), str):
                texts.append(action["text"])
        if isinstance(step.get("notes"), str):
            texts.append(step["notes"])
    findings: list[str] = []
    combined = "\n".join(texts)
    if SENSITIVE_TEXT.search(combined):
        findings.append("sensitive_keyword_pattern")
    if LONG_NUMBER.search(combined):
        findings.append("long_numeric_identifier_pattern")
    return findings


def validate_annotation(annotation_path: Path) -> dict[str, Any]:
    annotation_path = Path(annotation_path)
    errors: list[str] = []
    warnings: list[str] = []
    record = _read_json(annotation_path, errors, "annotation")
    if record is None:
        return _result(annotation_path, None, False, errors, warnings, 0, [])
    trajectory_id = record.get("trajectory_id")
    raw_uri = record.get("raw_trajectory_uri")
    raw_dir = Path(raw_uri) if isinstance(raw_uri, str) else Path("/__missing__")
    summary = _read_json(raw_dir / "trajectory.json", errors, "trajectory.json")
    steps = _read_steps(raw_dir / "steps.jsonl", errors)
    if summary is not None:
        if summary.get("trajectory_id") != trajectory_id:
            errors.append("annotation and raw trajectory IDs do not match")
        if summary.get("status") == "OPEN":
            errors.append("raw trajectory is not terminal")
        if summary.get("step_count") != len(steps):
            errors.append("trajectory step_count does not match steps.jsonl")
        if summary.get("termination_reason") is None:
            errors.append("raw trajectory has no terminal reason")
    previous_next: Mapping[str, Any] | None = None
    if not steps:
        errors.append("trajectory contains no transitions")
    for index, step in enumerate(steps):
        if step.get("step_index") != index:
            errors.append(f"step index is not contiguous at position {index}")
        source = step.get("frame_t")
        next_frame = step.get("frame_t_plus_1")
        _validate_frame(raw_dir, source, label=f"step {index} frame_t", errors=errors)
        _validate_frame(
            raw_dir,
            next_frame,
            label=f"step {index} frame_t_plus_1",
            errors=errors,
        )
        if previous_next is not None and isinstance(source, Mapping):
            if (
                source.get("frame_id") != previous_next.get("frame_id")
                or source.get("sha256") != previous_next.get("sha256")
            ):
                _validate_observation_refresh(
                    step.get("observation_refresh"),
                    previous_next=previous_next,
                    source=source,
                    step_index=index,
                    errors=errors,
                )
        if isinstance(next_frame, Mapping):
            previous_next = next_frame
        _validate_action(step, errors)
        execution = step.get("execution")
        if not isinstance(execution, Mapping) or execution.get("status") != "SUCCESS":
            errors.append(f"step {index} is not a completed successful transition")
        stability = step.get("stable_capture")
        if stability is not None:
            if not isinstance(stability, Mapping):
                errors.append(f"step {index} stable_capture is invalid")
            elif stability.get("stable") is not True:
                errors.append(f"step {index} did not reach a visually stable next frame")
            elif (
                type(stability.get("sample_count")) is not int
                or stability["sample_count"] < 1
            ):
                errors.append(f"step {index} stable_capture sample_count is invalid")
    _validate_actor_and_intervention(steps, errors)
    privacy = record.get("privacy")
    if not isinstance(privacy, Mapping):
        errors.append("privacy state is missing")
    else:
        if privacy.get("contains_sensitive_data") is True and privacy.get(
            "redaction_status"
        ) == "clean":
            errors.append("sensitive trajectory is incorrectly marked clean")
        if privacy.get("redaction_status") not in {"clean", "redacted", "quarantine"}:
            errors.append("privacy redaction_status is invalid")
    outcome = record.get("outcome")
    if not isinstance(outcome, Mapping) or outcome.get("status") not in {
        "success",
        "partial",
        "failure",
        "aborted",
        "env_error",
    }:
        errors.append("terminal outcome is missing or invalid")
    provenance = record.get("provenance")
    official = bool(
        isinstance(provenance, Mapping) and provenance.get("official_eval_case", False)
    )
    if official and record.get("training_eligible"):
        errors.append("official evaluation provenance is marked training eligible")
    collection = record.get("collection")
    mode = collection.get("collection_mode") if isinstance(collection, Mapping) else None
    if mode == "scripted_schema_test" and record.get("training_eligible"):
        errors.append("scripted schema fixture is marked training eligible")
    findings = privacy_findings(record, steps)
    if findings:
        warnings.extend(findings)
        if isinstance(privacy, Mapping) and privacy.get("redaction_status") == "clean":
            errors.append("privacy scan findings require redaction or quarantine review")
    selection_status = record.get("selection_status", "active")
    if selection_status not in {"active", "superseded"}:
        errors.append("selection_status is invalid")
    excluded = selection_status == "superseded"
    if excluded:
        warnings.append("superseded_runtime_evidence")
    accepted = not errors and not excluded and (
        not isinstance(privacy, Mapping)
        or privacy.get("redaction_status") != "quarantine"
    )
    return _result(
        annotation_path,
        trajectory_id if isinstance(trajectory_id, str) else None,
        accepted,
        errors,
        warnings,
        len(steps),
        findings,
        training_eligible=bool(record.get("training_eligible", False)),
        data_class=record.get("data_class"),
        task=record.get("task"),
        app=record.get("app"),
        excluded=excluded,
        excluded_reason=record.get("superseded_reason") if excluded else None,
    )


def _result(
    annotation_path: Path,
    trajectory_id: str | None,
    accepted: bool,
    errors: list[str],
    warnings: list[str],
    step_count: int,
    findings: list[str],
    *,
    training_eligible: bool = False,
    data_class: Any = None,
    task: Any = None,
    app: Any = None,
    excluded: bool = False,
    excluded_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "mobile-gui-vla.trajectory-qa.v0.1",
        "validated_at": _utc_now(),
        "trajectory_id": trajectory_id,
        "annotation_uri": str(annotation_path),
        "annotation_sha256": (
            _sha256_path(annotation_path) if annotation_path.is_file() else None
        ),
        "accepted": accepted,
        "excluded": excluded,
        "excluded_reason": excluded_reason,
        "training_eligible": training_eligible,
        "step_count": step_count,
        "errors": errors,
        "warnings": warnings,
        "privacy_findings": findings,
        "data_class": data_class,
        "task": task,
        "app": app,
    }


def validate_all(artifact_root: Path) -> dict[str, Any]:
    artifact_root = Path(artifact_root)
    qa_dir = artifact_root / "qa"
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for annotation in sorted((artifact_root / "annotations").glob("*.json")):
        result = validate_annotation(annotation)
        trajectory_id = result.get("trajectory_id")
        if trajectory_id in seen:
            result["accepted"] = False
            result["errors"].append("duplicate trajectory_id across annotations")
        elif isinstance(trajectory_id, str):
            seen.add(trajectory_id)
        target = qa_dir / f"{annotation.stem}.qa.json"
        _atomic_json(target, result)
        results.append(result)
    summary = {
        "schema_version": "mobile-gui-vla.qa-summary.v0.1",
        "validated_at": _utc_now(),
        "total": len(results),
        "accepted": sum(bool(result["accepted"]) for result in results),
        "excluded": sum(bool(result.get("excluded")) for result in results),
        "training_eligible": sum(
            bool(result["accepted"] and result["training_eligible"])
            for result in results
        ),
        "rejected": sum(
            not bool(result["accepted"]) and not bool(result.get("excluded"))
            for result in results
        ),
        "data_class_histogram": dict(
            sorted(
                Counter(str(result.get("data_class")) for result in results).items()
            )
        ),
        "accepted_data_class_histogram": dict(
            sorted(
                Counter(
                    str(result.get("data_class"))
                    for result in results
                    if result["accepted"]
                ).items()
            )
        ),
        "results": [
            {
                "trajectory_id": result["trajectory_id"],
                "accepted": result["accepted"],
                "excluded": result.get("excluded", False),
                "training_eligible": result["training_eligible"],
                "error_count": len(result["errors"]),
            }
            for result in results
        ],
    }
    _atomic_json(qa_dir / "summary.json", summary)
    return summary
