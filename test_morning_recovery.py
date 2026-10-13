"""Navigation latency and pre-start retries must not create duplicate records."""
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import morning_run as m


class Clock:
    def __init__(self):
        self.seconds = 0

    def monotonic(self):
        return self.seconds

    def wait(self, seconds):
        self.seconds += seconds
        return False


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = json.loads(Path('tests/fixtures/morning-config.json').read_text(encoding='utf-8'))
        m.STOP.clear()

    def test_delayed_page_load_is_waited_for_without_rapid_repeated_taps(self):
        clock = Clock()
        home = m.nodes('<hierarchy><node clickable="true" bounds="[0,100][200,300]">'
                       '<node text="智慧运动" bounds="[0,250][200,300]" /></node></hierarchy>')
        start = m.nodes('<hierarchy><node text="开始运动"/></hierarchy>')
        runner = m.Runner(self.config, self.directory)
        with patch.object(runner, 'activity', return_value='HomeActivity'), \
             patch.object(runner, 'call', return_value=''), \
             patch.object(runner, 'ui', side_effect=lambda: start if clock.seconds >= 15 else home), \
             patch.object(runner, 'tap') as tap, \
             patch.object(m.time, 'monotonic', side_effect=clock.monotonic), \
             patch.object(m.STOP, 'wait', side_effect=clock.wait):
            runner.navigate_to_start()
        tap.assert_called_once()
        self.assertGreaterEqual(clock.seconds, 15)

    def test_stale_home_is_reopened_once_and_then_enters_start_page(self):
        clock = Clock()
        home = m.nodes('<hierarchy><node clickable="true" bounds="[0,100][200,300]">'
                       '<node text="智慧运动" bounds="[0,250][200,300]" /></node></hierarchy>')
        start = m.nodes('<hierarchy><node text="开始运动"/></hierarchy>')
        runner = m.Runner(self.config, self.directory)
        reopened = []
        def call(args, **kwargs):
            if args[:3] == ['shell', 'am', 'start']:
                reopened.append(True)
            return ''
        with patch.object(runner, 'activity', return_value='HomeActivity'), \
             patch.object(runner, 'call', side_effect=call), \
             patch.object(runner, 'ui', side_effect=lambda: start if reopened else home), \
             patch.object(runner, 'tap') as tap, \
             patch.object(m.time, 'monotonic', side_effect=clock.monotonic), \
             patch.object(m.STOP, 'wait', side_effect=clock.wait):
            runner.navigate_to_start()
        self.assertEqual(len(reopened), 1)
        self.assertEqual(tap.call_count, 2)
        self.assertEqual(tap.call_args.args[0].get('text'), '智慧运动')

    def runner(self):
        runner = Mock()
        runner.c = dict(self.config, auto_origin=False)
        runner.started = False
        runner.last_position = None
        runner.preflight.return_value = (31.238138, 121.52577)
        runner.start.side_effect = lambda: setattr(runner, 'started', True)
        runner.finish.side_effect = lambda: setattr(runner, 'started', False)
        return runner

    def execute(self, runner, state=None):
        state = state if state is not None else {'status': 'preparing', 'mode': 'scheduled'}
        start = datetime(2026, 10, 14, 7, tzinfo=m.BEIJING)
        with patch.object(m.ctypes, 'windll', SimpleNamespace(kernel32=Mock()), create=True), \
             patch.object(m, 'now', return_value=start), \
             patch.object(m.STOP, 'wait', return_value=False), \
             patch.object(m, 'send_location'):
            code = m.execute_session(runner, self.directory / 'result.json', state, start=start, end=start + timedelta(hours=1))
        return code, state

    def test_navigation_retry_starts_only_one_record_and_persists_intent_before_tap(self):
        runner = self.runner()
        runner.navigate_to_start.side_effect = [m.NavigationTimeout('delayed entry'), None]
        def start():
            saved = json.loads((self.directory / 'result.json').read_text(encoding='utf-8'))
            self.assertTrue(saved['start_attempted'])
            runner.started = True
        runner.start.side_effect = start
        code, state = self.execute(runner)
        self.assertEqual(code, 0)
        self.assertEqual(state['preparation_attempts'], 2)
        runner.start.assert_called_once()
        runner.prepare.assert_called_once()
        runner.preflight.assert_called_with()

    def test_retry_exhaustion_never_starts_or_prepares_location(self):
        runner = self.runner()
        runner.navigate_to_start.side_effect = m.NavigationTimeout('entry unavailable')
        code, state = self.execute(runner)
        self.assertEqual(code, 1)
        self.assertEqual(runner.navigate_to_start.call_count, 3)
        runner.start.assert_not_called()
        runner.prepare.assert_not_called()
        self.assertFalse(m.retryable_daily_state(state))

    def test_uncertain_start_is_never_retried_even_without_confirmation(self):
        runner = self.runner()
        runner.start.side_effect = RuntimeError('unconfirmed start')
        code, state = self.execute(runner)
        self.assertEqual(code, 1)
        self.assertTrue(state['start_attempted'])
        self.assertFalse(state['start_confirmed'])
        self.assertFalse(m.retryable_daily_state(state))
        runner.start.assert_called_once()
        runner.navigate_to_start.assert_called_once()

    def test_old_144_navigation_failure_is_recoverable_but_ambiguous_failure_is_not(self):
        self.assertTrue(m.retryable_daily_state({'status': 'failed', 'start_confirmed': False,
                                               'error': '点击“智慧运动”后页面未变化，已停止重复点击'}))
        for extra in ({'start_attempted': True}, {'status': 'cancelled'}, {'status': 'preparing'},
                      {'started_at': 'a timestamp'}, {'preparation_attempts': 3}):
            self.assertFalse(m.retryable_daily_state({'status': 'failed', 'retryable_before_start': True, **extra}))
        self.assertFalse(m.retryable_daily_state({'status': 'failed', 'start_confirmed': False, 'error': 'unknown failure'}))

    def test_cancel_during_retry_wait_does_not_start_another_attempt(self):
        runner = self.runner()
        runner.navigate_to_start.side_effect = m.NavigationTimeout('delayed entry')
        state = {}
        with patch.object(m.STOP, 'wait', side_effect=lambda _: m.STOP.set()):
            with self.assertRaisesRegex(RuntimeError, '取消'):
                m.prepare_navigation(runner, state, Mock(), m.now())
        runner.navigate_to_start.assert_called_once()
        runner.start.assert_not_called()
        m.STOP.clear()

    def test_expired_window_prevents_navigation_and_start(self):
        runner = self.runner()
        with self.assertRaises(m.NavigationTimeout):
            m.prepare_navigation(runner, {}, Mock(), m.now() - timedelta(minutes=2))
        runner.navigate_to_start.assert_not_called()
        runner.start.assert_not_called()

    def test_home_login_refresh_uses_original_form_once_when_opted_in(self):
        clock = Clock()
        home = m.nodes('<hierarchy><node clickable="true" bounds="[0,100][200,300]">'
                       '<node text="智慧运动" bounds="[0,250][200,300]" /></node></hierarchy>')
        form = m.nodes('<hierarchy><node resource-id="cn.edu.aiit.axx:id/name_et"/>'
                       '<node resource-id="cn.edu.aiit.axx:id/psw_et"/><node resource-id="cn.edu.aiit.axx:id/login_btn"/></hierarchy>')
        start = m.nodes('<hierarchy><node text="开始运动"/></hierarchy>')
        runner = m.Runner(dict(self.config, auto_login=True, credential_file='saved.dat'), self.directory)
        page = ['home']
        def call(args, **kwargs):
            if args[:3] == ['shell', 'am', 'start']:
                self.assertTrue(args[-1].endswith('/.activity.LoginActivity'))
                page[0] = 'login'
            return ''
        with patch.object(runner, 'activity', return_value='HomeActivity'), \
             patch.object(runner, 'call', side_effect=call), patch.object(runner, 'tap'), \
             patch.object(runner, 'ui', side_effect=lambda: {'home': home, 'login': form, 'start': start}[page[0]]), \
             patch.object(m.time, 'monotonic', side_effect=clock.monotonic), \
             patch.object(m.STOP, 'wait', side_effect=clock.wait), \
             patch.object(m, 'load_credentials', return_value={'account': 'test', 'password': 'test'}), \
             patch.object(m, 'login', side_effect=lambda *args: page.__setitem__(0, 'start')) as login:
            runner.navigate_to_start()
        self.assertTrue(runner.login_refresh_attempted)
        login.assert_called_once()

    def test_navigation_only_leaves_daily_state_and_location_untouched(self):
        state_path = self.directory / 'daily-state.json'
        state_path.write_bytes(b'previous result')
        runner = self.runner()
        args = SimpleNamespace(check=False, scheduled=False, trial_seconds=None, test_seconds=None, navigate_only=True)
        with patch.object(m, 'send_location') as send:
            self.assertEqual(m.run_mode(args, self.directory / 'config.json', self.config, self.directory, runner), 0)
        self.assertEqual(state_path.read_bytes(), b'previous result')
        runner.start.assert_not_called()
        runner.prepare.assert_not_called()
        runner.run_route.assert_not_called()
        send.assert_not_called()

    def test_authenticated_page_receives_fresh_load_budget_after_slow_login(self):
        clock = Clock()
        form = m.nodes('<hierarchy><node resource-id="cn.edu.aiit.axx:id/name_et"/>'
                       '<node resource-id="cn.edu.aiit.axx:id/psw_et"/><node resource-id="cn.edu.aiit.axx:id/login_btn"/></hierarchy>')
        loading = m.nodes('<hierarchy><node text="智慧运动"/></hierarchy>')
        start = m.nodes('<hierarchy><node text="开始运动"/></hierarchy>')
        runner = m.Runner(dict(self.config, auto_login=True, credential_file='saved.dat'), self.directory)
        authenticated = []
        def login(*args):
            clock.seconds = 70
            authenticated.append(True)
        with patch.object(runner, 'activity', return_value='LoginActivity'), \
             patch.object(runner, 'call', return_value=''), \
             patch.object(runner, 'ui', side_effect=lambda: form if not authenticated else start if clock.seconds >= 110 else loading), \
             patch.object(m.time, 'monotonic', side_effect=clock.monotonic), \
             patch.object(m.STOP, 'wait', side_effect=clock.wait), \
             patch.object(m, 'load_credentials', return_value={'account': 'test', 'password': 'test'}), \
             patch.object(m, 'login', side_effect=login):
            runner.navigate_to_start()
        self.assertEqual(clock.seconds, 110)

    def test_same_day_known_navigation_failure_can_resume_remaining_preparations(self):
        start = datetime(2026, 10, 14, 6, 55, tzinfo=m.BEIJING)
        old = {'date': '2026-10-14', 'status': 'failed', 'error': 'navigation timeout',
               'retryable_before_start': True, 'preparation_attempts': 1, 'start_attempted': False}
        (self.directory / 'daily-state.json').write_text(json.dumps(old), encoding='utf-8')
        args = SimpleNamespace(check=False, scheduled=True, trial_seconds=None, test_seconds=None)
        with patch.object(m, 'now', return_value=start), patch.object(m, 'execute_session', return_value=0) as execute:
            self.assertEqual(m.run_mode(args, self.directory / 'config.json', self.config, self.directory, self.runner()), 0)
        state = execute.call_args.args[2]
        self.assertEqual(state['preparation_attempts'], 1)
        self.assertEqual(state['status'], 'preparing')
        self.assertNotIn('error', state)

    def test_same_day_start_intent_blocks_restart_despite_failed_status(self):
        start = datetime(2026, 10, 14, 6, 55, tzinfo=m.BEIJING)
        (self.directory / 'daily-state.json').write_text(json.dumps({'date': '2026-10-14', 'status': 'failed',
                                                                  'start_attempted': True}), encoding='utf-8')
        args = SimpleNamespace(check=False, scheduled=True, trial_seconds=None, test_seconds=None)
        with patch.object(m, 'now', return_value=start), patch.object(m, 'execute_session') as execute:
            self.assertEqual(m.run_mode(args, self.directory / 'config.json', self.config, self.directory, self.runner()), 0)
        execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
