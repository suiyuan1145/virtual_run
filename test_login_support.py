import json
import os
from pathlib import Path
import shlex
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import xml.etree.ElementTree as ET
import login_support as l
import morning_run as m


def node(rid='', text='', password=False):
    return ET.Element('node', {'resource-id': l.PACKAGE + ':id/' + rid, 'text': text,
                              'password': 'true' if password else 'false', 'bounds': '[10,20][200,120]'})


def form():
    return [node('name_et', 'oldaccount'), node('psw_et', '••••', True), node('login_btn', '登录')]


class LoginTests(unittest.TestCase):
    def setUp(self):
        self.stop = threading.Event()
        self.runner = Mock(serial='emulator-5554')
        self.runner.call.return_value = ''
        self.runner.activity.return_value = 'cn.edu.aiit.axx/.activity.HomeActivity'
        m.STOP.clear()

    @unittest.skipUnless(os.name == 'nt', 'Windows DPAPI')
    def test_real_dpapi_roundtrip_and_corruption_never_exposes_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'login.dat'
            l.save_credentials(path, 'test-user', 'Secret123!')
            self.assertNotIn(b'Secret123!', path.read_bytes())
            self.assertEqual(l.load_credentials(path), {'account': 'test-user', 'password': 'Secret123!'})
            path.write_bytes(b'broken')
            with self.assertRaisesRegex(RuntimeError, '重新手动填写'):
                l.load_credentials(path)

    def test_login_identification_requires_native_form_not_generic_text(self):
        self.assertTrue(l.is_login_form(form()))
        self.assertFalse(l.is_login_form([node(text='登录说明与密码政策')]))

    def test_submit_once_shell_quoted_symbols_and_confirm_authenticated_screen(self):
        password = "P'a$;<>!9"
        self.runner.ui.side_effect = [form(), form(), [node(text='智慧运动')]]
        l.login(self.runner, 'newuser', password, self.stop)
        text_commands = [call.args[0] for call in self.runner.call.call_args_list if 'input text ' in str(call.args[0])]
        self.assertEqual(text_commands[-1], ['shell', 'input text ' + shlex.quote(password)])
        self.runner.tap.assert_called_once()
        self.assertTrue(all(password not in str(call) for call in self.runner.log.call_args_list))

    def test_input_failure_does_not_log_password_in_adb_error(self):
        self.runner.ui.return_value = form()
        self.runner.call.side_effect = RuntimeError('adb secret password')
        with self.assertRaises(RuntimeError) as error:
            l.login(self.runner, 'user', 'Password123', self.stop)
        self.assertNotIn('secret', str(error.exception))
        self.runner.tap.assert_not_called()

    def test_failed_login_never_continuously_retries(self):
        self.runner.ui.return_value = form()
        with patch.object(l.time, 'monotonic', side_effect=[0, 46]):
            with self.assertRaisesRegex(RuntimeError, '不重复提交'):
                l.login(self.runner, 'user', 'Password123', self.stop)
        self.runner.tap.assert_called_once()

    def test_rejects_phone_existing_record_and_unsupported_input(self):
        self.runner.serial = 'phone123'
        with self.assertRaisesRegex(RuntimeError, '官方 Android'):
            l.login(self.runner, 'user', 'Password123', self.stop)
        self.runner.activity.return_value = 'SportMapActivity'
        with self.assertRaisesRegex(RuntimeError, '先结束'):
            l.open_and_login(self.runner, 'user', 'Password123', self.stop)
        for password in ('含中文', 'a b', 'literal%s'):
            with self.assertRaises(ValueError):
                l.validate_credentials('user', password)

    def test_already_logged_in_does_not_submit_credentials(self):
        self.runner.ui.return_value = [node(text='智慧运动')]
        l.open_and_login(self.runner, 'user', 'Password123', self.stop)
        self.runner.tap.assert_not_called()

    def test_completed_sport_page_is_reused_without_opening_splash_or_typing(self):
        self.runner.activity.return_value = 'cn.edu.aiit.axx/.activity.WebActivity'
        self.runner.ui.return_value = [node(text='结束运动'), node(text='公里')]
        l.open_and_login(self.runner, 'user', 'Password123', self.stop)
        self.runner.tap.assert_not_called()
        self.assertFalse(any('am' in call.args[0] or 'input' in call.args[0] for call in self.runner.call.call_args_list))

    def test_generic_web_page_is_not_treated_as_authenticated(self):
        self.assertFalse(l.authenticated_page([node(text='请授予权限')], 'cn.edu.aiit.axx/.activity.WebActivity'))
        self.assertFalse(l.authenticated_page([node(text='结束运动')], 'other.app/.activity.WebActivity'))

    def test_record_in_background_blocks_login_before_navigation(self):
        self.runner.activity.return_value = 'cn.edu.aiit.axx/.activity.WebActivity'
        self.runner.call.return_value = 'SportMapActivity'
        with self.assertRaisesRegex(RuntimeError, '先结束'):
            l.open_and_login(self.runner, 'user', 'Password123', self.stop)
        self.runner.ui.assert_not_called()
        self.runner.tap.assert_not_called()

    def test_runner_redacts_saved_login_xml(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = m.Runner({}, Path(folder))
            root = ET.Element('hierarchy')
            root.extend(form())
            with patch.object(runner, 'call', side_effect=['', ET.tostring(root, encoding='unicode')]):
                original = runner.ui()
            self.assertEqual(l.control(original, 'name_et').get('text'), 'oldaccount')
            saved = (Path(folder) / 'latest-ui.xml').read_text(encoding='utf-8')
            self.assertNotIn('oldaccount', saved)
            self.assertNotIn('••••', saved)

    def test_morning_login_missing_credentials_prevents_run(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = m.Runner({'auto_login': False}, Path(folder))
            with patch.object(runner, 'call', return_value=''), patch.object(runner, 'ui', return_value=form()):
                with self.assertRaisesRegex(RuntimeError, '需要登录'):
                    runner.navigate_to_start()
            self.assertFalse(runner.started)

    def test_morning_uses_saved_credentials_once_and_continues_navigation(self):
        with tempfile.TemporaryDirectory() as folder:
            runner = m.Runner({'auto_login': True, 'credential_file': 'local.dat'}, Path(folder))
            with patch.object(runner, 'call', return_value=''), \
                 patch.object(runner, 'ui', side_effect=[form(), [node(text='开始运动')]]), \
                 patch.object(m, 'load_credentials', return_value={'account': 'user', 'password': 'Password123'}), \
                 patch.object(m, 'login') as authenticate:
                runner.navigate_to_start()
            authenticate.assert_called_once()
            self.assertFalse(runner.started)


if __name__ == '__main__':
    unittest.main()
