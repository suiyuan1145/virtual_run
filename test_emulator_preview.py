from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import emulator_preview as p


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.adb = Path(self.temp.name) / 'adb.exe'
        self.adb.write_bytes(b'fake')

    def test_capture_uses_configured_avd_only_and_never_sends_input(self):
        def run(command, **kwargs):
            if command[1:] == ['devices']:
                output = b'List of devices attached\nphone123 device\nemulator-5554 device\nemulator-5556 device\n'
            elif command[-3:] == ['emu', 'avd', 'name']:
                output = b'Other\nOK\n' if 'emulator-5554' in command else b'VirtualRun_API30\nOK\n'
            else:
                self.assertEqual(command[1:], ['-s', 'emulator-5556', 'exec-out', 'screencap', '-p'])
                output = b'\x89PNG\r\n\x1a\npixels'
            return SimpleNamespace(returncode=0, stdout=output, stderr=b'')
        with patch.object(p.subprocess, 'run', side_effect=run) as calls:
            result = p.capture_screen(self.adb, 'VirtualRun_API30')
        self.assertTrue(result.startswith(b'\x89PNG'))
        self.assertFalse(any('phone123' in c.args[0] or 'input' in c.args[0] or 'geo' in c.args[0] for c in calls.call_args_list))

    def test_missing_target_does_not_start_or_restart_emulator(self):
        with patch.object(p.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=b'phone device\n')) as calls:
            with self.assertRaisesRegex(RuntimeError, '不会启动或重启'):
                p.capture_screen(self.adb, 'VirtualRun_API30')
        self.assertEqual(calls.call_count, 1)

    def test_invalid_screen_data_and_missing_sdk_have_clear_errors(self):
        results = [b'emulator-5554 device\n', b'VirtualRun_API30\nOK', b'not a screenshot']
        with patch.object(p.subprocess, 'run', side_effect=[SimpleNamespace(returncode=0, stdout=v) for v in results]):
            with self.assertRaisesRegex(RuntimeError, '有效画面'):
                p.capture_screen(self.adb, 'VirtualRun_API30')
        with self.assertRaisesRegex(RuntimeError, '没有找到 ADB'):
            p.capture_screen(Path(self.temp.name) / 'missing', 'VirtualRun_API30')


if __name__ == '__main__':
    unittest.main()
