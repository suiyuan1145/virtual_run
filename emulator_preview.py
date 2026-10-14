"""Read-only screen viewer for the configured official emulator, including headless mode."""
from pathlib import Path
import base64
import math
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk


def capture_screen(adb, avd):
    adb = Path(adb)
    if not adb.is_file():
        raise RuntimeError('没有找到 ADB，请在设置中确认 Android SDK 目录。')
    def call(args):
        result = subprocess.run([str(adb), *args], capture_output=True, timeout=12,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode:
            raise RuntimeError('无法读取模拟器画面，请检查设备是否离线。')
        return result.stdout
    serial = None
    for line in call(['devices']).decode('utf-8', errors='replace').splitlines():
        columns = line.split()
        if len(columns) >= 2 and columns[0].startswith('emulator-') and columns[1] == 'device':
            name = call(['-s', columns[0], 'emu', 'avd', 'name']).decode('utf-8', errors='replace').splitlines()
            if name and name[0].strip() == avd:
                serial = columns[0]
                break
    if serial is None:
        raise RuntimeError('指定模拟器未连接。请先启动模拟器；预览不会启动或重启设备。')
    data = call(['-s', serial, 'exec-out', 'screencap', '-p'])
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):
        raise RuntimeError('未取得有效画面，请稍后刷新。')
    return data


def show_preview(parent, adb, avd):
    window = tk.Toplevel(parent)
    window.title('后台模拟器画面 · ' + avd)
    frame = ttk.Frame(window, padding=8)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='只读预览，每 2 秒刷新。需手动操作时：关闭模拟器 → 取消后台选项 → 重新启动。',
              wraplength=480).pack(fill='x')
    status = tk.StringVar(value='正在读取画面……')
    ttk.Label(frame, textvariable=status, wraplength=480).pack(fill='x', pady=5)
    picture = ttk.Label(frame)
    picture.pack()
    stopped, refresh = threading.Event(), threading.Event()
    results = queue.Queue(maxsize=1)
    timer = [None]
    toolbar = ttk.Frame(frame)
    toolbar.pack(fill='x', pady=6)
    ttk.Button(toolbar, text='刷新画面', command=refresh.set).pack(side='left')

    def close():
        stopped.set()
        refresh.set()
        if timer[0] is not None:
            window.after_cancel(timer[0])
        window.destroy()

    ttk.Button(toolbar, text='关闭预览', command=close).pack(side='right')
    window.protocol('WM_DELETE_WINDOW', close)
    def worker():
        while not stopped.is_set():
            try:
                value = ('image', capture_screen(adb, avd))
            except RuntimeError as exc:
                value = ('error', str(exc))
            except Exception:
                value = ('error', '读取失败：请确认指定模拟器已连接，再刷新画面。')
            if stopped.is_set():
                return
            try:
                results.get_nowait()
            except queue.Empty:
                pass
            results.put_nowait(value)
            refresh.wait(2)
            refresh.clear()

    def poll():
        if stopped.is_set():
            return
        try:
            kind, value = results.get_nowait()
            if kind == 'image':
                original = tk.PhotoImage(data=base64.b64encode(value).decode('ascii'))
                factor = max(1, math.ceil(original.width() / 480),
                             math.ceil(original.height() / max(250, window.winfo_screenheight() - 220)))
                scaled = original.subsample(factor)
                picture.configure(image=scaled)
                picture.image = scaled
                status.set('已连接；预览不会点击按钮、发送定位或保存截图。')
            else:
                status.set(value)
        except queue.Empty:
            pass
        except tk.TclError:
            status.set('画面暂时无法显示，请刷新。')
        timer[0] = window.after(100, poll)

    def destroyed(event):
        if event.widget == window:
            stopped.set()
            refresh.set()
            if timer[0] is not None:
                try:
                    window.after_cancel(timer[0])
                except tk.TclError:
                    pass
    window.bind('<Destroy>', destroyed)
    threading.Thread(target=worker, daemon=True).start()
    timer[0] = window.after(100, poll)
    return window
