"""Recover a known Anxiaoxin system ANR only before creating any run."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import call, patch
import xml.etree.ElementTree as ET

import morning_run as m


def anr_dialog(title="安小信 isn't responding", package='android',
               resource_package='android', missing=()):
    labels = {
        'alertTitle': title,
        'aerr_close': 'Close app',
        'aerr_wait': 'Wait',
    }
    return [ET.Element('node', {
        'package': package,
        'resource-id': resource_package + ':id/' + rid,
        'text': text,
        'bounds': '[10,20][200,120]',
    }) for rid, text in labels.items() if rid not in missing]


def activity(component):
    return 'ActivityRecord{ab12cd u0 cn.edu.aiit.axx/.activity.' + component + ' t66}'


WEB_DUMP = '    mResumedActivity: ' + activity('WebActivity')


class AnrRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runner = m.Runner({}, Path(self.temp.name))
        self.runner.serial = 'emulator-5554'
        m.STOP.clear()
        self.addCleanup(m.STOP.clear)

    def test_known_system_anr_closes_app_and_reopens_original_splash_once(self):
        with patch.object(self.runner, 'call', return_value=WEB_DUMP) as adb, \
             patch.object(self.runner, 'close_app') as close, \
             patch.object(self.runner, 'tap') as tap, \
             patch.object(m, 'send_location') as send:
            self.assertTrue(self.runner.recover_unresponsive_app(anr_dialog()))
        self.assertTrue(self.runner.anr_recovery_attempted)
        self.assertFalse(self.runner.started)
        close.assert_called_once_with()
        self.assertEqual([entry for entry in adb.call_args_list if entry.args[0][:3] == ['shell', 'am', 'start']], [
            call(['shell', 'am', 'start', '-n', m.PACKAGE + '/.activity.SplashActivity'])])
        self.assertEqual(tap.call_args.args[0].get('resource-id'), 'android:id/aerr_close')
        send.assert_not_called()

    def test_second_anr_refuses_another_restart(self):
        with patch.object(self.runner, 'call', return_value=WEB_DUMP) as adb, \
             patch.object(self.runner, 'close_app') as close:
            self.assertTrue(self.runner.recover_unresponsive_app(anr_dialog()))
            with self.assertRaises(m.NavigationTimeout):
                self.runner.recover_unresponsive_app(anr_dialog())
        close.assert_called_once_with()
        self.assertEqual(sum(entry.args[0][:3] == ['shell', 'am', 'start']
                             for entry in adb.call_args_list), 1)

    def test_another_app_anr_is_not_handled(self):
        with patch.object(self.runner, 'call') as adb, \
             patch.object(self.runner, 'close_app') as close:
            self.assertFalse(self.runner.recover_unresponsive_app(
                anr_dialog(title="Other app isn't responding")))
        close.assert_not_called()
        adb.assert_not_called()

    def test_non_system_package_or_resource_ids_are_not_handled(self):
        for package, resource_package in ((m.PACKAGE, 'android'),
                                          ('android', m.PACKAGE),
                                          (m.PACKAGE, m.PACKAGE)):
            with self.subTest(package=package, resource_package=resource_package), \
                 patch.object(self.runner, 'call') as adb, \
                 patch.object(self.runner, 'close_app') as close:
                self.assertFalse(self.runner.recover_unresponsive_app(
                    anr_dialog(package=package, resource_package=resource_package)))
                close.assert_not_called()
                adb.assert_not_called()

    def test_incomplete_system_dialog_does_not_trigger_recovery(self):
        for missing in ('alertTitle', 'aerr_close', 'aerr_wait'):
            with self.subTest(missing=missing), \
                 patch.object(self.runner, 'call') as adb, \
                 patch.object(self.runner, 'close_app') as close:
                self.assertFalse(self.runner.recover_unresponsive_app(
                    anr_dialog(missing=(missing,))))
                close.assert_not_called()
                adb.assert_not_called()

    def test_foreground_or_background_run_prevents_force_close(self):
        native = activity('SportMapActivity')
        for dump in ('    mResumedActivity: ' + native,
                     WEB_DUMP + '\n      * Hist #1: ' + native,
                     WEB_DUMP + '\n      Activities=[' + native + ']'):
            with self.subTest(activity_dump=dump), \
                 patch.object(self.runner, 'call', return_value=dump) as adb, \
                 patch.object(self.runner, 'close_app') as close:
                with self.assertRaises(RuntimeError):
                    self.runner.recover_unresponsive_app(anr_dialog())
                close.assert_not_called()
                self.assertFalse(any(entry.args[0][:3] == ['shell', 'am', 'start']
                                     for entry in adb.call_args_list))

    def test_owned_started_record_prevents_force_close(self):
        self.runner.started = True
        with patch.object(self.runner, 'call', return_value=WEB_DUMP) as adb, \
             patch.object(self.runner, 'close_app') as close:
            with self.assertRaises(RuntimeError):
                self.runner.recover_unresponsive_app(anr_dialog())
        close.assert_not_called()
        self.assertFalse(any(entry.args[0][:3] == ['shell', 'am', 'start']
                             for entry in adb.call_args_list))

    def test_navigation_recovers_dialog_before_entering_start_page(self):
        start = [ET.Element('node', {'text': '开始运动'})]
        with patch.object(self.runner, 'activity', return_value=WEB_DUMP), \
             patch.object(self.runner, 'call', return_value=WEB_DUMP), \
             patch.object(self.runner, 'ui', side_effect=[anr_dialog(), start]), \
             patch.object(self.runner, 'close_app') as close, \
             patch.object(self.runner, 'tap') as tap, \
             patch.object(m.STOP, 'wait', return_value=False):
            self.runner.navigate_to_start()
        close.assert_called_once_with()
        self.assertEqual(tap.call_args.args[0].get('resource-id'), 'android:id/aerr_close')
        self.assertFalse(self.runner.started)

    def test_anr_recovery_does_not_resubmit_login_within_same_navigation(self):
        form = [ET.Element('node', {'resource-id': m.PACKAGE + ':id/' + rid})
                for rid in ('name_et', 'psw_et', 'login_btn')]
        self.runner.c = {'auto_login': True, 'credential_file': 'unused.dat'}
        with patch.object(self.runner, 'activity', return_value=WEB_DUMP), \
             patch.object(self.runner, 'call', return_value=WEB_DUMP), \
             patch.object(self.runner, 'ui', side_effect=[form, anr_dialog(), form]), \
             patch.object(self.runner, 'close_app') as close, \
             patch.object(m, 'load_credentials', return_value={'account': 'test-user', 'password': 'Test123!'}) as load, \
             patch.object(m, 'login') as authenticate, \
             patch.object(m.STOP, 'wait', return_value=False):
            with self.assertRaisesRegex(RuntimeError, '需要登录'):
                self.runner.navigate_to_start()
        load.assert_called_once()
        authenticate.assert_called_once()
        close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
