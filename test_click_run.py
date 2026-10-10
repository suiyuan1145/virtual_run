from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import click_run_test as test


class ClickTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runner = Mock()
        self.runner.directory = Path(self.temp.name)
        self.runner.started = False
        test.morning.STOP.clear()

    def test_existing_record_rejected_without_click_or_finish(self):
        self.runner.preflight.side_effect = RuntimeError('已有记录')
        result = test.exercise(self.runner)
        self.assertEqual(result['status'], 'failed')
        self.runner.start.assert_not_called()
        self.runner.finish.assert_not_called()

    def test_start_timeout_still_finishes_owned_record(self):
        def start():
            self.runner.started = True
            raise RuntimeError('ADB timeout after start')
        def finish():
            self.runner.started = False
        self.runner.start.side_effect = start
        self.runner.finish.side_effect = finish
        result = test.exercise(self.runner)
        self.runner.finish.assert_called_once()
        self.assertTrue(result['finish_confirmed'])
        self.assertEqual(result['status'], 'failed')

    def test_readonly_check_never_navigates_or_starts(self):
        result = test.exercise(self.runner, check=True)
        self.assertEqual(result['status'], 'check_passed')
        self.runner.navigate_to_start.assert_not_called()
        self.runner.start.assert_not_called()
        self.runner.capture.assert_not_called()

    def test_static_fix_continues_when_native_start_opens_gps_listener(self):
        runner = test.ClickTestRunner({}, Path(self.temp.name))
        runner.serial = 'emulator-5554'
        runner.last_position = (31, 121)
        items = [test.morning.nodes('<hierarchy><node text="开始运动" enabled="true" bounds="[1,1][3,3]"/></hierarchy>'),
                 test.morning.nodes('<hierarchy><node resource-id="cn.edu.aiit.axx:id/sport_run_ll_ll" enabled="true"/></hierarchy>')]
        with patch.object(runner, 'ui', side_effect=items), patch.object(runner, 'tap') as tap, \
                patch.object(test.morning, 'send_location') as send, \
                patch.object(test.morning, 'current_location', return_value=(31, 121)):
            runner.start()
        self.assertTrue(runner.started)
        tap.assert_called_once()
        send.assert_called_once_with('emulator-5554', 31, 121, 0)


if __name__ == '__main__':
    unittest.main()
