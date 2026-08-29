import subprocess
import unittest

from mobile_gui_vla_platform.adb import ADBDeviceAdapter
from mobile_gui_vla_platform.contracts import HomeAction, TypeAction


class TypeHomeFocusedTests(unittest.TestCase):
    def test_type_and_home_contracts_validate(self):
        action = TypeAction("synthetic value")
        action.validate()
        self.assertEqual(
            action.to_dict(), {"type": "type", "text": "synthetic value"}
        )
        self.assertEqual(HomeAction().to_dict(), {"type": "key", "key": "HOME"})
        for invalid in ("", "line\nbreak", "非ASCII"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                TypeAction(invalid).validate()

    def test_adb_adapter_dispatches_type_and_home(self):
        calls = []

        def runner(command, timeout, text):
            calls.append(list(command))
            return subprocess.CompletedProcess(command, 0, "", "")

        adapter = ADBDeviceAdapter(
            "fixture-serial", alias="fixture-device", runner=runner
        )
        self.assertGreaterEqual(adapter.type_text("synthetic value"), 0)
        self.assertGreaterEqual(adapter.home(), 0)
        self.assertEqual(
            calls,
            [
                [
                    "adb",
                    "-s",
                    "fixture-serial",
                    "shell",
                    "input",
                    "text",
                    "synthetic%svalue",
                ],
                [
                    "adb",
                    "-s",
                    "fixture-serial",
                    "shell",
                    "input",
                    "keyevent",
                    "HOME",
                ],
            ],
        )


if __name__ == "__main__":
    unittest.main()
