import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

from PIL import Image

from mobile_gui_vla_data_lab.collector import CollectionError
from mobile_gui_vla_data_lab.locking import DeviceLockError
from mobile_gui_vla_data_lab.qa import validate_all
from mobile_gui_vla_platform import Frame

from helpers import FakeAdapter, service, task, token


class ColorAdapter(FakeAdapter):
    def __init__(self):
        super().__init__()
        self.colors = [(0, 0, 0), (250, 250, 250), (80, 100, 120), (80, 100, 120)]

    def screenshot(self):
        self.capture_index += 1
        color = self.colors[min(self.capture_index - 1, len(self.colors) - 1)]
        image = Image.new("RGB", (108, 240), color)
        output = BytesIO()
        image.save(output, format="PNG")
        return Frame(
            frame_id=f"color-frame-{self.capture_index}",
            capture_wall_time=f"2026-08-28T00:00:0{self.capture_index}+00:00",
            capture_monotonic_time=float(self.capture_index),
            capture_ms=1.0,
            width_px=108,
            height_px=240,
            orientation=0,
            density_dpi=420,
            device_alias=self.alias,
            adb_endpoint="emulator-fixture",
            png_bytes=output.getvalue(),
        )


class SlowAdapter(FakeAdapter):
    def screenshot(self):
        time.sleep(0.05)
        return super().screenshot()


class CollectorTests(unittest.TestCase):
    def _wait_for_operation(self, lab, operation_id):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            operation = lab.get_operation(operation_id)
            if operation["state"] in {"COMPLETE", "ERROR"}:
                return operation
            time.sleep(0.001)
        self.fail(f"operation {operation_id} did not complete")

    def test_unrecorded_preparation_actions_are_strictly_mapped_and_not_persisted(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            adapter = FakeAdapter()
            lab = service(root, adapter)
            preview = lab.capture_preparation_preview("avd-p0")["preview"]
            result = lab.execute_preparation(
                "avd-p0",
                {
                    "type": "tap",
                    "x": 50,
                    "y": 100,
                    "viewport": {
                        "x": 0,
                        "y": 0,
                        "width": 100,
                        "height": 222.222222,
                    },
                    "preview_id": preview["preview_id"],
                    "frame_id": preview["frame_id"],
                    "frame_sha256": preview["sha256"],
                },
            )
            self.assertFalse(result["recorded"])
            self.assertEqual(adapter.calls, [("tap", 540, 1080)])
            self.assertFalse((root / "raw").exists())
            self.assertFalse((root / "annotations").exists())

    def test_back_to_back_preview_requests_reuse_the_latest_exact_frame(self):
        with tempfile.TemporaryDirectory() as root_value:
            adapter = FakeAdapter()
            lab = service(Path(root_value), adapter)
            first = lab.capture_preparation_preview("avd-p0")["preview"]
            captures = adapter.capture_index
            second = lab.capture_preparation_preview("avd-p0")["preview"]
            self.assertEqual(second["preview_id"], first["preview_id"])
            self.assertEqual(adapter.capture_index, captures)

    def test_concurrent_preview_requests_coalesce_onto_one_capture(self):
        with tempfile.TemporaryDirectory() as root_value:
            adapter = SlowAdapter()
            lab = service(Path(root_value), adapter)
            barrier = threading.Barrier(4)

            def capture(_):
                barrier.wait()
                return lab.capture_preparation_preview("avd-p0")["preview"]

            with ThreadPoolExecutor(max_workers=4) as pool:
                previews = list(pool.map(capture, range(4)))
            self.assertEqual(adapter.capture_index, 1)
            self.assertEqual(len({value["preview_id"] for value in previews}), 1)

    def test_async_preparation_returns_immediate_feedback_without_raw_data(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            lab = service(root)
            preview = lab.capture_preparation_preview("avd-p0")["preview"]
            started = lab.start_preparation_execute(
                "avd-p0",
                {
                    "preview_id": preview["preview_id"],
                    "frame_id": preview["frame_id"],
                    "frame_sha256": preview["sha256"],
                    "type": "wait",
                    "duration_ms": 0,
                },
            )
            operation = self._wait_for_operation(lab, started["operation_id"])
            self.assertEqual(operation["state"], "COMPLETE")
            self.assertFalse(operation["result"]["recorded"])
            self.assertEqual(
                operation["result"]["capture_policy"],
                "unrecorded_immediate_feedback",
            )
            self.assertFalse((root / "raw").exists())

    def test_unrecorded_preparation_cannot_race_an_active_session(self):
        with tempfile.TemporaryDirectory() as root_value:
            lab = service(Path(root_value))
            session = lab.start_session(
                device_alias="avd-p0",
                task=task(),
                collector_id="collector-fixture",
            )
            with self.assertRaises(DeviceLockError):
                lab.capture_preparation_preview("avd-p0")
            lab.finalize(session.session_id, outcome="aborted")

    def test_all_human_actions_record_source_action_next(self):
        with tempfile.TemporaryDirectory() as root:
            adapter = FakeAdapter()
            lab = service(Path(root), adapter)
            session = lab.start_session(
                device_alias="avd-p0",
                task=task(),
                collector_id="collector-fixture",
            )
            requests = [
                {"type": "tap", "x": 50, "y": 100, "viewport": {"x": 0, "y": 0, "width": 100, "height": 222.222222}},
                {"type": "swipe", "x0": 50, "y0": 180, "x1": 50, "y1": 50, "duration_ms": 300, "viewport": {"x": 0, "y": 0, "width": 100, "height": 222.222222}},
                {"type": "type", "text": "synthetic value"},
                {"type": "back"},
                {"type": "home"},
                {"type": "wait", "duration_ms": 0},
            ]
            for request in requests:
                lab.execute(session.session_id, {**token(session), **request})
            record = lab.finalize(session.session_id, outcome="success")
            self.assertTrue(record["training_eligible"])
            lines = session.recorder.steps_path.read_text().splitlines()
            self.assertEqual(len(lines), 6)
            for line in lines:
                step = json.loads(line)
                self.assertIn("frame_t", step)
                self.assertIn("canonical_action", step)
                self.assertIn("frame_t_plus_1", step)
            self.assertEqual(
                [call[0] for call in adapter.calls],
                ["tap", "swipe", "type", "back", "home"],
            )

    def test_async_recorded_action_closes_stable_transition_before_next_action(self):
        with tempfile.TemporaryDirectory() as root_value:
            lab = service(Path(root_value))
            session = lab.start_session(
                device_alias="avd-p0",
                task=task(),
                collector_id="collector-fixture",
            )
            started = lab.start_execute(
                session.session_id,
                {**token(session), "type": "wait", "duration_ms": 100},
            )
            with self.assertRaises(CollectionError):
                lab.start_execute(
                    session.session_id, {**token(session), "type": "home"}
                )
            operation = self._wait_for_operation(lab, started["operation_id"])
            self.assertEqual(operation["state"], "COMPLETE")
            self.assertTrue(operation["result"]["transition"]["stable_capture"]["stable"])
            self.assertEqual(operation["result"]["session"]["step_count"], 1)
            self.assertIsNone(operation["result"]["session"]["pending_operation_id"])
            lab.finalize(session.session_id, outcome="success")

    def test_stale_frame_is_rejected_before_dispatch(self):
        with tempfile.TemporaryDirectory() as root:
            adapter = FakeAdapter()
            lab = service(Path(root), adapter)
            session = lab.start_session(
                device_alias="avd-p0", task=task(), collector_id="collector-fixture"
            )
            with self.assertRaises(CollectionError):
                lab.execute(
                    session.session_id,
                    {"type": "back", "frame_id": "old", "frame_sha256": "old"},
                )
            self.assertEqual(adapter.calls, [])
            lab.finalize(session.session_id, outcome="aborted")

    def test_live_preview_is_the_exact_recorded_action_source(self):
        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            lab = service(root)
            session = lab.start_session(
                device_alias="avd-p0", task=task(), collector_id="human-p1"
            )
            time.sleep(lab.preview_min_interval_seconds + 0.001)
            preview = lab.capture_preview(session.session_id)["preview"]
            result = lab.execute(
                session.session_id,
                {
                    "type": "back",
                    "preview_id": preview["preview_id"],
                    "frame_id": preview["frame_id"],
                    "frame_sha256": preview["sha256"],
                },
            )
            transition = result["transition"]
            self.assertEqual(transition["frame_t"]["frame_id"], preview["frame_id"])
            self.assertEqual(
                transition["observation_refresh"]["selected_preview"]["preview_id"],
                preview["preview_id"],
            )

            later = lab.capture_preview(session.session_id)["preview"]
            lab.execute(
                session.session_id,
                {
                    "type": "home",
                    "preview_id": later["preview_id"],
                    "frame_id": later["frame_id"],
                    "frame_sha256": later["sha256"],
                },
            )
            lab.finalize(session.session_id, outcome="success")
            summary = validate_all(root)
            self.assertEqual(summary["accepted"], 1)

    def test_post_action_capture_skips_transient_until_visually_stable(self):
        from mobile_gui_vla_data_lab.collector import CollectionService

        with tempfile.TemporaryDirectory() as root_value:
            adapter = ColorAdapter()
            lab = CollectionService(
                artifact_root=Path(root_value),
                device_factories={"avd-p0": lambda: adapter},
                platform_dependency={"base_commit": "base", "dependency_commit": "dep"},
                post_action_settle_seconds=0,
                stable_capture_max_samples=5,
                stable_capture_interval_seconds=0,
            )
            session = lab.start_session(
                device_alias="avd-p0", task=task(), collector_id="human-p1"
            )
            result = lab.execute(session.session_id, {**token(session), "type": "back"})
            self.assertTrue(result["transition"]["stable_capture"]["stable"])
            self.assertEqual(result["transition"]["stable_capture"]["sample_count"], 3)
            self.assertEqual(
                result["transition"]["frame_t_plus_1"]["frame_id"],
                "color-frame-4",
            )
            lab.finalize(session.session_id, outcome="success")

    def test_zero_step_abort_is_never_training_eligible(self):
        with tempfile.TemporaryDirectory() as root_value:
            lab = service(Path(root_value))
            session = lab.start_session(
                device_alias="avd-p0", task=task(), collector_id="human-p1"
            )
            record = lab.finalize(session.session_id, outcome="aborted")
            self.assertFalse(record["training_eligible"])

    def test_unstable_next_frame_is_rejected_by_reload_qa(self):
        from mobile_gui_vla_data_lab.collector import CollectionService

        with tempfile.TemporaryDirectory() as root_value:
            root = Path(root_value)
            adapter = ColorAdapter()
            adapter.colors = [(0, 0, 0), (30, 30, 30), (120, 120, 120), (240, 240, 240)]
            lab = CollectionService(
                artifact_root=root,
                device_factories={"avd-p0": lambda: adapter},
                platform_dependency={"base_commit": "base", "dependency_commit": "dep"},
                post_action_settle_seconds=0,
                stable_capture_max_samples=3,
                stable_capture_interval_seconds=0,
            )
            session = lab.start_session(
                device_alias="avd-p0", task=task(), collector_id="human-p1"
            )
            result = lab.execute(session.session_id, {**token(session), "type": "back"})
            self.assertFalse(result["transition"]["stable_capture"]["stable"])
            lab.finalize(session.session_id, outcome="success")
            self.assertEqual(validate_all(root)["rejected"], 1)

    def test_preventive_and_post_error_fixtures_preserve_causality(self):
        with tempfile.TemporaryDirectory() as root:
            lab = service(Path(root))
            preventive = lab.start_session(
                device_alias="avd-p0",
                task=task("preventive", "recovery"),
                collector_id="collector-fixture",
                collection_mode="scripted_schema_test",
            )
            result = lab.execute(
                preventive.session_id,
                {
                    **token(preventive),
                    "type": "back",
                    "actor_source": "scripted_fixture",
                    "actor_role": "human",
                    "intervention": {
                        "kind": "preventive_override",
                        "reason": "wrong_action",
                    },
                    "model_proposal": {
                        "source": "scripted_fixture",
                        "structured_action": {"type": "tap", "x_px": 1, "y_px": 1},
                        "executed": False,
                    },
                },
            )
            self.assertFalse(result["transition"]["model_proposal"]["executed"])
            self.assertFalse(lab.finalize(preventive.session_id, outcome="success")["training_eligible"])

            post = lab.start_session(
                device_alias="avd-p0",
                task=task("post", "recovery"),
                collector_id="collector-fixture",
                collection_mode="scripted_schema_test",
            )
            lab.execute(
                post.session_id,
                {
                    **token(post),
                    "type": "back",
                    "actor_source": "scripted_fixture",
                    "actor_role": "model",
                },
            )
            result = lab.execute(
                post.session_id,
                {
                    **token(post),
                    "type": "home",
                    "actor_source": "scripted_fixture",
                    "actor_role": "human",
                    "intervention": {
                        "kind": "post_error_takeover",
                        "reason": "wrong_action",
                        "trigger_step_index": 0,
                    },
                },
            )
            self.assertEqual(result["transition"]["human_intervention"]["trigger_step_index"], 0)
            lab.finalize(post.session_id, outcome="success")

    def test_natural_model_collection_remains_closed(self):
        with tempfile.TemporaryDirectory() as root:
            lab = service(Path(root))
            with self.assertRaises(CollectionError):
                lab.start_session(
                    device_alias="avd-p0",
                    task=task(),
                    collector_id="collector-fixture",
                    collection_mode="model_with_human_intervention",
                )


if __name__ == "__main__":
    unittest.main()
