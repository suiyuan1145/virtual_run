"""Automatic-origin runs must own cleanup and leave daily settings untouched."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import morning_run as m
import morning_support as support


class OnDemandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = json.loads(Path('tests/fixtures/morning-config.json').read_text(encoding='utf-8'))
        self.config.update(auto_origin=True, anchor_wgs84=None, headless=True)
        m.STOP.clear()

    def test_config_without_coordinates_preserves_daily_config_and_credentials(self):
        daily = self.directory / 'morning'
        daily.mkdir()
        saved = b'{"auto_login":true,"speed_kmh":8,"anchor_wgs84":[31,121]}'
        (daily / '晨跑配置.json').write_bytes(saved)
        (daily / 'daily-state.json').write_bytes(b'previous daily result')
        (daily / 'login.dat').write_bytes(b'encrypted credentials')
        with patch.object(support, 'DATA_DIR', self.directory), \
                patch.object(support, 'runtime_python', return_value=Path(sys.executable)):
            path = support.configure_test('C:/SDK', 'VirtualRun_API30', 12, 150, True)
        config = json.loads(path.read_text(encoding='utf-8'))
        self.assertIsNone(config['anchor_wgs84'])
        self.assertTrue(config['headless'])
        self.assertTrue(config['auto_login'])
        self.assertEqual((daily / '晨跑配置.json').read_bytes(), saved)
        self.assertEqual((daily / 'daily-state.json').read_bytes(), b'previous daily result')
        self.assertEqual((daily / 'login.dat').read_bytes(), b'encrypted credentials')

    def test_preflight_without_fix_does_not_guess_or_inject_location(self):
        runner = m.Runner(self.config, self.directory)
        runner.serial = 'emulator-5554'
        def call(args, **kwargs):
            if 'package' in args:
                return 'versionName=2.0.5\nprimaryCpuAbi=x86\n'
            return '3' if 'settings' in args else ''
        with patch.object(runner, 'call', side_effect=call), \
                patch.object(m, 'current_location', side_effect=RuntimeError('no GPS')), \
                patch.object(m, 'send_location') as send:
            self.assertIsNone(runner.preflight())
        send.assert_not_called()

    def test_open_record_in_background_is_rejected_before_navigation(self):
        runner = m.Runner(self.config, self.directory)
        runner.serial = 'emulator-5554'
        with patch.object(runner, 'activity', return_value='HomeActivity'), \
                patch.object(runner, 'call', return_value='mResumedActivity: ActivityRecord{abc u0 cn.edu.aiit.axx/.activity.SportMapActivity t67}'), \
                patch.object(runner, 'tap') as tap:
            with self.assertRaisesRegex(RuntimeError, '旧记录'):
                runner.navigate_to_start()
        tap.assert_not_called()

    def test_existing_start_page_is_reused_without_reopening_splash(self):
        runner = m.Runner(self.config, self.directory)
        runner.serial = 'emulator-5554'
        with patch.object(runner, 'activity', return_value='WebActivity'), \
                patch.object(runner, 'call', return_value='') as call, \
                patch.object(runner, 'ui', return_value=m.nodes('<hierarchy><node text="开始运动"/></hierarchy>')):
            runner.navigate_to_start()
        self.assertFalse(any('am' in c.args[0] for c in call.call_args_list))

    def runner(self, missing_fix=False):
        runner = Mock()
        runner.c = self.config
        runner.serial = 'emulator-5554'
        runner.started = False
        runner.last_position = None
        origin = (31.23, 121.47)
        runner.preflight.return_value = None if missing_fix else origin
        runner.read_auto_origin.return_value = origin
        runner.start.side_effect = lambda: setattr(runner, 'started', True)
        runner.prepare.side_effect = lambda p: setattr(runner, 'last_position', p)
        runner.finish.side_effect = lambda: setattr(runner, 'started', False)
        return runner

    def execute(self, runner):
        state = {'mode': 'trial', 'status': 'preparing'}
        with patch.object(m.ctypes, 'windll', SimpleNamespace(kernel32=Mock()), create=True), \
                patch.object(m, 'send_location'):
            code = m.execute_session(runner, self.directory / 'trial-result.json', state, trial_seconds=900)
        return code, state

    def test_missing_fix_starts_listener_then_reads_before_first_injection(self):
        runner = self.runner(missing_fix=True)
        code, state = self.execute(runner)
        self.assertEqual(code, 0)
        names = [c[0] for c in runner.method_calls]
        self.assertLess(names.index('start'), names.index('read_auto_origin'))
        self.assertLess(names.index('read_auto_origin'), names.index('prepare'))
        runner.start.assert_called_once()
        self.assertTrue(state['finish_confirmed'])

    def test_fix_failure_ends_only_its_new_record_without_sending_guessed_coordinates(self):
        runner = self.runner(missing_fix=True)
        runner.read_auto_origin.side_effect = RuntimeError('no GPS')
        code, state = self.execute(runner)
        self.assertEqual(code, 1)
        self.assertTrue(state['start_confirmed'])
        self.assertTrue(state['finish_confirmed'])
        runner.finish.assert_called_once()
        runner.prepare.assert_not_called()
        runner.run_route.assert_not_called()

    def test_current_fix_prepares_before_clicking_start(self):
        runner = self.runner()
        code, state = self.execute(runner)
        self.assertEqual(code, 0)
        names = [c[0] for c in runner.method_calls]
        self.assertLess(names.index('prepare'), names.index('start'))
        self.assertTrue(state['finish_confirmed'])

    def test_cli_allows_long_on_demand_without_coordinates_but_not_scheduled(self):
        config = self.directory / 'test.json'
        config.write_text(json.dumps(self.config), encoding='utf-8')
        with patch.object(m, 'run_mode', return_value=0) as run:
            self.assertEqual(m.main(['--config', str(config), '--test-seconds', '900']), 0)
        self.assertTrue(run.call_args.args[2]['auto_origin'])
        with self.assertRaises((TypeError, ValueError)):
            m.main(['--config', str(config), '--scheduled'])

    def test_on_demand_uses_daily_lock_and_leaves_daily_state_unchanged(self):
        daily = self.directory / 'daily-state.json'
        daily.write_bytes(b'previous daily state')
        runner = self.runner()
        args = SimpleNamespace(check=False, scheduled=False, trial_seconds=None, test_seconds=900)
        with patch.object(m, 'execute_session', return_value=0) as execute:
            code = m.run_mode(args, self.directory / '即时测试配置.json', self.config, self.directory, runner)
        self.assertEqual(code, 0)
        self.assertEqual(execute.call_args.args[1], self.directory / 'trial-result.json')
        self.assertEqual(execute.call_args.kwargs['trial_seconds'], 900)
        self.assertEqual(daily.read_bytes(), b'previous daily state')
        self.assertTrue((self.directory / 'morning.lock').is_file())


if __name__ == '__main__':
    unittest.main()
