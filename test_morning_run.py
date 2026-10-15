import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace
from datetime import datetime, timedelta
import morning_run as m


def element(text='', rid='', bounds='[10,20][110,120]'):
    return m.nodes(f'<hierarchy><node text="{text}" resource-id="cn.edu.aiit.axx:id/{rid}" bounds="{bounds}" enabled="true" /></hierarchy>')[0]


class MorningTests(unittest.TestCase):
    def test_navigation_clicks_parent_not_text_label(self):
        items = m.nodes('<hierarchy><node clickable="true" bounds="[0,100][200,300]">'
                        '<node text="智慧运动" clickable="false" bounds="[0,250][200,300]" />'
                        '</node></hierarchy>')
        with patch.object(self.r, 'ui', side_effect=[items, [element(text='开始运动')]]), \
             patch.object(self.r, 'call', return_value='Starting'), patch.object(self.r, 'tap') as tap, \
             patch.object(m.STOP, 'wait', return_value=False):
            self.r.navigate_to_start()
        self.assertEqual(tap.call_args.args[0].get('bounds'), '[0,100][200,300]')

    def test_navigation_stops_repeating_an_unresponsive_entry(self):
        items = m.nodes('<hierarchy><node clickable="true" bounds="[0,100][200,300]">'
                        '<node text="智慧运动" clickable="false" bounds="[0,250][200,300]" />'
                        '</node></hierarchy>')
        with patch.object(self.r, 'ui', return_value=items), patch.object(self.r, 'call', return_value='Starting'), \
             patch.object(self.r, 'tap') as tap, patch.object(m.STOP, 'wait', return_value=False), \
             patch.object(m.time, 'monotonic', side_effect=range(0, 300, 2)):
            with self.assertRaises(m.NavigationTimeout):
                self.r.navigate_to_start()
        self.assertEqual(tap.call_count, 2)

    def test_web_title_is_not_a_clickable_entry(self):
        self.assertIsNone(m.clickable_entry([element(text='智慧运动')], '智慧运动'))

    def test_cancellation_before_start_never_creates_record_and_runs_cleanup(self):
        runner = Mock()
        runner.serial = 'emulator-5554'
        runner.last_position = None
        runner.started = False
        def cancelled():
            m.STOP.set()
            raise RuntimeError('用户停止了任务。')
        runner.navigate_to_start.side_effect = cancelled
        state = {'mode': 'trial', 'status': 'preparing'}
        with patch.object(m.ctypes, 'windll', SimpleNamespace(kernel32=Mock()), create=True):
            code = m.execute_session(runner, Path(self.temp.name) / 'cancelled.json', state, trial_seconds=60)
        self.assertEqual(code, 0)
        self.assertEqual(state['status'], 'cancelled')
        runner.start.assert_not_called()
        runner.capture.assert_called_once()
        m.STOP.clear()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = json.loads(Path('tests/fixtures/morning-config.json').read_text(encoding='utf-8'))
        self.r = m.Runner(self.config, Path(self.temp.name))
        self.r.serial = 'emulator-5554'
        m.STOP.clear()

    def test_deadline_uses_beijing_time_and_rejects_overnight(self):
        start, end = m.window(self.config, datetime(2026, 10, 9, 6, 55, tzinfo=m.BEIJING))
        self.assertEqual((start.hour, end.hour), (7, 8))
        self.assertEqual((end - start).total_seconds(), 3600)
        self.assertAlmostEqual(12 / 3.6 * 3600, 12000)
        with self.assertRaises(ValueError):
            m.window(dict(self.config, start='23:00', end='01:00'), start)

    def test_shutdown_only_closes_selected_avd_and_rejects_open_run(self):
        with patch.object(self.r, 'select_emulator') as select, \
             patch.object(self.r, 'call', return_value='HomeActivity') as call:
            self.r.close_emulator(Path(self.temp.name) / 'morning.lock')
        select.assert_called_once_with(start=False)
        self.assertEqual(call.call_args.args[0], ['emu', 'kill'])
        with patch.object(self.r, 'select_emulator'), \
             patch.object(self.r, 'call', return_value='mResumedActivity: ActivityRecord{abc u0 cn.edu.aiit.axx/.activity.SportMapActivity t67}') as call:
            with self.assertRaisesRegex(RuntimeError, '结束记录'):
                self.r.close_emulator(Path(self.temp.name) / 'morning.lock')
        self.assertNotIn(unittest.mock.call(['emu', 'kill']), call.call_args_list)

    def test_shutdown_cannot_interrupt_locked_morning_task(self):
        with patch('msvcrt.locking', side_effect=OSError('locked')), \
             patch.object(self.r, 'select_emulator') as select:
            with self.assertRaisesRegex(RuntimeError, '任务仍在运行'):
                self.r.close_emulator(Path(self.temp.name) / 'morning.lock')
        select.assert_not_called()

    def test_only_target_avd_selected_and_usb_never_selected(self):
        def shell(args, serial=None, **kw):
            if args == ['devices']:
                return 'List of devices attached\nphone123\tdevice\nemulator-5554\tdevice\nemulator-5556\tdevice'
            if args[:3] == ['emu', 'avd', 'name']:
                return ('Other' if serial == 'emulator-5554' else 'VirtualRun_API30') + '\nOK'
            return '1'
        with patch.object(m, 'adb', side_effect=shell):
            self.r.select_emulator()
        self.assertEqual(self.r.serial, 'emulator-5556')
        with self.assertRaises(RuntimeError):
            m.send_location('phone123', 31, 121)

    def test_does_not_take_over_existing_run_or_android_mock_provider(self):
        package = 'versionName=2.0.5\nprimaryCpuAbi=x86\n'
        def shell(args, **kw):
            return package if 'package' in args else '3' if 'settings' in args else 'gps provider [mock]:'
        with patch.object(self.r, 'call', side_effect=shell), patch.object(self.r, 'activity', return_value='mResumedActivity: ActivityRecord{abc u0 cn.edu.aiit.axx/.activity.SportMapActivity t67}'):
            with self.assertRaisesRegex(RuntimeError, '旧记录'):
                self.r.preflight()
        with patch.object(self.r, 'call', side_effect=shell), patch.object(self.r, 'activity', return_value='WebActivity'):
            with self.assertRaisesRegex(RuntimeError, '占用'):
                self.r.preflight()

    def test_finish_pauses_holds_end_and_confirms_result(self):
        steps = [[element(rid='sport_run_ll_ll')], [element(rid='sport_stop_ll_completedview')], [element(text='结束运动')]]
        self.r.started = True
        with patch.object(self.r, 'ui', side_effect=steps), patch.object(self.r, 'tap') as tap, patch.object(m.time, 'sleep'):
            self.r.finish()
        self.assertFalse(self.r.started)
        self.assertEqual(tap.call_args_list[1].kwargs, {'hold_ms': 4500})

    def test_short_record_uses_end_text_not_continue_button(self):
        items = [element(text='本次运动时间或距离过短，无法保存记录，确认结束吗？'),
                 element(text='结束', rid='sport_finish_dialog_neg_tv'),
                 element(text='继续运动', rid='sport_finish_dialog_pos_tv')]
        self.r.started = True
        with patch.object(self.r, 'ui', side_effect=[items, []]), patch.object(self.r, 'tap') as tap, \
             patch.object(self.r, 'activity', return_value='WebActivity'), patch.object(m.time, 'sleep'):
            self.r.finish()
        self.assertEqual(tap.call_args.args[0].get('text'), '结束')

    def test_start_failure_still_marks_record_for_cleanup(self):
        with patch.object(self.r, 'ui', return_value=[element(text='开始运动')]), \
             patch.object(self.r, 'tap', side_effect=RuntimeError('ADB timeout')):
            with self.assertRaises(RuntimeError):
                self.r.start()
        self.assertTrue(self.r.started)

    def test_first_run_battery_prompt_does_not_block_start_or_cleanup(self):
        deny = element(text='Deny')
        deny.set('resource-id', 'android:id/button2')
        prompt_activity = 'com.android.settings/.fuelgauge.RequestIgnoreBatteryOptimizations'
        with patch.object(self.r, 'ui', side_effect=[[element(text='开始运动')], [deny],
                                                  [element(rid='sport_run_ll_ll')]]), \
             patch.object(self.r, 'activity', return_value=prompt_activity), patch.object(self.r, 'tap') as tap:
            self.r.start()
        self.assertEqual(tap.call_args_list[1].args[0].get('resource-id'), 'android:id/button2')
        with patch.object(self.r, 'ui', side_effect=[[deny], [element(text='结束运动')]]), \
             patch.object(self.r, 'activity', return_value=prompt_activity), \
             patch.object(self.r, 'tap') as tap, patch.object(m.time, 'sleep'):
            self.r.finish()
        tap.assert_called_once_with(deny)
        self.assertFalse(self.r.started)

    def test_battery_handler_does_not_dismiss_other_dialogs(self):
        deny = element(text='取消')
        deny.set('resource-id', 'android:id/button2')
        with patch.object(self.r, 'activity', return_value='cn.edu.aiit.axx/.activity.LoginActivity'), \
             patch.object(self.r, 'tap') as tap:
            self.assertFalse(self.r.dismiss_battery_prompt([deny]))
        tap.assert_not_called()

    def test_deadline_sends_zero_speed_even_when_route_loop_does_not_run(self):
        origin = tuple(self.config['anchor_wgs84'])
        self.r.last_position = origin
        with patch.object(m, 'send_location') as send:
            self.r.run_route(origin, m.now() - timedelta(seconds=1))
        send.assert_called_once_with(self.r.serial, *origin, 0)

    def test_region_guard_blocks_emulator_default_usa(self):
        def shell(args, **kw):
            return 'versionName=2.0.5\nprimaryCpuAbi=x86\n' if 'package' in args else '3' if 'settings' in args else ''
        with patch.object(self.r, 'call', side_effect=shell), patch.object(self.r, 'activity', return_value='WebActivity'), \
             patch.object(m, 'current_location', return_value=(37.421998, -122.084)):
            with self.assertRaisesRegex(RuntimeError, '取消自动运行'):
                self.r.preflight()

    def test_prepare_allows_listener_to_begin_after_start_but_rejects_wrong_fix(self):
        origin = tuple(self.config['anchor_wgs84'])
        with patch.object(m.time, 'monotonic', side_effect=[0, 16]), \
                patch.object(m, 'current_location', side_effect=RuntimeError('GPS 尚未请求')):
            self.r.prepare(origin)
        self.assertEqual(self.r.last_position, origin)
        with patch.object(m.time, 'monotonic', side_effect=[0, 16]), \
                patch.object(m, 'current_location', return_value=(37.42, -122.08)):
            with self.assertRaisesRegex(RuntimeError, '未对齐'):
                self.r.prepare(origin)

    def test_start_confirms_both_native_run_ui_and_stationary_gps(self):
        origin = tuple(self.config['anchor_wgs84'])
        self.r.last_position = origin
        sequence = [[element(text='开始运动')], [element(rid='sport_run_ll_ll')]]
        with patch.object(self.r, 'ui', side_effect=sequence), patch.object(self.r, 'tap'), \
                patch.object(m, 'send_location') as send, patch.object(m, 'current_location', return_value=origin):
            self.r.start()
        self.assertTrue(self.r.started)
        send.assert_called_once_with(self.r.serial, *origin, 0)

    def test_trial_failure_finishes_owned_record_and_does_not_write_daily_state(self):
        runner = Mock()
        runner.serial = 'emulator-5554'
        runner.last_position = tuple(self.config['anchor_wgs84'])
        runner.started = False
        runner.preflight.return_value = runner.last_position
        runner.start.side_effect = lambda: setattr(runner, 'started', True)
        runner.run_route.side_effect = RuntimeError('运行中位置异常')
        runner.finish.side_effect = lambda: setattr(runner, 'started', False)
        target = Path(self.temp.name) / 'trial-result.json'
        state = {'mode': 'trial', 'status': 'preparing'}
        with patch.object(m.ctypes, 'windll', SimpleNamespace(kernel32=Mock()), create=True), \
                patch.object(m, 'send_location'):
            code = m.execute_session(runner, target, state, trial_seconds=60)
        self.assertEqual(code, 1)
        self.assertTrue(state['start_confirmed'])
        self.assertTrue(state['finish_confirmed'])
        runner.finish.assert_called_once()
        self.assertFalse((Path(self.temp.name) / 'daily-state.json').exists())


if __name__ == '__main__':
    unittest.main()
