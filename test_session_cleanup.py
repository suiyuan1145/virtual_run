"""Owned records finish before app shutdown; the emulator remains running."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import morning_run as m


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        m.STOP.clear()
        self.addCleanup(m.STOP.clear)

    def runner(self):
        runner = Mock()
        runner.c = {'auto_origin': False}
        runner.serial = 'emulator-5554'
        runner.started = False
        runner.last_position = None
        runner.preflight.return_value = (31.23, 121.47)
        runner.start.side_effect = lambda: setattr(runner, 'started', True)
        runner.finish.side_effect = lambda: setattr(runner, 'started', False)
        return runner

    def execute(self, runner):
        state = {'status': 'preparing'}
        with patch.object(m.ctypes, 'windll', SimpleNamespace(kernel32=Mock()), create=True):
            code = m.execute_session(runner, self.directory / 'result.json', state, trial_seconds=60)
        return code, state

    def test_capture_result_before_closing_app_and_persist_confirmation(self):
        runner = self.runner()
        def close():
            saved = json.loads((self.directory / 'result.json').read_text(encoding='utf-8'))
            self.assertTrue(saved['finish_confirmed'])
            self.assertFalse(saved['app_closed'])
        runner.close_app.side_effect = close
        code, state = self.execute(runner)
        self.assertEqual(code, 0)
        self.assertTrue(state['app_closed'])
        names = [entry[0] for entry in runner.method_calls]
        self.assertLess(names.index('finish'), names.index('capture'))
        self.assertLess(names.index('capture'), names.index('close_app'))
        runner.close_app.assert_called_once_with()
        runner.close_emulator.assert_not_called()

    def test_unconfirmed_finish_keeps_app_open(self):
        runner = self.runner()
        runner.finish.side_effect = RuntimeError('finish uncertain')
        code, state = self.execute(runner)
        self.assertEqual(code, 1)
        self.assertFalse(state['finish_confirmed'])
        runner.capture.assert_called_once()
        runner.close_app.assert_not_called()

    def test_failure_before_start_does_not_close_existing_app(self):
        runner = self.runner()
        runner.preflight.side_effect = RuntimeError('existing run')
        code, state = self.execute(runner)
        self.assertEqual(code, 1)
        runner.finish.assert_not_called()
        runner.close_app.assert_not_called()

    def test_cancelled_owned_run_closes_only_after_confirmed_finish(self):
        runner = self.runner()
        runner.run_route.side_effect = lambda *_: m.STOP.set()
        code, state = self.execute(runner)
        self.assertEqual(code, 0)
        self.assertEqual(state['status'], 'cancelled')
        self.assertTrue(state['finish_confirmed'])
        self.assertTrue(state['app_closed'])

    def test_close_failure_is_reported_without_restarting_record(self):
        runner = self.runner()
        runner.close_app.side_effect = RuntimeError('close uncertain')
        code, state = self.execute(runner)
        self.assertEqual(code, 1)
        self.assertTrue(state['finish_confirmed'])
        self.assertFalse(state['app_closed'])
        self.assertEqual(state['close_error'], 'close uncertain')
        self.assertFalse(m.retryable_daily_state(state))
        runner.start.assert_called_once()

    def test_close_app_targets_only_anxiaoxin_without_emulator_commands(self):
        runner = m.Runner({}, self.directory)
        runner.serial = 'emulator-5554'
        with patch.object(runner, 'call', side_effect=['WebActivity', '', 'USER PID NAME\nu0_a1 123 com.other.app']) as adb:
            runner.close_app()
        self.assertEqual(adb.call_args_list, [
            call(['shell', 'dumpsys', 'activity', 'activities']),
            call(['shell', 'am', 'force-stop', m.PACKAGE]),
            call(['shell', 'ps', '-A'])])

    def test_open_native_record_or_phone_blocks_force_stop(self):
        for serial, started, activity in [('emulator-5554', True, ''),
                                          ('emulator-5554', False, 'background ' + m.SPORT),
                                          ('phone-123', False, '')]:
            with self.subTest(serial=serial, started=started, activity=activity):
                runner = m.Runner({}, self.directory)
                runner.serial, runner.started = serial, started
                with patch.object(runner, 'call', return_value=activity) as adb:
                    with self.assertRaises(RuntimeError):
                        runner.close_app()
                self.assertNotIn(call(['shell', 'am', 'force-stop', m.PACKAGE]), adb.call_args_list)

    def test_surviving_secondary_process_is_not_reported_closed(self):
        runner = m.Runner({}, self.directory)
        runner.serial = 'emulator-5554'
        with patch.object(runner, 'call', side_effect=['WebActivity', '',
                         *['u0_a1 123 ' + m.PACKAGE + ':remote'] * 3]), patch.object(m.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, '未确认关闭'):
                runner.close_app()


if __name__ == '__main__':
    unittest.main()
