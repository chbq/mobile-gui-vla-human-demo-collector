"""Deterministic manifest selection and model-neutral JSONL export."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(dict(payload), stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def build_manifest(
    artifact_root: Path,
    *,
    dataset_version: str,
    parent_manifest: str | None = None,
    seed: int = 0,
    include_non_training: bool = False,
    manifest_role: str = "training",
) -> Path:
    artifact_root = Path(artifact_root)
    qa_paths = sorted((artifact_root / "qa").glob("*.qa.json"))
    selected: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for qa_path in qa_paths:
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        if not qa.get("accepted"):
            continue
        if not include_non_training and not qa.get("training_eligible"):
            continue
        annotation_path = Path(qa["annotation_uri"])
        record = json.loads(annotation_path.read_text(encoding="utf-8"))
        provenance = record.get("provenance")
        if isinstance(provenance, Mapping) and provenance.get("official_eval_case"):
            continue
        records.append(record)
        selected.append(
            {
                "trajectory_id": record["trajectory_id"],
                "annotation_uri": str(annotation_path),
                "annotation_sha256": _sha256(annotation_path),
                "qa_uri": str(qa_path),
                "qa_sha256": _sha256(qa_path),
            }
        )
    selected.sort(key=lambda value: value["trajectory_id"])
    if not selected:
        raise ValueError("no trajectories satisfy the requested manifest policy")
    capabilities = Counter()
    for record in records:
        capabilities.update(record["task"].get("capability_labels", []))
    manifest = {
        "schema_version": "mobile-gui-vla.dataset-manifest.v0.1",
        "dataset_version": dataset_version,
        "parent_manifest": parent_manifest,
        "seed": seed,
        "selection_policy": {
            "qa_accepted": True,
            "training_eligible": not include_non_training,
            "official_eval_case": False,
            "privacy_quarantine": False,
            "ordering": "trajectory_id_ascending",
        },
        "manifest_role": manifest_role,
        "training_manifest": not include_non_training,
        "trajectory_ids": [value["trajectory_id"] for value in selected],
        "entries": selected,
        "class_histogram": _histogram(records, "data_class"),
        "capability_histogram": dict(sorted(capabilities.items())),
        "app_histogram": _nested_histogram(records, "app", "package_name"),
        "template_histogram": _nested_histogram(records, "task", "template_id"),
    }
    path = artifact_root / "manifests" / f"{dataset_version}.json"
    _write_json(path, manifest)
    return path


def _histogram(records: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(record.get(field)) for record in records).items()))


def _nested_histogram(
    records: list[dict[str, Any]], container: str, field: str
) -> dict[str, int]:
    values = []
    for record in records:
        nested = record.get(container)
        value = nested.get(field) if isinstance(nested, Mapping) else None
        values.append(str(value))
    return dict(sorted(Counter(values).items()))


def export_model_neutral(
    manifest_path: Path,
    *,
    output_path: Path,
) -> dict[str, Any]:
    manifest_path = Path(manifest_path)
    output_path = Path(output_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + f".{os.getpid()}.tmp")
    sample_count = 0
    with temporary.open("x", encoding="utf-8") as stream:
        for entry in sorted(manifest["entries"], key=lambda value: value["trajectory_id"]):
            annotation = json.loads(Path(entry["annotation_uri"]).read_text(encoding="utf-8"))
            raw_dir = Path(annotation["raw_trajectory_uri"])
            steps = [
                json.loads(line)
                for line in (raw_dir / "steps.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            for step in steps:
                sample = {
                    "schema_version": "mobile-gui-vla.model-neutral-sample.v0.1",
                    "sample_id": f"{annotation['trajectory_id']}:{step['step_index']:06d}",
                    "trajectory_id": annotation["trajectory_id"],
                    "step_index": step["step_index"],
                    "instruction": annotation["task"]["instruction"],
                    "task_family": annotation["task"].get("task_family"),
                    "capability_labels": annotation["task"].get(
                        "capability_labels", []
                    ),
                    "data_class": annotation["data_class"],
                    "observation": _portable_frame(step["frame_t"]),
                    "actor": step["actor"],
                    "action": step["canonical_action"],
                    "next_observation": _portable_frame(step["frame_t_plus_1"]),
                    "intervention": step.get("human_intervention"),
                    "model_proposal": step.get("model_proposal"),
                    "outcome": annotation["outcome"],
                }
                stream.write(
                    json.dumps(sample, sort_keys=True, separators=(",", ":")) + "\n"
                )
                sample_count += 1
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output_path)
    receipt = {
        "schema_version": "mobile-gui-vla.export-receipt.v0.1",
        "created_at": _utc_now(),
        "manifest_uri": str(manifest_path),
        "manifest_sha256": _sha256(manifest_path),
        "export_uri": str(output_path),
        "export_sha256": _sha256(output_path),
        "sample_count": sample_count,
        "ordering": "trajectory_id_then_step_index_ascending",
    }
    _write_json(output_path.with_suffix(output_path.suffix + ".receipt.json"), receipt)
    return receipt


def _portable_frame(frame: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "path": frame["path"],
        "sha256": frame["sha256"],
        "width_px": frame["width_px"],
        "height_px": frame["height_px"],
        "orientation": frame.get("orientation"),
    }
