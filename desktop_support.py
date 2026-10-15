"""Resources and persistent storage for the Windows desktop distribution."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

VERSION = '1.4.7'
RESOURCE_DIR = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else RESOURCE_DIR
DATA_DIR = Path(os.environ.get('VIRTUAL_RUN_DATA_DIR') or
                str(Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'VirtualRunDesktop'))


def read_document(name):
    return (RESOURCE_DIR / name).read_text(encoding='utf-8-sig')


def load_settings():
    try:
        value = json.loads((DATA_DIR / 'settings.json').read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(value):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = DATA_DIR / 'settings.tmp'
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(DATA_DIR / 'settings.json')


def sdk_directory():
    value = load_settings().get('sdk') or os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    return Path(value) if value else Path.home() / 'AppData/Local/Android/Sdk'


def emulator_launch(sdk, avd, headless=False):
    """Shared GUI/daily launch settings; do not alter global Qt or AVD data."""
    sdk = str(sdk)
    environment = dict(os.environ, ANDROID_HOME=sdk, ANDROID_SDK_ROOT=sdk,
                       QT_OPENGL='desktop')
    command = [str(Path(sdk) / 'emulator/emulator.exe'), '-avd', avd,
               '-no-snapshot-load', '-gpu', 'swangle', '-feature', '-Vulkan']
    if headless:
        command.extend(['-no-window', '-no-audio'])
    return command, environment


def bundled_adb():
    source = RESOURCE_DIR / 'bundled-adb'
    if not (source / 'adb.exe').is_file():
        return None
    # ADB starts a separate server. Copy it outside the one-file extraction
    # directory so that closing this app does not remove the server's files.
    target = DATA_DIR / 'tools' / 'platform-tools'
    target.mkdir(parents=True, exist_ok=True)
    for name in ('adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll', 'NOTICE.txt'):
        original, installed = source / name, target / name
        if not installed.is_file() or hashlib.sha256(original.read_bytes()).digest() != hashlib.sha256(installed.read_bytes()).digest():
            temporary = target / (name + '.tmp')
            shutil.copyfile(original, temporary)
            temporary.replace(installed)
    return str(target / 'adb.exe')


def default_apk():
    for candidate in (APP_DIR / '安小信.apk', APP_DIR / 'analysis/input.apk',
                      APP_DIR.parent / 'analysis/input.apk'):
        if candidate.is_file():
            return candidate
    return APP_DIR / '安小信.apk'
