"""Daily 07:00-08:00 controller for the official Android Emulator."""
from __future__ import annotations
import argparse
import ctypes
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from android_location_test import circle_position, location_command, find_adb
from location_tools import distance_meters, parse_gps_location
from login_support import is_login_form, login, load_credentials
from desktop_support import emulator_launch

BEIJING = timezone(timedelta(hours=8))
PACKAGE = 'cn.edu.aiit.axx'
SPORT = 'SportMapActivity'
STOP = threading.Event()
ADB_PATH = None
MAX_PREPARATION_ATTEMPTS = 3


class NavigationTimeout(RuntimeError):
    """The start page did not load; no run-start action was performed."""


def adb(args, serial=None, timeout=20):
    command = [ADB_PATH or find_adb()]
    if serial:
        command += ['-s', serial]
    result = subprocess.run(command + list(args), capture_output=True, timeout=timeout,
                            encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    output = (result.stdout + '\n' + result.stderr).strip()
    if result.returncode:
        raise RuntimeError(output or f'ADB 退出码 {result.returncode}')
    return output


def current_location(serial):
    return parse_gps_location(adb(['shell', 'dumpsys', 'location'], serial))


def send_location(serial, lat, lon, speed_kmh=0):
    if not serial or not serial.startswith('emulator-'):
        raise RuntimeError('晨跑脚本只控制官方 Android Emulator。')
    output = adb(location_command(lat, lon, speed_kmh), serial)
    if 'KO:' in output or 'error' in output.lower():
        raise RuntimeError(output)


def now():
    return datetime.now(BEIJING)


def window(config, when):
    def at(value):
        hour, minute = map(int, value.split(':'))
        return when.replace(hour=hour, minute=minute, second=0, microsecond=0)
    start, end = at(config['start']), at(config['end'])
    if not 0 < (end - start).total_seconds() <= 3600:
        raise ValueError('每日起止时间必须在同一天，且时长不超过 1 小时。')
    return start, end


def validate(config, *, auto_origin=False):
    window(config, now())
    if not isinstance(config.get('headless', False), bool):
        raise ValueError('headless 必须为 true 或 false。')
    for key, low, high in [('speed_kmh', .5, 30), ('radius_m', 10, 10000),
                           ('max_origin_gap_m', 10, 5000)]:
        value = float(config[key])
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f'{key} 无效。')
    if not auto_origin:
        lat, lon = config['anchor_wgs84']
        if not math.isfinite(lat + lon) or not (-85 <= lat <= 85 and -180 <= lon <= 180):
            raise ValueError('起点区域坐标无效。')
    return config


def nodes(xml):
    return list(ET.fromstring(xml).iter('node'))


def clickable_entry(items, text):
    """Resolve a label to its nearest clickable ancestor in the UI tree."""
    label = find(items, text=text)
    if label is None:
        return None
    parents = {child: parent for parent in items for child in parent}
    candidate = label
    while candidate is not None:
        if candidate.get('clickable') == 'true' and candidate.get('enabled', 'true') == 'true':
            center(candidate)
            return candidate
        candidate = parents.get(candidate)
    return None


def find(items, *, text=None, rid=None):
    return next((n for n in items if n.get('enabled', 'true') == 'true' and
                 (text is None or n.get('text') == text) and
                 (rid is None or n.get('resource-id') == PACKAGE + ':id/' + rid)), None)


def center(node):
    values = list(map(int, re.findall(r'\d+', node.get('bounds', ''))))
    if len(values) != 4 or values[2] <= values[0] or values[3] <= values[1]:
        raise RuntimeError('控件没有有效可见坐标。')
    return (values[0] + values[2]) // 2, (values[1] + values[3]) // 2


def is_abnormal(items):
    return any('当前位置数据异常' in n.get('text', '') for n in items)


class Runner:
    def __init__(self, config, directory):
        self.c, self.directory = config, directory
        directory.mkdir(parents=True, exist_ok=True)
        self.serial = None
        self.started = False
        self.last_position = None
        self.login_refresh_attempted = False
        self.lock = threading.Lock()

    def log(self, message):
        line = f'{now().isoformat(timespec="seconds")} {message}'
        with self.lock:
            with (self.directory / 'run.log').open('a', encoding='utf-8') as f:
                f.write(line + '\n')
        print(line, flush=True)

    def call(self, args, timeout=20):
        return adb(args, self.serial, timeout=timeout)

    def activity(self):
        output = self.call(['shell', 'dumpsys', 'activity', 'activities'])
        return next((line for line in output.splitlines() if 'mResumedActivity' in line), '')

    def ui(self):
        path = '/data/local/tmp/virtualrun-morning-ui.xml'
        self.call(['shell', 'uiautomator', 'dump', path], timeout=15)
        xml = self.call(['shell', 'cat', path])
        items = nodes(xml)
        saved = ET.fromstring(xml)
        for node in saved.iter('node'):
            if node.get('password') == 'true' or node.get('resource-id') in (
                    PACKAGE + ':id/psw_et', PACKAGE + ':id/name_et'):
                node.set('text', '')
        (self.directory / 'latest-ui.xml').write_text(ET.tostring(saved, encoding='unicode'), encoding='utf-8')
        return items

    def tap(self, node, hold_ms=0):
        x, y = center(node)
        label = node.get('text') or node.get('resource-id') or '已识别控件'
        self.log(f'{"长按" if hold_ms else "点击"}：{label}')
        if hold_ms:
            self.call(['shell', 'input', 'swipe', str(x), str(y), str(x), str(y), str(hold_ms)], timeout=15)
        else:
            self.call(['shell', 'input', 'tap', str(x), str(y)])

    def select_emulator(self, start=False, stop_event=None):
        cancelled = stop_event if stop_event is not None else STOP
        if cancelled.is_set():
            raise RuntimeError('用户停止了任务。')
        def inventory():
            output = adb(['devices'])
            return [line.split()[0] for line in output.splitlines()
                    if line.startswith('emulator-') and line.split()[1:2] == ['device']]
        found = inventory()
        def choose(devices):
            for serial in devices:
                name = adb(['emu', 'avd', 'name'], serial).splitlines()[0].strip()
                if name == self.c['avd']:
                    return serial
            return None
        self.serial = choose(found)
        if not self.serial and start:
            executable = Path(self.c['sdk']) / 'emulator/emulator.exe'
            if not executable.is_file():
                raise RuntimeError('没有找到官方 Android Emulator。')
            command, env = emulator_launch(self.c['sdk'], self.c['avd'], self.c.get('headless', False))
            self.log('正在无窗口启动指定模拟器。' if self.c.get('headless') else '正在启动指定模拟器窗口。')
            subprocess.Popen(command,
                             env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            until = time.monotonic() + 180
            while not self.serial and time.monotonic() < until and not cancelled.wait(2):
                self.serial = choose(inventory())
        if not self.serial:
            raise RuntimeError('指定的官方模拟器未连接；脚本不会选择 USB 手机。')
        until = time.monotonic() + 180
        while self.call(['shell', 'getprop', 'sys.boot_completed']).strip() != '1':
            if time.monotonic() >= until or cancelled.wait(2):
                raise RuntimeError('等待模拟器开机超时或已停止。')

    def preflight(self):
        package = self.call(['shell', 'dumpsys', 'package', PACKAGE])
        if 'versionName=2.0.5' not in package or 'primaryCpuAbi=x86\n' not in package + '\n':
            raise RuntimeError('自动操作仅校验过安小信 2.0.5 的 x86 安装；请使用电脑版安装按钮安装原 APK。')
        if self.call(['shell', 'settings', 'get', 'secure', 'location_mode']).strip() == '0':
            raise RuntimeError('模拟器系统定位已关闭。')
        if SPORT in self.activity() or SPORT in self.call(['shell', 'dumpsys', 'activity', 'activities']):
            raise RuntimeError('安小信已有打开的跑步记录，先手动结束；脚本不会接管旧记录。')
        location = self.call(['shell', 'dumpsys', 'location'])
        if re.search(r'(?:gps|network) provider \[mock\]', location):
            raise RuntimeError('安卓版正在占用模拟器定位，请先点击其“恢复真实定位”。')
        try:
            origin = current_location(self.serial)
        except RuntimeError:
            if self.c.get('auto_origin'):
                self.log('当前 GPS 尚不可读；进入运动页后自动读取，不使用默认坐标。')
                return None
            origin = tuple(self.c['anchor_wgs84'])
            self.log('模拟器尚无 GPS，计划在新记录开始前静止准备已确认的区域起点。')
        if self.c.get('auto_origin'):
            self.log('已自动读取模拟器当前起点。')
            return origin
        gap = distance_meters(origin, self.c['anchor_wgs84'])
        if gap > self.c['max_origin_gap_m']:
            raise RuntimeError(f'当前起点距已确认区域 {gap:.0f} 米，取消自动运行。请先确认模拟器位置。')
        self.log(f'检查通过：{self.serial}，起点 {origin[0]:.8f}, {origin[1]:.8f}')
        return origin

    def close_emulator(self, lock_path):
        """Close only the configured AVD, excluding owned tasks/open records."""
        import msvcrt
        lock_path = Path(lock_path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open('a+b') as mutex:
            mutex.seek(0)
            try:
                msvcrt.locking(mutex.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError('晨跑任务仍在运行，请先停止并确认记录结束。')
            try:
                self.select_emulator(start=False)
                activity = self.call(['shell', 'dumpsys', 'activity', 'activities'])
                if SPORT in activity:
                    raise RuntimeError('安小信仍有打开的跑步页面，请先结束记录并返回首页。')
                self.call(['emu', 'kill'])
                self.log('已关闭指定模拟器；可选择窗口模式后重新启动。')
            finally:
                mutex.seek(0)
                msvcrt.locking(mutex.fileno(), msvcrt.LK_UNLCK, 1)

    def navigate_to_start(self, deadline=None):
        self.call(['shell', 'input', 'keyevent', 'KEYCODE_WAKEUP'])
        activity = self.activity()
        if SPORT in activity or SPORT in self.call(['shell', 'dumpsys', 'activity', 'activities']):
            raise RuntimeError('安小信已有打开的跑步记录；脚本不会接管旧记录。')
        if not any(page in activity for page in ('HomeActivity', 'WebActivity', 'LoginActivity')):
            launched = self.call(['shell', 'am', 'start', '-n', PACKAGE + '/.activity.SplashActivity'])
            if re.search(r'Error:|Permission Denial', launched):
                raise RuntimeError('无法打开安小信：' + launched)
        began = time.monotonic()
        until = began + 90
        hard_until = began + 180
        if deadline is not None:
            hard_until = min(hard_until, began + max(0, (deadline - now()).total_seconds()))
            until = min(until, hard_until)
        backed = False
        login_attempted = False
        attempts = {}
        restarted = False
        first_home_tap = None
        def tap_entry(node, label, alternate=None):
            count, last = attempts.get(label, (0, float('-inf')))
            if count >= 2 or time.monotonic() - last < 20:
                return False
            attempts[label] = (count + 1, time.monotonic())
            self.log('打开入口：' + label)
            # A second attempt touches the label within the same identified
            # control, rather than repeatedly targeting its image/blank area.
            self.tap(alternate if count and alternate is not None else node)
            return True
        while time.monotonic() < until and not STOP.is_set():
            try:
                items = self.ui()
            except (subprocess.TimeoutExpired, ET.ParseError):
                self.log('读取页面暂时超时，等待界面恢复。')
                STOP.wait(2)
                continue
            if is_login_form(items):
                if login_attempted or not self.c.get('auto_login'):
                    raise RuntimeError('需要登录安小信。请在软件“晨跑与登录”中填写账号密码，或在模拟器中手动登录。')
                credentials = load_credentials(self.c['credential_file'])
                login_attempted = True
                login(self, credentials['account'], credentials['password'], STOP)
                del credentials
                # Login may consume most of the initial page budget. Give
                # the authenticated page time to load without overrunning
                # the daily start deadline or this call's hard time limit.
                until = min(hard_until, max(until, time.monotonic() + 90))
                continue
            if is_abnormal(items):
                raise RuntimeError('安小信已显示定位异常。')
            if find(items, text='开始运动') is not None:
                return
            if find(items, rid='sport_run_ll_ll') is not None:
                raise RuntimeError('检测到已有跑步记录；脚本不会接管旧记录。')
            entry = find(items, text='随性跑步')
            if entry is not None:
                parent = clickable_entry(items, '随性跑步')
                tap_entry(parent if parent is not None else entry, '随性跑步', entry)
            elif find(items, text='智慧运动') is not None:
                # Known WebActivity header; inspect the actual hierarchy, no
                # fixed screen coordinates or fallback into an unknown page.
                if find(items, text='课外锻炼进度') is not None:
                    header = find(items, text='智慧运动')
                    _, y = center(header)
                    width = max(int(re.findall(r'\d+', n.get('bounds', '[0,0][0,0]'))[2]) for n in items)
                    menu = ET.Element('node', bounds=f'[{round(width * .90)},{y - 8}][{width},{y + 8}]')
                    tap_entry(menu, '跑步菜单')
                else:
                    entry = clickable_entry(items, '智慧运动')
                    if entry is not None:
                        if tap_entry(entry, '智慧运动', find(items, text='智慧运动')) and first_home_tap is None:
                            first_home_tap = time.monotonic()
                        if first_home_tap is not None and time.monotonic() - first_home_tap >= 40 and not restarted:
                            # Recover an old homepage through the original app
                            # entrance, retaining its normal login/session flow.
                            activity = self.activity()
                            if 'HomeActivity' in activity:
                                if SPORT in self.call(['shell', 'dumpsys', 'activity', 'activities']):
                                    raise RuntimeError('检测到已有跑步记录；取消入口恢复。')
                                refresh_login = self.c.get('auto_login') and not self.login_refresh_attempted and not login_attempted
                                page = 'LoginActivity' if refresh_login else 'SplashActivity'
                                if refresh_login:
                                    # Use the original, exported login form. The
                                    # saved opt-in is required; submit only once.
                                    self.login_refresh_attempted = True
                                    self.log('首页入口长期无响应，按已保存的自动登录设置恢复登录一次。')
                                else:
                                    self.log('首页入口等待后仍未跳转，重新打开安小信入口一次。')
                                result = self.call(['shell', 'am', 'start', '-n', PACKAGE + '/.activity.' + page])
                                if re.search(r'Error:|Permission Denial', result):
                                    raise RuntimeError('无法恢复安小信入口。')
                                restarted = True
                                attempts.pop('智慧运动', None)
                    else:
                        # A WebActivity title is not a navigation button.
                        if not attempts.get('loading'):
                            self.log('智慧运动页面正在加载，等待跑步入口；不点击页面标题。')
                            attempts['loading'] = (1, time.monotonic())
            elif find(items, text='结束运动') is not None and not backed:
                self.call(['shell', 'input', 'keyevent', 'KEYCODE_BACK'])
                backed = True
            STOP.wait(1)
        if STOP.is_set():
            raise RuntimeError('用户停止了任务。')
        raise NavigationTimeout('等待跑步入口超时；未点击开始运动。请检查登录状态、服务入口或网络。')

    def read_auto_origin(self):
        until = time.monotonic() + 25
        while time.monotonic() < until and not STOP.is_set():
            try:
                origin = current_location(self.serial)
                if not (-85 <= origin[0] <= 85 and -180 <= origin[1] <= 180):
                    raise RuntimeError('起点超出可用范围。')
                self.log('已自动取得当前 GPS 起点。')
                return origin
            except RuntimeError:
                STOP.wait(1)
        raise RuntimeError('用户停止了任务。' if STOP.is_set() else '未取得有效系统 GPS，请检查安小信定位权限后重试。')

    def prepare(self, origin):
        self.last_position = origin
        until = time.monotonic() + 15
        while time.monotonic() < until and not STOP.is_set():
            send_location(self.serial, *origin, 0)
            STOP.wait(1)
        if STOP.is_set():
            raise RuntimeError('用户停止了任务。')
        try:
            position = current_location(self.serial)
        except RuntimeError:
            self.log('开始页尚未请求 GPS；点击开始后继续发送静止坐标并确认定位。')
        else:
            if distance_meters(position, origin) > 15:
                raise RuntimeError('静止准备后 GPS 未对齐起点。')
        self.log('起点准备完成；运动页已开启，接下来发送轨迹。' if self.started
                 else '起点准备完成；尚未点击开始运动。')

    def start(self):
        items = self.ui()
        button = find(items, text='开始运动')
        if button is None:
            raise RuntimeError('开始按钮不在当前页面，取消启动。')
        # Set ownership before checking the ensuing activity: a slow UI or a
        # callback failure must still enter the cleanup/finish path.
        self.started = True
        self.tap(button)
        until = time.monotonic() + 35
        while time.monotonic() < until and not STOP.is_set():
            if self.last_position:
                send_location(self.serial, *self.last_position, 0)
            items = self.ui()
            if self.dismiss_battery_prompt(items):
                continue
            if is_abnormal(items):
                raise RuntimeError('安小信拒绝模拟定位。')
            if find(items, rid='sport_run_ll_ll') is not None:
                if self.last_position:
                    try:
                        position = current_location(self.serial)
                    except RuntimeError:
                        STOP.wait(1)
                        continue
                    if distance_meters(position, self.last_position) > 15:
                        raise RuntimeError('开始运动后 GPS 未对齐起点。')
                self.log('安小信已进入运动中界面。')
                return
            STOP.wait(1)
        raise RuntimeError('点击开始后未确认运动中界面。')

    def dismiss_battery_prompt(self, items):
        # This known Android first-run prompt covers the timer. Dismiss only
        # its negative button, without enabling a system battery exemption.
        button = next((n for n in items if n.get('resource-id') == 'android:id/button2'
                       and n.get('enabled', 'true') == 'true'), None)
        if button is None:
            return False
        if 'com.android.settings/.fuelgauge.RequestIgnoreBatteryOptimizations' not in self.activity():
            return False
        self.tap(button)
        self.log('已关闭首次电池优化提示，继续确认运动界面。')
        return True

    def finish(self):
        until = time.monotonic() + 65
        held = False
        closed_abnormal = False
        closed_short = False
        while time.monotonic() < until:
            items = self.ui()
            if self.dismiss_battery_prompt(items):
                continue
            close = find(items, text='关闭跑步')
            if close is not None and is_abnormal(items):
                self.tap(close)
                closed_abnormal = True
            elif find(items, rid='sport_run_ll_ll') is not None:
                self.tap(find(items, rid='sport_run_ll_ll'))  # pause first
            elif find(items, rid='sport_stop_ll_completedview') is not None and not held:
                self.tap(find(items, rid='sport_stop_ll_completedview'), hold_ms=4500)
                held = True
            elif find(items, text='结束') is not None and any('无法保存记录' in n.get('text', '') for n in items):
                self.tap(find(items, text='结束'))
                closed_short = True
            elif find(items, text='结束运动') is not None:
                self.started = False
                self.log('已确认安小信显示结束运动结果页。')
                return
            elif find(items, rid='sport_lock_ll_completedview') is not None:
                self.tap(find(items, rid='sport_lock_ll_completedview'), hold_ms=4500)
            elif (closed_abnormal or closed_short) and SPORT not in self.activity():
                self.started = False
                self.log('已关闭本条记录；未确认保存为有效运动。')
                return
            time.sleep(1)
        raise RuntimeError('未确认安小信结束结果，请人工检查；任务不会记为成功。')

    def capture(self):
        if not self.serial:
            return
        for filename, args in [('location.txt', ['shell', 'dumpsys', 'location']),
                               ('activity.txt', ['shell', 'dumpsys', 'activity', 'activities']),
                               ('sport-logcat.txt', ['logcat', '-d', '-v', 'threadtime', 'sport:E', 'SportMapActivity:E', 'AndroidRuntime:E', '*:S'])]:
            try:
                (self.directory / filename).write_text(self.call(args), encoding='utf-8')
            except Exception as exc:
                self.log(f'保存 {filename} 失败：{exc}')
        try:
            command = [str(Path(self.c['sdk']) / 'platform-tools/adb.exe'), '-s', self.serial, 'exec-out', 'screencap', '-p']
            result = subprocess.run(command, capture_output=True, timeout=15, check=True,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            (self.directory / 'result.png').write_bytes(result.stdout)
        except Exception as exc:
            self.log(f'保存截图失败：{exc}')

    def run_route(self, origin, end):
        failure = []
        finished = threading.Event()
        def monitor():
            while not finished.wait(10):
                try:
                    items = self.ui()
                    if is_abnormal(items):
                        raise RuntimeError('运行中出现位置异常。')
                    if find(items, rid='sport_run_ll_ll') is None:
                        raise RuntimeError('运动界面已离开或被暂停。')
                except Exception as exc:
                    failure.append(exc)
                    return
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        started = time.monotonic()
        try:
            with (self.directory / 'track.jsonl').open('w', encoding='utf-8') as track:
                while now() < end and not STOP.is_set():
                    if failure:
                        raise failure[0]
                    elapsed = time.monotonic() - started
                    distance = elapsed * self.c['speed_kmh'] / 3.6
                    lat, lon, east, north = circle_position(*origin, self.c['radius_m'], distance)
                    self.last_position = lat, lon
                    send_location(self.serial, lat, lon, self.c['speed_kmh'])
                    track.write(json.dumps({'time': now().isoformat(), 'elapsed': elapsed,
                                            'distance': distance, 'lat': lat, 'lon': lon}, ensure_ascii=False) + '\n')
                    track.flush()
                    if self.c.get('auto_origin'):
                        print('@virtualrun:' + json.dumps({'elapsed': elapsed, 'distance': distance,
                              'lat': lat, 'lon': lon, 'east': east, 'north': north,
                              'radius': self.c['radius_m']}, ensure_ascii=False), flush=True)
                    STOP.wait(min(1, max(0, (end - now()).total_seconds())))
        finally:
            finished.set()
            # Reset speed at the deadline, before waiting for a pending UI dump.
            if self.last_position:
                send_location(self.serial, *self.last_position, 0)
            thread.join(timeout=40)


def retryable_daily_state(state):
    """Only a known navigation failure before any start click may be retried."""
    if state.get('status') != 'failed' or any(state.get(key) for key in ('start_attempted', 'start_confirmed', 'started_at')):
        return False
    if int(state.get('preparation_attempts', 0)) >= MAX_PREPARATION_ATTEMPTS:
        return False
    if state.get('retryable_before_start') is True:
        return True
    # Migrate the specific pre-start failure written by 1.4.4; never infer
    # safety merely from a missing confirmation field in older state files.
    error = state.get('error', '')
    return error.startswith('点击“智慧运动”后页面未变化') or error.startswith('无法识别跑步入口。')


def prepare_navigation(runner, state, save, start=None):
    """Retry navigation under the shared lock, before any location/start action."""
    deadline = start + timedelta(seconds=60) if start is not None else None
    maximum = MAX_PREPARATION_ATTEMPTS if start is not None else 1
    while True:
        if STOP.is_set():
            raise RuntimeError('用户取消。')
        if deadline is not None and now() >= deadline:
            raise NavigationTimeout('准备窗口已结束；未开始记录，不补跑。')
        state['preparation_attempts'] = int(state.get('preparation_attempts', 0)) + 1
        state['retryable_before_start'] = False
        save()
        runner.select_emulator(start=True)
        origin = runner.preflight()
        try:
            if deadline is None:
                runner.navigate_to_start()
            else:
                runner.navigate_to_start(deadline=deadline)
            return origin
        except NavigationTimeout as exc:
            if runner.started or state.get('start_attempted') or state['preparation_attempts'] >= maximum:
                raise
            if STOP.is_set() or (deadline is not None and now() + timedelta(seconds=10) >= deadline):
                raise
            state.setdefault('preparation_errors', []).append(str(exc))
            state['retryable_before_start'] = True
            save()
            runner.log(f'入口尚未就绪，10 秒后重试准备（{state["preparation_attempts"]}/{maximum}）；尚未创建记录。')
            STOP.wait(10)


def execute_session(runner, state_path, state, *, start=None, end=None, trial_seconds=None):
    """Use the same preparation, UI start, route and cleanup for both modes."""
    def save():
        temporary = state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(state_path)

    state.update(start_confirmed=False, finish_confirmed=False)
    state.setdefault('start_attempted', False)
    save()
    awake = ctypes.windll.kernel32
    awake.SetThreadExecutionState(0x80000003)
    try:
        origin = prepare_navigation(runner, state, save, start)
        early_start = runner.c.get('auto_origin') is True and origin is None
        if early_start:
            # Starting the native page may be what activates its GPS listener.
            # No coordinates are injected until a real emulator fix is read.
            state['start_attempted'] = True
            save()
            runner.start()
            state.update(start_confirmed=True, started_at=now().isoformat())
            save()
            origin = runner.read_auto_origin()
        elif runner.c.get('auto_origin') is True:
            # Read again after navigation; use the freshest fix available.
            origin = runner.read_auto_origin()
        if runner.c.get('auto_origin') is True:
            state['origin_wgs84'] = origin
        runner.prepare(origin)
        if start is not None:
            runner.log('起点准备完成，等待当天 07:00。')
            while now() < start and not STOP.is_set():
                STOP.wait(min(1, (start - now()).total_seconds()))
            if now() > start + timedelta(seconds=60):
                raise RuntimeError('准备超时，取消迟到的晨跑。')
        if STOP.is_set():
            raise RuntimeError('用户取消。')
        if not early_start:
            # Persist intent before the tap: a crash or an unconfirmed start
            # must never make this day's record eligible for another start.
            state['start_attempted'] = True
            save()
            runner.start()
        state.update(status='running', start_confirmed=True)
        state.setdefault('started_at', now().isoformat())
        if trial_seconds is not None:
            end = now() + timedelta(seconds=trial_seconds)
        save()
        runner.run_route(origin, end)
        state['status'] = 'cancelled' if STOP.is_set() else 'finished'
    except Exception as exc:
        state['status'], state['error'] = ('cancelled' if STOP.is_set() else 'failed'), str(exc)
        state['retryable_before_start'] = (isinstance(exc, NavigationTimeout) and not STOP.is_set()
                                         and not state['start_attempted'] and not runner.started)
        runner.log(f'{"任务已取消" if STOP.is_set() else "任务失败"}：{exc}')
    finally:
        if runner.last_position:
            try:
                send_location(runner.serial, *runner.last_position, 0)
            except Exception as exc:
                state['status'], state['stop_error'] = 'failed', str(exc)
        if runner.started:
            try:
                runner.finish()
                state['finish_confirmed'] = not runner.started
            except Exception as exc:
                state['status'], state['finish_error'] = 'failed', str(exc)
                runner.log(f'结束未确认：{exc}')
        runner.capture()
        state['completed_at'] = now().isoformat()
        save()
        awake.SetThreadExecutionState(0x80000000)
        runner.log('结果报告：' + str(state_path))
    return 1 if state['status'] == 'failed' else 0


def main(argv=None):
    global ADB_PATH
    parser = argparse.ArgumentParser(description='安小信模拟器每日晨跑定时器')
    parser.add_argument('--config', required=True)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--check', action='store_true', help='只读检查，不点击按钮或发送位置')
    modes.add_argument('--scheduled', action='store_true', help='由每日计划任务调用')
    modes.add_argument('--trial-seconds', type=int, help='立即短时试跑 1–300 秒，不影响当天定时状态')
    modes.add_argument('--test-seconds', type=int, help='自动读取起点并测试 1–3600 秒，不修改每日任务')
    modes.add_argument('--navigate-only', action='store_true', help='仅验证进入开始运动页，不开始记录、不发送位置')
    parser.add_argument('--stop-file', help='界面使用的停止请求文件；结束自己创建的记录后退出')
    args = parser.parse_args(argv)
    if args.trial_seconds is not None and not 1 <= args.trial_seconds <= 300:
        parser.error('试跑时长为 1–300 秒。')
    if args.test_seconds is not None and not 1 <= args.test_seconds <= 3600:
        parser.error('测试时长为 1–3600 秒。')
    STOP.clear()
    path = Path(args.config).resolve()
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    config['auto_origin'] = args.test_seconds is not None or args.navigate_only
    config = validate(config, auto_origin=config['auto_origin'])
    ADB_PATH = str(Path(config['sdk']) / 'platform-tools/adb.exe')
    os.environ['ANDROID_HOME'] = config['sdk']
    os.environ['ANDROID_SDK_ROOT'] = config['sdk']
    directory = path.parent / 'logs' / now().strftime('%Y-%m-%d_%H%M%S_%f')
    runner = Runner(config, directory)
    from stop_support import StopWatcher, disabled
    if not args.check and disabled(path.parent):
        runner.log('一键停用已生效；请在软件中重新开始操作或启用每日晨跑。')
        return 0
    with StopWatcher(path.parent, STOP, args.stop_file):
        return run_mode(args, path, config, directory, runner)


def run_mode(args, path, config, directory, runner):
    if args.check:
        try:
            runner.select_emulator()
            runner.preflight()
            if is_login_form(runner.ui()):
                if not config.get('auto_login'):
                    raise RuntimeError('安小信当前未登录。请点击“登录安小信”，或保存晨跑自动登录信息。')
                load_credentials(config['credential_file'])
                runner.log('安小信当前未登录，已保存本机加密凭据；晨跑启动时将尝试登录。')
            runner.log('只读检查完成；未启动记录、未发送位置。')
            return 0
        except Exception as exc:
            runner.log(f'检查未通过：{exc}')
            return 1
    start, end = window(config, now())
    if args.scheduled and (now() > start + timedelta(seconds=60) or now() < start - timedelta(minutes=10)):
        runner.log('不在当天准备/启动窗口，跳过；不补跑过期任务。')
        return 0
    state_path = path.parent / 'daily-state.json' if args.scheduled else directory / 'trial-result.json'
    mutex_path = path.parent / 'morning.lock'
    with mutex_path.open('a+b') as mutex:
        import msvcrt
        mutex.seek(0)
        try:
            msvcrt.locking(mutex.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            runner.log('已有晨跑任务运行，本次跳过。')
            return 0
        state = json.loads(state_path.read_text(encoding='utf-8')) if args.scheduled and state_path.exists() else {}
        day = start.date().isoformat()
        if getattr(args, 'navigate_only', False):
            try:
                runner.select_emulator(start=True)
                runner.preflight()
                runner.navigate_to_start()
                runner.log('导航验证通过：已进入开始运动页；未开始记录、未发送位置。')
                return 0
            except Exception as exc:
                runner.log('导航验证失败：' + str(exc))
                return 1
            finally:
                runner.capture()
        if args.scheduled and state.get('date') == day:
            if not retryable_daily_state(state):
                runner.log('今天已尝试开始、被取消或达到准备次数上限，避免重复记录；详情见 daily-state.json。')
                return 0
            runner.log('今天之前仅入口准备失败，允许在启动窗口内继续有限准备。')
            state.setdefault('preparation_errors', []).append(state.pop('error', '入口准备失败'))
            state.pop('completed_at', None)
            state.update(status='preparing', log_directory=str(directory))
        else:
            state = {'date': day, 'mode': 'scheduled' if args.scheduled else 'trial',
                 'status': 'preparing', 'log_directory': str(directory),
                 'speed_kmh': config['speed_kmh'], 'radius_m': config['radius_m']}
        if args.scheduled:
            return execute_session(runner, state_path, state, start=start, end=end)
        test_seconds = getattr(args, 'test_seconds', None)
        seconds = test_seconds if test_seconds is not None else args.trial_seconds
        state['trial_seconds'] = seconds
        state['auto_origin'] = config.get('auto_origin', False)
        return execute_session(runner, state_path, state, trial_seconds=seconds)


if __name__ == '__main__':
    signal.signal(signal.SIGINT, lambda *_: STOP.set())
    signal.signal(signal.SIGTERM, lambda *_: STOP.set())
    raise SystemExit(main())
