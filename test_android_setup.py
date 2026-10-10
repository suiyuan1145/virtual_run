import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile
import android_setup as setup
import morning_support


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_only_stable_windows_archive_selected(self):
        def package(version, channel, host='windows'):
            return f'<remotePackage path="emulator"><revision><major>{version}</major></revision><uses-license ref="license"/><channelRef ref="channel-{channel}"/><archives><archive><host-os>{host}</host-os><complete><size>3</size><checksum>{"a" * 40}</checksum><url>emulator.zip</url></complete></archive></archives></remotePackage>'
        xml = ('<repository><license id="license">Terms</license>' + package(100, 2) + package(37, 0) + package(38, 0, 'linux') + '</repository>').encode()
        found = setup.packages(xml, setup.REPOSITORY, {'emulator'})
        self.assertEqual(found[0]['version'], (37, 0, 0))
        self.assertEqual(found[0]['url'], 'https://dl.google.com/android/repository/emulator.zip')
        with self.assertRaises(RuntimeError):
            setup.packages(xml.replace(b'emulator.zip', b'https://evil.test/x.zip'), setup.REPOSITORY, {'emulator'})

    def test_archive_cannot_escape_and_staging_is_removed(self):
        archive = self.root / 'bad.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('../escape', 'bad')
        sdk = self.root / 'sdk'
        sdk.mkdir()
        with self.assertRaises(RuntimeError):
            setup.install_archive({'name': 'emulator'}, archive, sdk, threading.Event())
        self.assertFalse((self.root / 'escape').exists())
        self.assertEqual(list(sdk.iterdir()), [])

    def test_archive_installed_once_and_existing_directory_preserved(self):
        archive = self.root / 'tools.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('platform-tools/adb.exe', 'test')
        sdk = self.root / 'sdk'
        sdk.mkdir()
        package = {'name': 'platform-tools', 'element': setup.ET.fromstring('<remotePackage path="platform-tools"><revision><major>37</major></revision></remotePackage>'), 'namespaces': {}}
        setup.install_archive(package, archive, sdk, threading.Event())
        self.assertEqual((sdk / 'platform-tools/adb.exe').read_text(), 'test')
        with self.assertRaises(RuntimeError):
            setup.install_archive(package, archive, sdk, threading.Event())

    def test_existing_avd_login_disk_never_replaced(self):
        home, sdk = self.root / 'avds', self.root / 'sdk'
        self.assertTrue(setup.create_avd(sdk, 'Test', home))
        disk = home / 'Test.avd/userdata-qemu.img'
        disk.write_bytes(b'private device data')
        self.assertFalse(setup.create_avd(sdk, 'Test', home))
        self.assertEqual(disk.read_bytes(), b'private device data')
        with self.assertRaises(ValueError):
            setup.create_avd(sdk, '../Test', home)

    def test_download_cache_requires_size_and_checksum(self):
        cached = self.root / 'a.zip'
        cached.write_bytes(b'abc')
        package = {'url': 'https://dl.google.com/a.zip', 'size': 3, 'algorithm': 'sha1', 'digest': hashlib.sha1(b'abc').hexdigest()}
        self.assertEqual(setup.download(package, self.root, None, threading.Event(), lambda _: None), cached)

    def test_install_requires_license_acceptance(self):
        with self.assertRaisesRegex(RuntimeError, '许可'):
            setup.install(self.root / 'sdk', self.root / 'cache', 'Test', '', False, threading.Event(), lambda _: None)

    def test_personal_morning_configuration_uses_runtime_not_developer_paths(self):
        python = self.root / 'python.exe'
        python.write_text('test')
        with patch.object(morning_support, 'DATA_DIR', self.root / 'data'), patch.object(morning_support, 'runtime_python', return_value=python):
            target = morning_support.configure(self.root / 'sdk', 'Test', 31, 121, 12, 150)
        config = json.loads((target / '晨跑配置.json').read_text(encoding='utf-8'))
        self.assertEqual(config['python'], str(python))
        self.assertEqual(config['anchor_wgs84'], [31, 121])
        self.assertTrue((target / '启动晨跑.ps1').read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertFalse((target / 'daily-state.json').exists())
        self.assertTrue((target / 'login_support.py').is_file())
        self.assertFalse(config['auto_login'])
        self.assertNotIn('password', config)

    def test_morning_auto_login_requires_saved_credentials(self):
        python = self.root / 'python.exe'
        python.write_text('test')
        with patch.object(morning_support, 'DATA_DIR', self.root / 'data'), patch.object(morning_support, 'runtime_python', return_value=python):
            with self.assertRaisesRegex(RuntimeError, '先填写账号密码'):
                morning_support.configure(self.root / 'sdk', 'Test', 31, 121, auto_login=True)


if __name__ == '__main__':
    unittest.main()
