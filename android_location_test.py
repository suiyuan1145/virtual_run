"""Windows desktop test console for the official Android Emulator. Python stdlib only."""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import uuid
from location_tools import (align_app_location, distance_meters, parse_app_location,
                            parse_gps_location)
from desktop_support import (APP_DIR, DATA_DIR, RESOURCE_DIR, VERSION, bundled_adb,
                             default_apk, load_settings, read_document,
                             save_settings, sdk_directory, emulator_launch)

ROOT = Path(__file__).resolve().parent
PACKAGE = 'cn.edu.aiit.axx'
APK = default_apk()
EARTH_RADIUS = 6371000.0


def find_adb():
    candidates = [str(sdk_directory() / 'platform-tools/adb.exe')]
    for key in ('ANDROID_HOME', 'ANDROID_SDK_ROOT'):
        if os.environ.get(key):
            candidates.append(str(Path(os.environ[key]) / 'platform-tools' / 'adb.exe'))
    candidates.extend([
        str(Path.home() / 'AppData/Local/Android/Sdk/platform-tools/adb.exe'),
        shutil.which('adb'),
        r'C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe',
    ])
    for item in candidates:
        if item and Path(item).is_file():
            return str(item)
    included = bundled_adb()
    if included:
        return included
    raise RuntimeError('没有找到 adb，请安装 Android SDK Platform Tools。')


def adb(args, serial=None, timeout=20):
    command = [find_adb()]
    if serial:
        command += ['-s', serial]
    command += list(args)
    result = subprocess.run(command, capture_output=True, timeout=timeout,
                            encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    output = (result.stdout + '\n' + result.stderr).strip()
    if result.returncode:
        raise RuntimeError(output or f'adb 退出码 {result.returncode}')
    return output


def circle_position(lat, lon, radius, distance):
    # Start on the south side of a circle around a center north of the start.
    a = distance / radius
    east, north = radius * math.sin(a), radius * (1 - math.cos(a))
    bearing = math.atan2(east, north)
    angle = math.hypot(east, north) / EARTH_RADIUS
    phi, lam = math.radians(lat), math.radians(lon)
    p = math.asin(math.sin(phi) * math.cos(angle) +
                  math.cos(phi) * math.sin(angle) * math.cos(bearing))
    l = lam + math.atan2(math.sin(bearing) * math.sin(angle) * math.cos(phi),
                         math.cos(angle) - math.sin(phi) * math.sin(p))
    return math.degrees(p), (math.degrees(l) + 540) % 360 - 180, east, north


def current_location(serial):
    return parse_gps_location(adb(['shell', 'dumpsys', 'location'], serial))


def app_location(serial):
    pids = adb(['shell', 'pidof', PACKAGE], serial).split()
    if not pids or not pids[0].isdigit():
        raise RuntimeError('安小信尚未运行。')
    pid = pids[0]
    text = adb(['logcat', '-d', '-t', '2000', '--pid=' + pid, '-v', 'threadtime,epoch', 'sport:E', '*:S'], serial)
    device_time = float(adb(['shell', 'date', '+%s'], serial).strip())
    lat, lon, _ = parse_app_location(text, device_time)
    try:
        gps = current_location(serial)
    except RuntimeError:
        gps = None
    p, q, source = align_app_location(lat, lon, gps)
    return p, q, f'安小信原坐标 {lat:.6f}, {lon:.6f}；{source}'


def location_command(lat, lon, speed_kmh=0):
    if not all(math.isfinite(v) for v in (lat, lon, speed_kmh)) or not (
            -90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= speed_kmh <= 30):
        raise ValueError('定位坐标或速度无效。')
    # geo fix velocity is in knots; 1 knot = 1.852 km/h.
    return ['emu', 'geo', 'fix', f'{lon:.8f}', f'{lat:.8f}', '0', '8', f'{speed_kmh / 1.852:.6f}']


def send_location(serial, lat, lon, speed_kmh=0):
    if not serial.startswith('emulator-'):
        raise RuntimeError('此工具使用官方 Android Emulator 的 geo fix。第三方模拟器请使用其官方定位控制功能。')
    # Emulator console expects longitude FIRST, then latitude (WGS84).
    output = adb(location_command(lat, lon, speed_kmh), serial)
    if 'KO:' in output or 'error' in output.lower():
        raise RuntimeError(output)
    return output


def self_test():
    p = circle_position(31.0, 121.0, 150, 0)
    assert abs(p[0] - 31) < 1e-10 and abs(p[1] - 121) < 1e-10
    closed = circle_position(31, 121, 150, 2 * math.pi * 150)
    assert abs(closed[0] - 31) < 1e-10 and abs(closed[1] - 121) < 1e-10
    quarter = circle_position(31, 121, 150, math.pi * 150 / 2)
    assert quarter[0] > 31 and quarter[1] > 121
    for distance in range(0, 1000, 5):
        p = circle_position(31, 179.999, 150, distance)
        assert -90 <= p[0] <= 90 and -180 <= p[1] <= 180
    print('PASS: route start, direction, closed loop and longitude wrapping')


def gui(smoke_output=None):
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    app = tk.Tk()
    app.title(f'虚拟跑步 {VERSION} · 电脑版晨跑')
    app.geometry('1120x850')
    app.minsize(1020, 760)
    if (RESOURCE_DIR / 'assets/app.ico').is_file():
        app.iconbitmap(str(RESOURCE_DIR / 'assets/app.ico'))
    style = ttk.Style(app)
    style.theme_use('clam')
    style.configure('.', font=('Microsoft YaHei', 10))
    style.configure('TNotebook.Tab', padding=(18, 9))
    style.configure('TButton', padding=(9, 5))
    style.configure('TNotebook', background='#f2f5f8')
    notebook = ttk.Notebook(app)
    notebook.pack(fill='both', expand=True, padx=12, pady=12)
    events = queue.Queue()
    stop = threading.Event()
    busy = False
    disabling = False
    worker_thread = None
    samples = []
    prepared = None
    route_key = None
    device = tk.StringVar()
    settings = load_settings()
    frame = ttk.Frame(notebook, padding=14)
    notebook.add(frame, text='跑步测试')
    ttk.Label(frame, text='虚拟跑步', font=('Microsoft YaHei', 22, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='读取安小信位置 → 准备起点 → 在安小信开始新测试 → 开始环形定位。准备起点时请勿记录跑步。').pack(anchor='w', pady=8)
    top = ttk.Frame(frame)
    top.pack(fill='x', pady=5)
    combo = ttk.Combobox(top, textvariable=device, state='readonly', width=35)
    combo.pack(side='left', padx=(0, 8))
    controls = []

    def log(text):
        output.insert('end', text + '\n')
        output.see('end')

    def work(fn, error_kind='log'):
        nonlocal busy, worker_thread
        if busy or disabling:
            messagebox.showinfo('任务进行中', '请等待当前任务完成，或停止定位测试。')
            return
        busy = True
        stop.clear()
        for button in controls:
            button.configure(state='disabled')
        combo.configure(state='disabled')
        def run():
            try:
                fn()
            except Exception as exc:
                events.put((error_kind, '失败：' + str(exc)))
            finally:
                events.put(('done', None))
        worker_thread = threading.Thread(target=run, daemon=True)
        worker_thread.start()

    def add_button(parent, label, fn):
        b = ttk.Button(parent, text=label, command=fn)
        b.pack(side='left', padx=3)
        controls.append(b)
        return b

    def refresh():
        def task():
            result = adb(['devices', '-l'])
            found = [line.split()[0] for line in result.splitlines()
                     if len(line.split()) >= 2 and line.split()[1] == 'device']
            events.put(('devices', found))
            events.put(('log', result if found else '没有可用设备。请先启动安卓模拟器；未授权或离线设备不能测试。\n' + result))
            if found:
                serial = next((x for x in found if x.startswith('emulator-')), found[0])
                try:
                    lat, lon, source = app_location(serial)
                    events.put(('origin', (serial, lat, lon, source)))
                except RuntimeError as exc:
                    events.put(('log', str(exc) + '\n请进入智慧运动地图页，再点击“读取安小信位置”。'))
        work(task)

    def start_emulator():
        sdk = sdk_var.get().strip()
        executable = Path(sdk) / 'emulator/emulator.exe'
        if not executable.is_file():
            raise RuntimeError('没有找到官方模拟器。请进入“首次安装”自动下载安装，或在“设置”页选择已有 SDK。')
        avd = avd_var.get().strip()
        headless = headless_var.get()
        def task():
            available = subprocess.run([str(executable), '-list-avds'], capture_output=True,
                                       encoding='utf-8', errors='replace', timeout=15,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if avd not in available.stdout.splitlines():
                raise RuntimeError(f'没有找到虚拟设备 {avd}。请在“设置”中选择已有的 AVD 名称。')
            devices = adb(['devices'])
            for line in devices.splitlines():
                parts = line.split()
                if len(parts) > 1 and parts[0].startswith('emulator-') and parts[1] == 'device':
                    name = adb(['emu', 'avd', 'name'], parts[0]).splitlines()[0].strip()
                    if name == avd:
                        events.put(('log', f'{avd} 已启动，点击“刷新设备”即可连接。'))
                        return
            command, environment = emulator_launch(sdk, avd, headless)
            subprocess.Popen(command,
                             env=environment, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            events.put(('log', '模拟器已在后台启动；等待开机后点击“刷新设备”。' if headless else '模拟器已启动。等待安卓桌面出现，再点击“刷新设备”。'))
        work(task)

    add_button(top, '启动模拟器', lambda: action(start_emulator))
    add_button(top, '刷新设备', refresh)

    def selected():
        serial = device.get()
        if not serial:
            raise RuntimeError('请先启动模拟器并刷新设备。')
        return serial

    def install():
        serial = selected()
        chosen = str(APK) if APK.is_file() else filedialog.askopenfilename(title='选择安小信 APK', filetypes=[('APK', '*.apk *.1'), ('所有文件', '*.*')])
        if not chosen:
            return
        def task():
            args = ['install', '-r']
            if serial.startswith('emulator-'):
                abis = adb(['shell', 'getprop', 'ro.product.cpu.abilist'], serial).strip().split(',')
                if 'x86' in abis:
                    args += ['--abi', 'x86']
                    events.put(('log', '为安小信选择 32 位 x86 兼容模式，避免其部分原生库的 64 位加载错误。'))
            events.put(('log', adb(args + [chosen], serial, timeout=180)))
        work(task)

    def launch():
        serial = selected()
        work(lambda: events.put(('log', adb(['shell', 'am', 'start', '-a', 'android.intent.action.MAIN',
                                           '-c', 'android.intent.category.LAUNCHER', '-n',
                                           PACKAGE + '/.activity.SplashActivity'], serial))))

    def action(fn):
        try:
            fn()
        except Exception as exc:
            messagebox.showerror('无法开始', str(exc))

    add_button(top, '安装原版 APK', lambda: action(install))
    add_button(top, '打开安小信', lambda: action(launch))
    form = ttk.Frame(frame)
    form.pack(fill='x', pady=12)
    fields = {}
    for col, (key, label, value) in enumerate([
        ('lat', '纬度 WGS84', ''), ('lon', '经度 WGS84', ''),
        ('radius', '路线半径 / 米', '150'), ('speed', '速度 / km/h', '6'), ('seconds', '测试时长 / 秒', '30')]):
        group = ttk.Frame(form)
        group.grid(row=0, column=col, padx=6, sticky='ew')
        form.columnconfigure(col, weight=1)
        ttk.Label(group, text=label).pack(anchor='w')
        v = tk.StringVar(value=str(settings.get(key, value)) if key in ('radius', 'speed', 'seconds') else value)
        ttk.Entry(group, textvariable=v, width=14).pack(fill='x')
        fields[key] = v

    def config():
        if not fields['lat'].get().strip() or not fields['lon'].get().strip():
            raise ValueError('请先点击“读取安小信位置”，或读取系统 GPS / 手动填写起点。')
        c = {k: float(v.get()) for k, v in fields.items()}
        if not all(math.isfinite(v) for v in c.values()):
            raise ValueError('参数必须是有限数字。')
        if not (-85 <= c['lat'] <= 85 and -180 <= c['lon'] <= 180 and
                10 <= c['radius'] <= 10000 and .5 <= c['speed'] <= 30 and 1 <= c['seconds'] <= 3600):
            raise ValueError('纬度 ±85，经度 ±180；半径 10–10000 米，速度 0.5–30 km/h，时长 1–3600 秒。')
        return c

    buttons = ttk.Frame(frame)
    origin_buttons = ttk.Frame(frame)
    origin_buttons.pack(fill='x', pady=(0, 8))
    ready_text = tk.StringVar(value='尚未读取和准备起点')
    ttk.Label(origin_buttons, textvariable=ready_text).pack(side='right')
    buttons.pack(fill='x')

    def invalidate_origin(*_):
        nonlocal prepared
        prepared = None
        ready_text.set('起点未准备；请先准备起点')

    device.trace_add('write', invalidate_origin)
    fields['lat'].trace_add('write', invalidate_origin)
    fields['lon'].trace_add('write', invalidate_origin)

    def signature(serial, c):
        return (serial, c['lat'], c['lon'])

    def read_origin(from_app=True):
        serial = selected()
        def task():
            if from_app:
                lat, lon, source = app_location(serial)
            else:
                lat, lon = current_location(serial)
                source = '已读取模拟器当前系统 GPS 位置'
            events.put(('origin', (serial, lat, lon, source)))
        work(task)

    add_button(origin_buttons, '读取安小信位置', lambda: action(read_origin))
    add_button(origin_buttons, '读取系统 GPS', lambda: action(lambda: read_origin(False)))

    def single():
        serial, c = selected(), config()
        invalidate_origin()
        samples.clear()
        paint()
        stop.clear()
        def task():
            events.put(('log', '正在静止准备起点（15 秒），此时请勿在安小信记录跑步。'))
            started = time.monotonic()
            while not stop.is_set():
                send_location(serial, c['lat'], c['lon'], 0)
                if time.monotonic() - started >= 15:
                    gap = distance_meters(current_location(serial), (c['lat'], c['lon']))
                    if gap > 15:
                        raise RuntimeError(f'系统 GPS 仍距起点 {gap:.1f} 米，请重新准备。')
                    events.put(('ready', signature(serial, c)))
                    events.put(('log', '系统 GPS 已稳定在起点。确认安小信地图位置稳定，再开始新的测试记录，然后启动环形定位。'))
                    return
                if stop.wait(1):
                    break
            events.put(('log', '起点准备已取消，请重新准备。'))
        work(task)

    add_button(origin_buttons, '准备起点（15 秒）', lambda: action(single))

    def route():
        nonlocal route_key
        serial, c = selected(), config()
        if not serial.startswith('emulator-'):
            raise RuntimeError('环形测试需要官方 Android Emulator。')
        if prepared != signature(serial, c):
            raise RuntimeError('请先读取位置并准备起点，确认安小信地图稳定后，再开始新的测试记录。')
        key = (*signature(serial, c), c['radius'])
        if samples and key != route_key:
            invalidate_origin()
            raise RuntimeError('路线参数已改变，请重新准备起点并开始新的测试。')
        origin_distance = samples[-1]['distance'] if samples else 0
        origin_elapsed = samples[-1]['elapsed'] if samples else 0
        route_key = key
        stop.clear()
        def task():
            expected = circle_position(c['lat'], c['lon'], c['radius'], origin_distance)[:2]
            gap = distance_meters(current_location(serial), expected)
            if gap > 15:
                events.put(('invalid', None))
                raise RuntimeError(f'当前 GPS 距路线位置 {gap:.1f} 米，已取消启动，避免位置跳变。请重新准备起点。')
            started = time.monotonic()
            events.put(('log', f'以 {c["speed"]:g} km/h 测试，本段 {c["seconds"]:g} 秒；从累计 {origin_distance:.1f} 米继续。'))
            last_position = expected
            try:
                while not stop.is_set():
                    segment_time = min(time.monotonic() - started, c['seconds'])
                    elapsed = origin_elapsed + segment_time
                    distance = origin_distance + segment_time * c['speed'] / 3.6
                    lat, lon, x, y = circle_position(c['lat'], c['lon'], c['radius'], distance)
                    send_location(serial, lat, lon, c['speed'])
                    last_position = (lat, lon)
                    events.put(('sample', {'elapsed': elapsed, 'distance': distance, 'lat': lat, 'lon': lon,
                                           'east': x, 'north': y, 'radius': c['radius'], 'speed_kmh': c['speed']}))
                    if segment_time >= c['seconds']:
                        break
                    stop.wait(min(1, c['seconds'] - segment_time))
            finally:
                send_location(serial, *last_position, 0)
            events.put(('log', '本段定位已结束，当前位置已发送零速度。请在安小信查看轨迹和里程；再次开始会从当前位置继续。'))
        work(task)

    add_button(buttons, '开始环形定位测试', lambda: action(route))
    ttk.Button(buttons, text='停止', command=stop.set).pack(side='left', padx=3)

    def capture():
        serial = selected()
        def task():
            timestamp = time.strftime('%Y%m%d-%H%M%S')
            directory = DATA_DIR / 'logs' / ('dynamic-' + timestamp)
            directory.mkdir(parents=True, exist_ok=True)
            for name, args in [
                ('system-location.txt', ['shell', 'dumpsys', 'location']),
                ('sport-logcat.txt', ['logcat', '-d', '-v', 'time', 'SportMapActivity:V', 'sport:V', 'AndroidRuntime:E', '*:S']),
                ('package.txt', ['shell', 'dumpsys', 'package', PACKAGE]),
            ]:
                text = adb(args, serial)
                (directory / name).write_text(text, encoding='utf-8')
            events.put(('log', '定位和应用日志已保存：' + str(directory)))
        work(task)
    add_button(buttons, '保存定位和应用日志', lambda: action(capture))

    def export():
        if not samples:
            messagebox.showinfo('没有轨迹', '请先运行环形定位测试。')
            return
        path = filedialog.asksaveasfilename(defaultextension='.json', initialfile='emulator-test.json')
        if path:
            Path(path).write_text(json.dumps({'type': 'emulator-location-test', 'coordinates': 'WGS84', 'samples': samples}, ensure_ascii=False, indent=2), encoding='utf-8')
            log('轨迹已导出：' + path)
    add_button(buttons, '导出测试轨迹', export)
    canvas = tk.Canvas(frame, bg='#122337', height=250, highlightthickness=0)
    canvas.pack(fill='x', pady=12)
    progress = tk.StringVar(value='准备就绪。先启动模拟器，然后刷新设备。')
    ttk.Label(frame, textvariable=progress).pack(anchor='w')
    output = tk.Text(frame, height=10, wrap='word', font=('Microsoft YaHei', 9))
    output.pack(fill='both', expand=True, pady=(10, 0))

    documents = {}
    def document_tab(title, filename):
        page = ttk.Frame(notebook, padding=16)
        notebook.add(page, text=title)
        text = tk.Text(page, wrap='word', padx=20, pady=16, relief='flat',
                       font=('Microsoft YaHei', 11), bg='#ffffff', fg='#23364a',
                       spacing1=4, spacing3=8)
        scroll = ttk.Scrollbar(page, orient='vertical', command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        text.pack(fill='both', expand=True)
        text.tag_configure('title', font=('Microsoft YaHei', 22, 'bold'), spacing3=16)
        text.tag_configure('heading', font=('Microsoft YaHei', 15, 'bold'), spacing1=12)
        content = read_document(filename)
        for line in content.splitlines():
            tag = 'title' if line.startswith('# ') else 'heading' if line.startswith('## ') else None
            text.insert('end', line.lstrip('# ').replace('`', '') + '\n' if tag else line.replace('`', '') + '\n', tag or ())
        text.configure(state='disabled')
        documents[title] = content
        return page

    setup_page = ttk.Frame(notebook, padding=20)
    notebook.add(setup_page, text='首次安装')
    ttk.Label(setup_page, text='安装 Android 环境与安小信', font=('Microsoft YaHei', 20, 'bold')).pack(anchor='w')
    ttk.Label(setup_page, text='1. 自动下载环境 → 2. 启动模拟器 → 3. 安装内置 APK → 4. 使用自己的账号登录', wraplength=980).pack(anchor='w', pady=8)
    ttk.Label(setup_page, text='首次需要联网下载约 1–2 GB，磁盘至少留出 12 GB；无需另装 Python、Java 或 Android Studio。', wraplength=980).pack(anchor='w')
    install_sdk_var = tk.StringVar(value=str(sdk_directory()))
    ttk.Label(setup_page, text='Android 环境安装目录').pack(anchor='w', pady=(14, 4))
    setup_sdk_row = ttk.Frame(setup_page)
    setup_sdk_row.pack(fill='x')
    ttk.Entry(setup_sdk_row, textvariable=install_sdk_var).pack(side='left', fill='x', expand=True)
    def choose_install_dir():
        chosen = filedialog.askdirectory(title='选择 Android 环境目录')
        if chosen:
            install_sdk_var.set(chosen)
    ttk.Button(setup_sdk_row, text='选择目录', command=choose_install_dir).pack(side='left', padx=8)
    proxy_var = tk.StringVar(value='')
    ttk.Label(setup_page, text='可选 HTTP 代理（留空使用系统网络设置，例如 http://127.0.0.1:7897）').pack(anchor='w', pady=(12, 4))
    ttk.Entry(setup_page, textvariable=proxy_var).pack(fill='x')
    license_var = tk.BooleanVar(value=False)
    license_row = ttk.Frame(setup_page)
    license_row.pack(fill='x', pady=12)
    ttk.Checkbutton(license_row, text='已阅读并同意 Android SDK 许可', variable=license_var).pack(side='left')
    def open_license():
        import webbrowser
        webbrowser.open('https://developer.android.com/studio/terms')
    ttk.Button(license_row, text='查看许可', command=open_license).pack(side='left', padx=12)
    setup_actions = ttk.Frame(setup_page)
    setup_actions.pack(fill='x', pady=4)
    setup_output = tk.Text(setup_page, height=12, wrap='word', font=('Microsoft YaHei', 10))
    def automatic_setup():
        import android_setup
        sdk, proxy, accepted = install_sdk_var.get().strip(), proxy_var.get().strip(), license_var.get()
        avd = avd_var.get().strip() or 'VirtualRun_API30'
        if not sdk:
            raise ValueError('请选择 Android 环境安装目录。')
        stop.clear()
        def task():
            android_setup.install(sdk, DATA_DIR / 'downloads', avd, proxy, accepted, stop,
                                  lambda message: events.put(('setup_log', message)))
            value = load_settings()
            value.update(sdk=sdk, avd=avd)
            save_settings(value)
            events.put(('sdk_ready', (sdk, avd)))
        work(task, 'setup_log')
    add_button(setup_actions, '自动下载安装环境', lambda: action(automatic_setup))
    ttk.Button(setup_actions, text='停止下载', command=stop.set).pack(side='left', padx=3)
    add_button(setup_actions, '启动模拟器', lambda: action(start_emulator))
    add_button(setup_actions, '刷新设备', refresh)
    add_button(setup_actions, '安装安小信', lambda: action(install))
    add_button(setup_actions, '打开安小信', lambda: action(launch))
    setup_output.pack(fill='both', expand=True, pady=10)
    ttk.Label(setup_page, text='下载中断可重试续传。已有虚拟设备不会被覆盖。完成后到“跑步测试”刷新设备，选择 emulator- 开头的设备。', wraplength=980).pack(anchor='w')
    login_page = document_tab('启动与登录', 'docs/首次安装与登录.md')
    ttk.Button(setup_page, text='查看完整启动与登录教程', command=lambda: notebook.select(login_page)).pack(anchor='w', pady=8)

    morning_page = ttk.Frame(notebook, padding=24)
    notebook.insert(0, morning_page, text='晨跑与登录')
    ttk.Label(morning_page, text='电脑版晨跑', font=('Microsoft YaHei', 20, 'bold')).pack(anchor='w')
    ttk.Label(morning_page, text='登录 → 读取起点 → 试跑 → 启用每日晨跑。06:55 准备，07:00 开始，08:00 结束。', wraplength=980).pack(anchor='w', pady=8)
    quick_help = ttk.Frame(morning_page)
    quick_help.pack(fill='x')
    ttk.Button(quick_help, text='首次安装环境与 APK', command=lambda: notebook.select(setup_page)).pack(side='left', padx=3)
    ttk.Button(quick_help, text='登录教程', command=lambda: notebook.select(login_page)).pack(side='left', padx=3)
    import morning_support
    import login_support
    morning_directory = DATA_DIR / 'morning'
    credential_path = morning_directory / 'login.dat'
    try:
        stored_morning = json.loads((morning_directory / '晨跑配置.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        stored_morning = {}
    headless_var = tk.BooleanVar(value=bool(stored_morning.get('headless', settings.get('headless', False))))
    headless_control = ttk.Checkbutton(morning_page, text='后台运行模拟器（无窗口；仅在下次启动模拟器时生效）', variable=headless_var)
    headless_control.pack(anchor='w', pady=5)
    controls.append(headless_control)
    account_var = tk.StringVar()
    password_var = tk.StringVar()
    remember_var = tk.BooleanVar(value=bool(stored_morning.get('auto_login')))
    try:
        account_var.set(login_support.load_credentials(credential_path)['account'])
    except RuntimeError:
        pass
    login_form = ttk.LabelFrame(morning_page, text='登录安小信', padding=10)
    login_form.pack(fill='x', pady=5)
    ttk.Label(login_form, text='账号').grid(row=0, column=0, padx=5)
    account_entry = ttk.Entry(login_form, textvariable=account_var, width=25)
    account_entry.grid(row=0, column=1, padx=5, sticky='ew')
    ttk.Label(login_form, text='密码').grid(row=0, column=2, padx=5)
    password_entry = ttk.Entry(login_form, textvariable=password_var, show='●', width=25)
    password_entry.grid(row=0, column=3, padx=5, sticky='ew')
    login_form.columnconfigure(1, weight=1)
    login_form.columnconfigure(3, weight=1)
    show_password = tk.BooleanVar(value=False)
    ttk.Checkbutton(login_form, text='显示密码', variable=show_password,
                    command=lambda: password_entry.configure(show='' if show_password.get() else '●')).grid(row=0, column=4, padx=5)
    ttk.Checkbutton(login_form, text='为晨跑保存登录信息（当前 Windows 用户加密保存）', variable=remember_var).grid(row=1, column=0, columnspan=4, sticky='w', pady=8)
    login_actions = ttk.Frame(login_form)
    login_actions.grid(row=2, column=0, columnspan=5, sticky='w')
    login_status = tk.StringVar(value='已保存本机登录信息' if credential_path.is_file() else '未保存登录信息；可仅登录一次')
    ttk.Label(login_form, textvariable=login_status).grid(row=3, column=0, columnspan=5, sticky='w', pady=(5, 0))

    def login_values():
        account, password = account_var.get().strip(), password_var.get()
        if not password and remember_var.get() and credential_path.is_file():
            saved = login_support.load_credentials(credential_path)
            if account != saved['account']:
                raise ValueError('修改账号后请重新填写对应密码。')
            password = saved['password']
        return account, password, remember_var.get()

    def persist_login(account, password, remember):
        if remember:
            login_support.save_credentials(credential_path, account, password)
        else:
            credential_path.unlink(missing_ok=True)
        config_path = morning_directory / '晨跑配置.json'
        if config_path.is_file():
            value = json.loads(config_path.read_text(encoding='utf-8-sig'))
            value.update(auto_login=bool(remember), credential_file=str(credential_path))
            temporary = config_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(config_path)
        events.put(('login_status', '已加密保存；晨跑遇到登录页时自动登录' if remember else '已清除保存的登录信息；晨跑需要安小信保持登录'))

    def store_login():
        values = login_values()
        if values[2]:
            login_support.validate_credentials(*values[:2])
        work(lambda: persist_login(*values), 'morning_log')

    def automatic_login():
        values = login_values()
        login_support.validate_credentials(*values[:2])
        sdk, avd = sdk_var.get().strip(), avd_var.get().strip()
        headless = headless_var.get()
        stop.clear()
        def task():
            from morning_run import Runner
            persist_login(*values)
            runner = Runner({'sdk': sdk, 'avd': avd, 'headless': headless}, morning_directory / 'logs' / ('login_' + time.strftime('%Y-%m-%d_%H%M%S')))
            # GUI uses the SDK selected in Settings, without touching a phone.
            import morning_run
            original_adb = morning_run.ADB_PATH
            morning_run.ADB_PATH = str(Path(sdk) / 'platform-tools/adb.exe')
            old_log = runner.log
            def report(message):
                old_log(message)
                events.put(('morning_log', message))
            runner.log = report
            try:
                runner.select_emulator(start=True, stop_event=stop)
                login_support.open_and_login(runner, *values[:2], stop)
                events.put(('clear_password', None))
                events.put(('login_status', '已确认安小信登录成功'))
            finally:
                morning_run.ADB_PATH = original_adb
        work(task, 'morning_log')

    add_button(login_actions, '登录安小信', lambda: action(automatic_login))
    add_button(login_actions, '保存登录设置', lambda: action(store_login))
    ttk.Label(morning_page, text='读取指定模拟器的当前 GPS 作为起点；确认安小信地图位置正确。试跑和启用时自动保存下方参数。', wraplength=980).pack(anchor='w', pady=(10, 0))
    anchor = stored_morning.get('anchor_wgs84') or ['', '']
    morning_lat = tk.StringVar(value=str(anchor[0]))
    morning_lon = tk.StringVar(value=str(anchor[1]))
    morning_speed = tk.StringVar(value=str(stored_morning.get('speed_kmh', 12)))
    morning_radius = tk.StringVar(value=str(stored_morning.get('radius_m', 150)))
    morning_form = ttk.Frame(morning_page)
    morning_form.pack(fill='x', pady=10)
    for col, (label, variable) in enumerate((('纬度 WGS84', morning_lat), ('经度 WGS84', morning_lon),
                                           ('速度 / km/h', morning_speed), ('半径 / 米', morning_radius))):
        ttk.Label(morning_form, text=label).grid(row=0, column=col, sticky='w', padx=4)
        ttk.Entry(morning_form, textvariable=variable, width=18).grid(row=1, column=col, sticky='ew', padx=4)
        morning_form.columnconfigure(col, weight=1)
    def copy_origin():
        if not fields['lat'].get() or not fields['lon'].get():
            raise ValueError('请先在“跑步测试”读取或填写起点。')
        morning_lat.set(fields['lat'].get())
        morning_lon.set(fields['lon'].get())
    def read_morning_origin():
        sdk, avd = sdk_var.get().strip(), avd_var.get().strip()
        headless = headless_var.get()
        stop.clear()
        def task():
            import morning_run
            runner = morning_run.Runner({'sdk': sdk, 'avd': avd, 'headless': headless}, morning_directory / 'logs' / ('origin_' + time.strftime('%Y-%m-%d_%H%M%S')))
            original_adb = morning_run.ADB_PATH
            morning_run.ADB_PATH = str(Path(sdk) / 'platform-tools/adb.exe')
            try:
                runner.select_emulator(start=True, stop_event=stop)
                origin = morning_run.current_location(runner.serial)
                events.put(('morning_origin', origin))
                events.put(('morning_log', '已读取当前系统 GPS；确认安小信地图位置正确后再试跑。'))
            finally:
                morning_run.ADB_PATH = original_adb
        work(task, 'morning_log')
    add_button(quick_help, '读取当前 GPS 起点', lambda: action(read_morning_origin))
    def close_morning_emulator():
        sdk, avd = sdk_var.get().strip(), avd_var.get().strip()
        def task():
            import morning_run
            runner = morning_run.Runner({'sdk': sdk, 'avd': avd}, morning_directory / 'logs' / ('close_' + time.strftime('%Y-%m-%d_%H%M%S')))
            original_adb = morning_run.ADB_PATH
            morning_run.ADB_PATH = str(Path(sdk) / 'platform-tools/adb.exe')
            try:
                runner.close_emulator(morning_directory / 'morning.lock')
                events.put(('morning_log', '指定模拟器已关闭。切换后台选项后，点击“读取当前 GPS 起点”可重新启动。'))
            finally:
                morning_run.ADB_PATH = original_adb
        work(task, 'morning_log')
    add_button(quick_help, '关闭模拟器', lambda: action(close_morning_emulator))
    ttk.Button(morning_form, text='从跑步测试复制', command=lambda: action(copy_origin)).grid(row=1, column=4, padx=6)
    morning_actions = ttk.Frame(morning_page)
    morning_actions.pack(fill='x')
    morning_output = tk.Text(morning_page, height=8, wrap='word', font=('Microsoft YaHei', 10))
    def powershell_file(path, extra=()):
        executable = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        result = subprocess.run([str(executable), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(path), *extra], capture_output=True, timeout=45,
                                encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        events.put(('morning_log', result.stdout + result.stderr))
        if result.returncode:
            raise RuntimeError('晨跑命令未完成，查看晨跑日志。')
    def morning_parameters():
        try:
            origin = float(morning_lat.get()), float(morning_lon.get())
        except ValueError:
            raise ValueError('请填写晨跑 WGS84 起点，或从“跑步测试”复制起点。')
        sdk, avd = sdk_var.get().strip(), avd_var.get().strip()
        speed, radius = float(morning_speed.get()), float(morning_radius.get())
        values = login_values()
        if values[2]:
            login_support.validate_credentials(*values[:2])
        from morning_run import validate
        validate({'start': '07:00', 'end': '08:00', 'anchor_wgs84': origin,
                  'speed_kmh': speed, 'radius_m': radius, 'max_origin_gap_m': 2000})
        return sdk, avd, origin, speed, radius, values, headless_var.get()
    def save_morning(parameters):
        sdk, avd, origin, speed, radius, values, headless = parameters
        persist_login(*values)
        target = morning_support.configure(sdk, avd, *origin, speed, radius, auto_login=values[2], headless=headless)
        events.put(('morning_log', f'参数已保存：07:00–08:00，{speed:g} km/h，半径 {radius:g} 米。'))
        return target
    def configure_morning(enable=False, check=False):
        parameters = morning_parameters()
        if enable:
            from stop_support import resume
            resume(morning_directory)
        def task():
            target = save_morning(parameters)
            if check:
                run_runner(['--check'])
            if enable:
                if stop.is_set():
                    raise RuntimeError('已取消启用晨跑。')
                powershell_file(target / '启用每日晨跑.ps1')
                events.put(('morning_log', '每日晨跑已启用。运行前结束旧记录，电脑保持开机并登录。'))
        work(task, 'morning_log')
    add_button(morning_actions, '保存晨跑配置', lambda: action(configure_morning))
    add_button(morning_actions, '保存并检查环境', lambda: action(lambda: configure_morning(check=True)))
    add_button(morning_actions, '保存并启用每日晨跑', lambda: action(lambda: configure_morning(enable=True)))
    add_button(morning_actions, '停用每日晨跑', lambda: work(lambda: powershell_file(RESOURCE_DIR / 'morning/停用每日晨跑.ps1', ('-MorningDirectory', str(morning_directory))), 'morning_log'))
    run_actions = ttk.Frame(morning_page)
    run_actions.pack(fill='x', pady=8)
    def run_runner(mode):
        if stop.is_set():
            raise RuntimeError('用户停止了任务。')
        configuration = morning_directory / '晨跑配置.json'
        request = morning_directory / ('stop-' + uuid.uuid4().hex + '.request')
        command = [str(morning_support.runtime_python()), '-X', 'utf8', '-u', str(morning_directory / 'morning_run.py'),
                   '--config', str(configuration), '--stop-file', str(request), *mode]
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 encoding='utf-8', errors='replace',
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        def read_output():
            for line in child.stdout:
                events.put(('morning_log', line.rstrip()))
        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        try:
            requested = False
            while child.poll() is None:
                if stop.wait(.2):
                    if not requested:
                        request.touch()
                        requested = True
                    time.sleep(.2)
            reader.join(timeout=3)
            if child.returncode and not stop.is_set():
                raise RuntimeError('晨跑未完成，具体原因见上方日志与结果文件。')
            if stop.is_set():
                events.put(('morning_log', '当前脚本已停止并退出，后台进程已回收。'))
        finally:
            child.wait()
            reader.join(timeout=3)
            child.stdout.close()
            request.unlink(missing_ok=True)
    def run_morning(trial=False):
        parameters = morning_parameters()
        from stop_support import resume
        resume(morning_directory)
        def task():
            save_morning(parameters)
            run_runner(['--trial-seconds', '60'] if trial else ['--scheduled'])
            events.put(('morning_log', '脚本已退出。定时模式在准备窗口之外会跳过；试跑结果见 trial-result.json。'))
        work(task, 'morning_log')
    add_button(run_actions, '立即试跑 60 秒', lambda: action(lambda: run_morning(trial=True)))
    add_button(run_actions, '执行今天的定时脚本', lambda: action(run_morning))
    ttk.Button(run_actions, text='停止当前操作', command=stop.set).pack(side='left', padx=3)
    def disable_all():
        nonlocal disabling
        if disabling:
            return
        from stop_support import request_disable
        request_disable(morning_directory)
        stop.set()
        disabling = True
        disable_button.configure(state='disabled')
        for button in controls:
            button.configure(state='disabled')
        combo.configure(state='disabled')
        sdk, avd = sdk_var.get().strip(), avd_var.get().strip()
        active = worker_thread
        events.put(('morning_log', '正在一键停用：取消当前操作、停用后续晨跑，并等待当前任务退出……'))
        def task():
            failures = []
            try:
                powershell_file(RESOURCE_DIR / 'morning/停用每日晨跑.ps1', ('-MorningDirectory', str(morning_directory)))
            except Exception as exc:
                failures.append(str(exc))
            if active is not None:
                while active.is_alive():
                    active.join(timeout=10)
                    if active.is_alive():
                        events.put(('morning_log', '仍在等待当前任务完成清理并退出，请稍候……'))
            # Cover an enable action that was already in progress when cancelled.
            request_disable(morning_directory)
            try:
                powershell_file(RESOURCE_DIR / 'morning/停用每日晨跑.ps1', ('-MorningDirectory', str(morning_directory)))
                from stop_support import wait_until_idle
                wait_until_idle(morning_directory)
                import morning_run
                runner = morning_run.Runner({'sdk': sdk, 'avd': avd}, morning_directory / 'logs' / ('disable_' + time.strftime('%Y-%m-%d_%H%M%S')))
                old_adb = morning_run.ADB_PATH
                morning_run.ADB_PATH = str(Path(sdk) / 'platform-tools/adb.exe')
                try:
                    # Shutdown is a separate action, after operation cancellation.
                    morning_run.STOP.clear()
                    devices = morning_run.adb(['devices'])
                    connected = any(line.startswith('emulator-') and line.split()[1:2] == ['device'] for line in devices.splitlines())
                    if connected:
                        runner.close_emulator(morning_directory / 'morning.lock')
                        events.put(('morning_log', '已关闭指定模拟器。'))
                finally:
                    morning_run.ADB_PATH = old_adb
            except Exception as exc:
                failures.append('模拟器/计划任务处理未完成：' + str(exc))
            events.put(('disable_done', failures))
        def guarded_disable():
            try:
                task()
            except Exception as exc:
                events.put(('disable_done', ['停用未完成：' + str(exc)]))
        threading.Thread(target=guarded_disable, daemon=True).start()
    disable_button = ttk.Button(run_actions, text='一键停用', command=lambda: action(disable_all))
    disable_button.pack(side='left', padx=3)
    def open_morning_logs():
        directory = DATA_DIR / 'morning'
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(str(directory))
    ttk.Button(run_actions, text='打开配置与日志', command=lambda: action(open_morning_logs)).pack(side='left', padx=3)
    ttk.Label(morning_page, text='“执行今天的定时脚本”只在 06:50–07:01 准备或启动；其他时间用 60 秒试跑。\n计划任务无需保持本窗口打开；电脑须开机、Windows 已登录。首次权限提示需先手动完成。', wraplength=980).pack(anchor='w', pady=4)
    morning_output.pack(fill='both', expand=True, pady=8)

    guide_page = document_tab('使用指南', 'docs/使用指南.md')
    document_tab('README', 'README.md')
    app.bind('<F1>', lambda _: notebook.select(guide_page))

    settings_page = ttk.Frame(notebook, padding=28)
    notebook.add(settings_page, text='设置')
    ttk.Label(settings_page, text='模拟器与文件', font=('Microsoft YaHei', 20, 'bold')).pack(anchor='w', pady=(0, 20))
    ttk.Label(settings_page, text='Android SDK 目录（用于启动已安装的官方模拟器）').pack(anchor='w')
    sdk_var = tk.StringVar(value=str(sdk_directory()))
    sdk_row = ttk.Frame(settings_page)
    sdk_row.pack(fill='x', pady=(8, 20))
    ttk.Entry(sdk_row, textvariable=sdk_var).pack(side='left', fill='x', expand=True)
    def browse_sdk():
        chosen = filedialog.askdirectory(title='选择 Android SDK 目录', initialdir=str(sdk_directory()))
        if chosen:
            sdk_var.set(chosen)
    ttk.Button(sdk_row, text='选择目录', command=browse_sdk).pack(side='left', padx=8)
    ttk.Label(settings_page, text='虚拟设备名称（AVD）').pack(anchor='w')
    avd_var = tk.StringVar(value=str(settings.get('avd', 'VirtualRun_API30')))
    avd_combo = ttk.Combobox(settings_page, textvariable=avd_var, width=45)
    avd_combo.pack(anchor='w', pady=8)
    def list_avds():
        try:
            exe = Path(sdk_var.get().strip()) / 'emulator/emulator.exe'
            result = subprocess.run([str(exe), '-list-avds'], capture_output=True, timeout=15,
                                    encoding='utf-8', errors='replace',
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise RuntimeError(result.stderr)
            avd_combo['values'] = result.stdout.splitlines()
            if not avd_combo['values']:
                messagebox.showinfo('没有虚拟设备', '此 SDK 尚未创建 AVD。请在 Android Studio 的 Device Manager 中创建。')
        except Exception as exc:
            messagebox.showerror('读取失败', str(exc))
    ttk.Button(settings_page, text='读取已有虚拟设备', command=list_avds).pack(anchor='w', pady=(0, 16))
    def store_preferences(show=False):
        value = load_settings()
        value.update(sdk=sdk_var.get().strip(), avd=avd_var.get().strip(), headless=headless_var.get())
        for key in ('radius', 'speed', 'seconds'):
            try:
                numeric = float(fields[key].get())
                low, high = {'radius': (10, 10000), 'speed': (.5, 30), 'seconds': (1, 3600)}[key]
                if math.isfinite(numeric) and low <= numeric <= high:
                    value[key] = fields[key].get()
            except ValueError:
                pass
        save_settings(value)
        if show:
            messagebox.showinfo('已保存', '设置已保存。起点请在每次启动后重新读取和准备。')
    ttk.Button(settings_page, text='保存设置', command=lambda: action(lambda: store_preferences(True))).pack(anchor='w', pady=8)
    ttk.Separator(settings_page).pack(fill='x', pady=24)
    ttk.Label(settings_page, text='设置和日志目录').pack(anchor='w')
    ttk.Label(settings_page, text=str(DATA_DIR), wraplength=900).pack(anchor='w', pady=8)
    def open_data():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        os.startfile(str(DATA_DIR))
    ttk.Button(settings_page, text='打开数据目录', command=lambda: action(open_data)).pack(anchor='w')
    shortcut_row = ttk.Frame(settings_page)
    shortcut_row.pack(anchor='w', pady=12)
    shortcut_status = tk.StringVar(value='快捷方式使用固定程序目录，不依赖下载的 EXE 位置。')
    def make_shortcuts(kind):
        def task():
            from shortcut_support import create_shortcuts
            paths = create_shortcuts(kind)
            events.put(('shortcut_status', '已创建：' + '；'.join(str(p) for p in paths)))
        work(task, 'shortcut_status')
    add_button(shortcut_row, '创建桌面快捷方式', lambda: action(lambda: make_shortcuts('desktop')))
    add_button(shortcut_row, '添加到开始菜单', lambda: action(lambda: make_shortcuts('start-menu')))
    def begin_uninstall():
        from uninstall_support import launch_uninstaller
        launch_uninstaller()
        close()
    add_button(shortcut_row, '卸载软件…', lambda: action(begin_uninstall))
    ttk.Label(settings_page, textvariable=shortcut_status, wraplength=900).pack(anchor='w')
    ttk.Label(settings_page, text='软件带有安小信 APK、Python 运行环境、启动与登录教程和晨跑脚本。\n“首次安装”可自动下载官方模拟器与 Android 镜像；也可使用已有环境。\n关闭本软件不会关闭安卓模拟器。按 F1 可打开使用指南。', wraplength=900).pack(anchor='w', pady=24)
    document_tab('关于与许可', 'docs/关于.md')

    def paint():
        canvas.delete('all')
        radius = samples[-1]['radius'] if samples else 150
        w, h = canvas.winfo_width(), canvas.winfo_height()
        scale = max(1, min(w - 50, h - 50)) / (2 * radius)
        def pos(p):
            return w / 2 + p['east'] * scale, h / 2 - (p['north'] - radius) * scale
        rr = radius * scale
        canvas.create_oval(w / 2 - rr, h / 2 - rr, w / 2 + rr, h / 2 + rr, outline='#638197', dash=(5, 5))
        canvas.create_text(18, 18, text='环形路线预览', anchor='nw', fill='#afc6d8', font=('Microsoft YaHei', 11))
        if not samples:
            canvas.create_text(w / 2, h / 2, text='准备起点后开始测试', fill='#afc6d8', font=('Microsoft YaHei', 11))
            return
        coords = [n for point in samples for n in pos(point)]
        if len(coords) >= 4:
            canvas.create_line(*coords, fill='#53dec6', width=3)
        x, y = pos(samples[-1])
        canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill='#aaffdd', outline='')

    def poll():
        nonlocal busy, prepared, disabling
        while True:
            try:
                kind, value = events.get_nowait()
            except queue.Empty:
                break
            if kind == 'done':
                busy = False
                if not disabling:
                    for button in controls:
                        button.configure(state='normal')
                    combo.configure(state='readonly')
            elif kind == 'disable_done':
                disabling = False
                disable_button.configure(state='normal')
                if not busy:
                    for button in controls:
                        button.configure(state='normal')
                    combo.configure(state='readonly')
                message = '当前操作已退出，后续晨跑已停用。可重新登录、试跑或启用计划任务。'
                if value:
                    message = '停止请求已发送；请检查以下未完成事项：\n' + '\n'.join(value)
                morning_output.insert('end', message + '\n')
                morning_output.see('end')
                log(message)
            elif kind == 'devices':
                combo['values'] = value
                device.set(next((x for x in value if x.startswith('emulator-')), value[0] if value else ''))
            elif kind == 'origin':
                serial, lat, lon, source = value
                if device.get() == serial:
                    fields['lat'].set(f'{lat:.8f}')
                    fields['lon'].set(f'{lon:.8f}')
                    samples.clear()
                    paint()
                    progress.set(f'已读取起点：{lat:.6f}, {lon:.6f}；请准备起点。')
                    log(source)
            elif kind == 'ready':
                try:
                    current = signature(selected(), config())
                except (ValueError, RuntimeError):
                    current = None
                if value == current:
                    prepared = value
                    ready_text.set('系统起点已准备；开始前确认应用地图')
                    progress.set('请在安小信开始新的测试，再启动环形定位。')
            elif kind == 'invalid':
                invalidate_origin()
            elif kind == 'sample':
                samples.append(value)
                progress.set(f"已发送 {value['elapsed']:.1f} 秒 / {value['distance']:.1f} 米 · {value['lat']:.6f}, {value['lon']:.6f}")
                paint()
            elif kind == 'log':
                log(value)
            elif kind == 'setup_log':
                setup_output.insert('end', value + '\n')
                setup_output.see('end')
                log(value)
            elif kind == 'morning_log':
                morning_output.insert('end', value + '\n')
                morning_output.see('end')
                log(value)
            elif kind == 'login_status':
                login_status.set(value)
                morning_output.insert('end', value + '\n')
                morning_output.see('end')
            elif kind == 'shortcut_status':
                shortcut_status.set(value)
            elif kind == 'clear_password':
                password_var.set('')
            elif kind == 'morning_origin':
                morning_lat.set(f'{value[0]:.8f}')
                morning_lon.set(f'{value[1]:.8f}')
            elif kind == 'sdk_ready':
                sdk_var.set(value[0])
                avd_var.set(value[1])
                messagebox.showinfo('安装完成', '环境已就绪。启动模拟器、刷新设备，再安装内置 APK 并登录安小信。')
        app.after(100, poll)
    canvas.bind('<Configure>', lambda _: paint())
    closing = False
    def close():
        nonlocal closing
        if not closing:
            closing = True
            stop.set()
            try:
                store_preferences()
            except OSError as exc:
                log('设置保存失败：' + str(exc))
        if busy or disabling:
            progress.set('正在停止当前任务，请稍候……')
            app.after(100, close)
        else:
            app.destroy()
    app.protocol('WM_DELETE_WINDOW', close)
    notebook.select(morning_page)
    poll()
    if smoke_output:
        notebook.select(morning_page)
        def smoke():
            result = {'version': VERSION, 'tabs': [notebook.tab(t, 'text') for t in notebook.tabs()],
                      'documents': {k: len(v) for k, v in documents.items()},
                      'coordinates_blank': not fields['lat'].get() and not fields['lon'].get(),
                      'controls_fit': all(b.winfo_rootx() + b.winfo_width() <= app.winfo_rootx() + app.winfo_width() for b in controls),
                      'morning_controls': [b.cget('text') for b in controls if b.winfo_toplevel() == app and b.winfo_viewable()],
                      'login_form_fit': password_entry.winfo_rooty() + password_entry.winfo_height() < app.winfo_rooty() + app.winfo_height(),
                      'password_masked': password_entry.cget('show') == '●'}
            result['headless_option_visible'] = bool(headless_control.winfo_viewable())
            result['disable_button_visible'] = bool(disable_button.winfo_viewable())
            result['disable_button_always_available'] = disable_button not in controls
            result['headless_default'] = headless_var.get()
            notebook.select(settings_page)
            app.update_idletasks()
            result['shortcut_buttons'] = [b.cget('text') for b in shortcut_row.winfo_children()]
            result['shortcut_controls_fit'] = all(
                b.winfo_rootx() + b.winfo_width() <= app.winfo_rootx() + app.winfo_width()
                and b.winfo_rooty() + b.winfo_height() <= app.winfo_rooty() + app.winfo_height()
                for b in shortcut_row.winfo_children())
            try:
                destination = Path(smoke_output)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            finally:
                app.destroy()
        app.after(600, smoke)
    app.mainloop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--diagnostics', metavar='JSON_FILE')
    parser.add_argument('--ui-smoke', metavar='JSON_FILE')
    args = parser.parse_args()
    if args.diagnostics:
        self_test()
        import tkinter
        included = bundled_adb()
        bundled_version = subprocess.run([included, 'version'], capture_output=True, timeout=15,
                                         encoding='utf-8', errors='replace',
                                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) if included else None
        report = {'version': VERSION, 'frozen': bool(getattr(__import__('sys'), 'frozen', False)),
                  'documents': {p: len(read_document(p)) for p in ('README.md', 'docs/使用指南.md', 'docs/关于.md')},
                  'adb_version': adb(['version']), 'tk_version': tkinter.TkVersion,
                  'bundled_adb_version': bundled_version.stdout if bundled_version else None}
        if bundled_version and bundled_version.returncode:
            raise RuntimeError(bundled_version.stderr)
        destination = Path(args.diagnostics)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    elif args.ui_smoke:
        gui(args.ui_smoke)
    elif args.self_test:
        self_test()
    elif args.check:
        print(adb(['devices', '-l']))
    else:
        gui()
