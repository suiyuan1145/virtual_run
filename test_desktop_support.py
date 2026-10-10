"""Verify persisted settings and bundled resources without touching a device."""
import tempfile
import os
import json
import sys
from pathlib import Path
import unittest
from unittest.mock import patch
import desktop_support as support


class DesktopSupportTests(unittest.TestCase):
    def test_preferences_round_trip_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(support, 'DATA_DIR', Path(temporary)):
            self.assertEqual(support.load_settings(), {})
            settings = {'sdk': 'C:/安卓/SDK', 'avd': 'VirtualRun_API30', 'speed': '6'}
            support.save_settings(settings)
            self.assertEqual(support.load_settings(), settings)
            self.assertEqual(str(support.sdk_directory()), str(Path(settings['sdk'])))
            (Path(temporary) / 'settings.json').write_text('broken', encoding='utf-8')
            self.assertEqual(support.load_settings(), {})

    def test_bundled_adb_persists_outside_extraction_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            resources, data = root / 'extract', root / 'user-data'
            source = resources / 'bundled-adb'
            source.mkdir(parents=True)
            for name in ('adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll', 'NOTICE.txt'):
                (source / name).write_bytes(b'first')
            with patch.object(support, 'RESOURCE_DIR', resources), patch.object(support, 'DATA_DIR', data):
                target = Path(support.bundled_adb())
                self.assertTrue(target.is_relative_to(data))
                self.assertEqual(target.read_bytes(), b'first')
                (source / 'adb.exe').write_bytes(b'updated')
                self.assertEqual(Path(support.bundled_adb()).read_bytes(), b'updated')

    def test_embedded_guide_and_readme_are_available(self):
        self.assertIn('准备起点', support.read_document('docs/使用指南.md'))
        self.assertIn(support.VERSION, support.read_document('README.md'))

    def test_headless_launch_is_optional_and_does_not_modify_environment(self):
        with patch.dict(os.environ, {'QT_OPENGL': 'software'}, clear=False):
            command, environment = support.emulator_launch('C:/SDK with spaces', 'VirtualRun_API30', True)
            self.assertIn('-no-window', command)
            self.assertIn('-no-audio', command)
            self.assertEqual(environment['QT_OPENGL'], 'desktop')
            self.assertEqual(os.environ['QT_OPENGL'], 'software')
            self.assertNotIn('-no-window', support.emulator_launch('C:/SDK', 'VirtualRun_API30')[0])

    def test_morning_configuration_preserves_background_choice(self):
        import morning_support
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(morning_support, 'DATA_DIR', Path(temporary)), \
             patch.object(morning_support, 'runtime_python', return_value=Path(sys.executable)):
            target = morning_support.configure('C:/SDK', 'VirtualRun_API30', 31.23, 121.47, headless=True)
            configuration = json.loads((target / '晨跑配置.json').read_text(encoding='utf-8'))
            self.assertTrue(configuration['headless'])
            self.assertFalse(configuration['auto_login'])
            morning_support.configure('C:/SDK', 'VirtualRun_API30', 31.23, 121.47)
            self.assertFalse(json.loads((target / '晨跑配置.json').read_text(encoding='utf-8'))['headless'])


if __name__ == '__main__':
    unittest.main()
