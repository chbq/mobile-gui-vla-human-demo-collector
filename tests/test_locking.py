import tempfile
import unittest
from pathlib import Path

from mobile_gui_vla_data_lab.locking import DeviceLockError, DeviceSessionLock


class LockingTests(unittest.TestCase):
    def test_only_one_active_writer_per_device(self):
        with tempfile.TemporaryDirectory() as root:
            first = DeviceSessionLock(Path(root), "avd-p0", {"session": "one"}).acquire()
            second = DeviceSessionLock(Path(root), "avd-p0", {"session": "two"})
            with self.assertRaises(DeviceLockError):
                second.acquire()
            first.release()
            second.acquire()
            second.release()

    def test_alias_cannot_escape_lock_root(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                DeviceSessionLock(Path(root), "../unsafe", {})


if __name__ == "__main__":
    unittest.main()
