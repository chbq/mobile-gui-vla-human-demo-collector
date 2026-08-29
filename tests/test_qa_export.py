import json
import tempfile
import unittest
from pathlib import Path

from mobile_gui_vla_data_lab.export import build_manifest, export_model_neutral
from mobile_gui_vla_data_lab.qa import validate_all

from helpers import service, task, token


class QAExportTests(unittest.TestCase):
    def test_qa_manifest_and_export_are_closed_and_deterministic(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            lab = service(root)
            normal = lab.start_session(
                device_alias="avd-p0",
                task=task("accepted-normal"),
                collector_id="collector-fixture",
            )
            lab.execute(normal.session_id, {**token(normal), "type": "back"})
            lab.finalize(normal.session_id, outcome="success")

            fixture = lab.start_session(
                device_alias="avd-p0",
                task=task("fixture-recovery", "recovery"),
                collector_id="collector-fixture",
                collection_mode="scripted_schema_test",
            )
            lab.execute(
                fixture.session_id,
                {
                    **token(fixture),
                    "type": "wait",
                    "duration_ms": 0,
                    "actor_source": "scripted_fixture",
                    "actor_role": "reference",
                },
            )
            lab.finalize(fixture.session_id, outcome="success")

            summary = validate_all(root)
            self.assertEqual(summary["accepted"], 2)
            self.assertEqual(summary["training_eligible"], 1)
            self.assertEqual(
                summary["accepted_data_class_histogram"],
                {"normal": 1, "recovery": 1},
            )
            manifest = build_manifest(root, dataset_version="p0-v0.1")
            first = root / "exports" / "first.jsonl"
            second = root / "exports" / "second.jsonl"
            first_receipt = export_model_neutral(manifest, output_path=first)
            second_receipt = export_model_neutral(manifest, output_path=second)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(first_receipt["export_sha256"], second_receipt["export_sha256"])
            self.assertEqual(first_receipt["sample_count"], 1)

    def test_frame_tamper_is_rejected_on_reload(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            lab = service(root)
            session = lab.start_session(
                device_alias="avd-p0", task=task(), collector_id="collector-fixture"
            )
            lab.execute(session.session_id, {**token(session), "type": "back"})
            lab.finalize(session.session_id, outcome="success")
            frame = next(session.recorder.frames_dir.glob("*.png"))
            frame.write_bytes(b"tampered")
            summary = validate_all(root)
            self.assertEqual(summary["rejected"], 1)

    def test_superseded_record_is_excluded_from_accepted_histogram(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            lab = service(root)
            session = lab.start_session(
                device_alias="avd-p0", task=task(), collector_id="collector-fixture"
            )
            lab.execute(session.session_id, {**token(session), "type": "back"})
            annotation = lab.finalize(session.session_id, outcome="success")
            annotation["selection_status"] = "superseded"
            annotation["superseded_reason"] = "fixture rerun"
            session.annotation_path.write_text(
                json.dumps(annotation), encoding="utf-8"
            )

            summary = validate_all(root)
            self.assertEqual(summary["accepted"], 0)
            self.assertEqual(summary["excluded"], 1)
            self.assertEqual(summary["accepted_data_class_histogram"], {})


if __name__ == "__main__":
    unittest.main()
