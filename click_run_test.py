"""Short start/finish button test for an already logged-in Android Emulator."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import signal
import time

from desktop_support import DATA_DIR, load_settings, sdk_directory
import morning_run as morning


class ClickTestRunner(morning.Runner):
    def save_ui(self, name):
        items = self.ui()
        source = self.directory / 'latest-ui.xml'
        (self.directory / name).write_bytes(source.read_bytes())
        return items


def exercise(runner, hold_seconds=20, check=False, prepare_origin=False):
    """Never alter an existing record; always clean up this test's own start."""
    result = {'mode': 'button-test', 'coordinates_sent': False,
              'hold_seconds': hold_seconds, 'status': 'checking',
              'start_confirmed': False, 'finish_confirmed': False}
    try:
        origin = runner.preflight()
        if check:
            result['status'] = 'check_passed'
            runner.log('检查通过。未点击开始按钮。')
            return result
        runner.navigate_to_start()
        if prepare_origin:
            result['coordinates_sent'] = True
            runner.prepare(origin)
            runner.log('静止起点已准备，继续按钮测试。')
        runner.save_ui('01-before-start.xml')
        runner.start()
        result['start_confirmed'] = True
        result['status'] = 'running'
        runner.save_ui('02-running.xml')
        runner.log(f'开始按钮测试通过；等待 {hold_seconds} 秒后自动结束。')
        deadline = time.monotonic() + hold_seconds
        while time.monotonic() < deadline and not morning.STOP.is_set():
            if prepare_origin:
                morning.send_location(runner.serial, *origin, 0)
            morning.STOP.wait(min(1, max(0, deadline - time.monotonic())))
        result['status'] = 'cancelled' if morning.STOP.is_set() else 'finished'
    except Exception as exc:
        result['status'] = 'failed'
        result['error'] = str(exc)
        runner.log('测试未通过：' + str(exc))
    finally:
        # Runner.start marks ownership before clicking so an ADB/UI timeout
        # still enters this finish path; pre-existing records never do.
        if runner.started:
            try:
                runner.finish()
                result['finish_confirmed'] = not runner.started
                runner.save_ui('03-after-finish.xml')
            except Exception as exc:
                result['status'] = 'failed'
                result['finish_error'] = str(exc)
                runner.log('结束未确认，请检查模拟器：' + str(exc))
        if not check:
            runner.capture()
        result['completed_at'] = morning.now().isoformat()
        (runner.directory / 'result.json').write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='安小信自动点击开始和结束按钮的短时测试')
    parser.add_argument('--sdk', default=str(sdk_directory()))
    parser.add_argument('--avd', default=load_settings().get('avd', 'VirtualRun_API30'))
    parser.add_argument('--hold-seconds', type=int, default=20)
    parser.add_argument('--start-emulator', action='store_true', help='未连接时启动已配置的官方模拟器')
    parser.add_argument('--origin', nargs=2, type=float, metavar=('LAT', 'LON'),
                        help='没有 GPS 时，用指定 WGS84 起点静止准备 15 秒')
    parser.add_argument('--check', action='store_true', help='只读检查，不点击开始按钮')
    args = parser.parse_args(argv)
    if not 1 <= args.hold_seconds <= 60:
        parser.error('按钮测试等待时间为 1–60 秒。')
    morning.STOP.clear()
    morning.ADB_PATH = str(Path(args.sdk) / 'platform-tools/adb.exe')
    directory = DATA_DIR / 'click-tests' / morning.now().strftime('%Y-%m-%d_%H%M%S_%f')
    config = {'sdk': args.sdk, 'avd': args.avd, 'start': '07:00', 'end': '08:00',
              'speed_kmh': 12, 'radius_m': 150, 'max_origin_gap_m': 2000,
              'anchor_wgs84': [0, 0]}
    runner = ClickTestRunner(config, directory)
    runner.log('开始按钮测试。脚本不发送跑步路线；短记录可能无法保存。')
    try:
        runner.select_emulator(start=args.start_emulator)
        prepare_origin = False
        try:
            config['anchor_wgs84'] = list(morning.current_location(runner.serial))
        except RuntimeError:
            if not args.origin:
                raise RuntimeError('模拟器没有 GPS。请先准备起点，或使用 --origin 纬度 经度 静止准备已确认的起点。')
            config['anchor_wgs84'] = args.origin
            morning.validate(config)
            prepare_origin = True
            runner.log('没有系统 GPS，将在点击开始前静止准备指定起点。')
        # Share the personal morning mutex so a test cannot race that task.
        mutex_path = DATA_DIR / 'morning' / 'morning.lock'
        mutex_path.parent.mkdir(parents=True, exist_ok=True)
        with mutex_path.open('a+b') as mutex:
            import msvcrt
            mutex.seek(0)
            try:
                msvcrt.locking(mutex.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError('已有晨跑或按钮测试正在运行。')
            result = exercise(runner, args.hold_seconds, args.check, prepare_origin)
    except Exception as exc:
        result = {'status': 'failed', 'start_confirmed': False,
                  'finish_confirmed': False, 'coordinates_sent': False,
                  'error': str(exc), 'completed_at': morning.now().isoformat()}
        runner.log('测试未通过：' + str(exc))
        (directory / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    runner.log('测试报告：' + str(directory / 'result.json'))
    return 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    signal.signal(signal.SIGINT, lambda *_: morning.STOP.set())
    signal.signal(signal.SIGTERM, lambda *_: morning.STOP.set())
    raise SystemExit(main())
