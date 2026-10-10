import tempfile
import threading
from pathlib import Path
import unittest
from unittest.mock import patch
import stop_support as s


class StopTests(unittest.TestCase):
    def test_persistent_disable_cancels_background_runner_and_watcher_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            event = threading.Event()
            with s.StopWatcher(directory, event) as watcher:
                s.request_disable(directory)
                self.assertTrue(event.wait(2))
            self.assertFalse(watcher.thread.is_alive())
            self.assertTrue(s.disabled(directory))
            s.resume(directory)
            self.assertFalse(s.disabled(directory))
            self.assertFalse((Path(directory) / 'stop-all.request').exists())

    def test_private_cancel_and_normal_return_leave_no_watcher_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            request = Path(directory) / 'private.request'
            event = threading.Event()
            with s.StopWatcher(directory, event, request) as watcher:
                request.touch()
                self.assertTrue(event.wait(2))
            self.assertFalse(watcher.thread.is_alive())
            event.clear()
            request.unlink()
            with s.StopWatcher(directory, event) as watcher:
                pass
            self.assertFalse(watcher.thread.is_alive())

    def test_waiting_for_locked_runner_does_not_force_stop_it(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('msvcrt.locking', side_effect=OSError('owned runner busy')):
                with self.assertRaisesRegex(RuntimeError, '未强制终止'):
                    s.wait_until_idle(directory, timeout=0)


if __name__ == '__main__':
    unittest.main()
