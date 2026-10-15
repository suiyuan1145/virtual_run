"""Local Windows credential storage and the observed Anxiaoxin login form."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shlex
import time
import re

PACKAGE = 'cn.edu.aiit.axx'


def has_open_run_activity(activity_dump):
    """Read live task entries, excluding Android's last-paused references."""
    component = re.compile(r'(?<![\w.])' + re.escape(PACKAGE) + r'/(?:\.activity\.|' +
                           re.escape(PACKAGE) + r'\.activity\.)SportMapActivity(?=\s|$)')
    for line in activity_dump.splitlines():
        line = line.strip()
        if not (re.match(r'(?:mResumedActivity|mTopResumedActivity|topResumedActivity|ResumedActivity|Resumed)\s*[:=]', line)
                or line.startswith('Activities=[')
                or re.match(r'\*\s+(?:Hist\s+#\d+\s*:|ActivityRecord\{)', line)):
            continue
        for record in re.findall(r'ActivityRecord\{([^}]*)\}', line):
            if not component.search(record):
                continue
            task = re.search(r'\bt(-?\d+)\b', record)
            if (task and int(task.group(1)) < 0) or re.search(r'(?:^|\s)f(?:\s|$)', record):
                continue
            return True
    return False


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('保存登录信息需要 Windows 本机加密功能。')
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN, current Windows user, never machine-wide.
    if decrypt:
        function = library.CryptUnprotectData
        second_type = ctypes.c_void_p
        second = None
    else:
        function = library.CryptProtectData
        second_type = wintypes.LPCWSTR
        second = 'VirtualRun login'
    function.argtypes = [ctypes.POINTER(Blob), second_type, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), second, None, None, None, 1, ctypes.byref(output)):
        raise RuntimeError('无法读取本机加密登录信息，请重新手动填写并保存。')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


def validate_credentials(account, password):
    if not account or not password:
        raise ValueError('请手动填写账号和密码。')
    # Android input text interprets %s as a space and cannot enter Unicode.
    if any(ord(c) < 33 or ord(c) > 126 for c in account + password) or '%s' in account + password:
        raise ValueError('自动输入支持英文、数字和符号；含中文、空格或 %s 的密码请在模拟器中手动登录。')
    return {'account': account, 'password': password}


def save_credentials(path, account, password):
    value = validate_credentials(account, password)
    encrypted = crypt(json.dumps(value).encode('utf-8'))
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_bytes(encrypted)
    temporary.replace(path)


def load_credentials(path):
    path = Path(path)
    if not path.is_file():
        raise RuntimeError('没有保存晨跑登录信息，请在软件中填写账号密码并保存。')
    try:
        value = json.loads(crypt(path.read_bytes(), decrypt=True))
        return validate_credentials(value['account'], value['password'])
    except (OSError, ValueError, KeyError, TypeError):
        raise RuntimeError('登录信息不可用，请重新手动填写并保存。') from None


def control(items, rid):
    return next((n for n in items if n.get('resource-id') == PACKAGE + ':id/' + rid), None)


def is_login_form(items):
    return all(control(items, rid) is not None for rid in ('name_et', 'psw_et', 'login_btn'))


def login(runner, account, password, stop):
    """Submit once; confirm an authenticated page, never navigate into a run."""
    validate_credentials(account, password)
    if not runner.serial or not runner.serial.startswith('emulator-'):
        raise RuntimeError('自动登录只用于配置的官方 Android 模拟器。')
    items = runner.ui()
    if not is_login_form(items):
        raise RuntimeError('当前不是已识别的账号密码登录页；请先打开安小信。')
    runner.log('填写登录信息并提交；账号密码不会写入日志。')
    try:
        for rid, value in (('name_et', account), ('psw_et', password)):
            if stop.is_set():
                raise RuntimeError('已停止登录。')
            # Clear the complete saved text; HOME + shift-END selects all.
            field = control(items, rid)
            bounds = list(map(int, re.findall(r'\d+', field.get('bounds', ''))))
            if len(bounds) != 4 or bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
                raise RuntimeError('field unavailable')
            runner.call(['shell', 'input', 'tap', str((bounds[0] + bounds[2]) // 2),
                         str((bounds[1] + bounds[3]) // 2)])
            runner.call(['shell', 'input', 'keyevent', 'KEYCODE_MOVE_END'])
            count = max(64, len(field.get('text', '')), len(value))
            runner.call(['shell', 'input', 'keyevent', *(['KEYCODE_DEL'] * count)])
            result = runner.call(['shell', 'input text ' + shlex.quote(value)])
            if 'Error' in result or 'Exception' in result:
                raise RuntimeError('input failed')
        # Read fresh bounds after typing. The observed form exposes its login
        # button above the keyboard; BACK could exit when a hardware keyboard
        # is configured, so do not dismiss it blindly.
        items = runner.ui()
        button = control(items, 'login_btn')
        if button is None or button.get('enabled', 'true') != 'true':
            raise RuntimeError('button unavailable')
        runner.tap(button)
    except Exception:
        # ADB errors can include the input command; never propagate secrets.
        raise RuntimeError('自动填写或提交失败，请在模拟器中检查登录页。') from None
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and not stop.is_set():
        items = runner.ui()
        if authenticated_page(items, runner.activity()) and not is_login_form(items):
            runner.log('已确认安小信登录成功。')
            return
        stop.wait(1)
    raise RuntimeError('未确认登录成功。请检查密码、网络或验证码提示；本次不重复提交。')


def authenticated_page(items, activity):
    return 'cn.edu.aiit.axx/.activity.HomeActivity' in activity or any(
        n.get('text') in ('智慧运动', '随性跑步', '开始运动') for n in items) or (
        'cn.edu.aiit.axx/.activity.WebActivity' in activity and
        any(n.get('text') in ('结束运动', '课外锻炼进度') for n in items))


def open_and_login(runner, account, password, stop):
    validate_credentials(account, password)
    activity = runner.activity()
    if has_open_run_activity(activity) or has_open_run_activity(runner.call(['shell', 'dumpsys', 'activity', 'activities'])):
        raise RuntimeError('当前存在跑步记录，请先结束记录再登录。')
    # Reuse known existing pages, including the completed-run WebView. Opening
    # Splash first could return to that same page and then wait for a nonexistent
    # password form. This recognizes UI state, not server token validity.
    items = runner.ui()
    if is_login_form(items):
        login(runner, account, password, stop)
        return
    if authenticated_page(items, activity):
        runner.log('安小信已处于登录后的页面，本次无需重复提交。')
        return
    runner.call(['shell', 'input', 'keyevent', 'KEYCODE_WAKEUP'])
    result = runner.call(['shell', 'am', 'start', '-n', PACKAGE + '/.activity.SplashActivity'])
    if 'Error:' in result or 'Permission Denial' in result:
        raise RuntimeError('无法打开安小信，请先安装原版 APK。')
    until = time.monotonic() + 60
    while time.monotonic() < until and not stop.is_set():
        items = runner.ui()
        if is_login_form(items):
            login(runner, account, password, stop)
            return
        if authenticated_page(items, runner.activity()):
            runner.log('安小信已登录，无需重复提交。')
            return
        stop.wait(1)
    raise RuntimeError('未找到账号密码登录页。请点击“查看后台画面”检查权限或其他提示；需要手动操作时切换窗口模式。')
