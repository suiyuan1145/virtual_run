"""Read-only local snapshots; never start the runner or manipulate its records."""
import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from location_tools import distance_meters, parse_gps_location

TZ = timezone(timedelta(hours=8))
PACKAGE = 'cn.edu.aiit.axx'


def command(args):
    result = subprocess.run(args, capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=20,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError('只读命令执行失败')
    return result.stdout


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def tail(path, limit=2_000_000):
    try:
        with Path(path).open('rb') as stream:
            stream.seek(max(0, Path(path).stat().st_size - limit))
            return stream.read().decode('utf-8', errors='replace')
    except OSError:
        return ''


def classify_error(state):
    # Never copy raw messages that may contain identities, URLs or credentials.
    text = ' '.join(str(state.get(k, '')) for k in ('error', 'finish_error', 'stop_error'))
    patterns = [('旧记录尚未结束', '已有打开的跑步'), ('起点超出范围', '当前起点距'),
                ('定位异常', '位置异常'), ('定位异常', '定位异常'),
                ('需要登录', '登录'), ('准备或启动超时', '超时'),
                ('导航入口未识别', '入口'), ('模拟器不可用', '模拟器'),
                ('运动页面丢失或暂停', '运动界面已离开'), ('用户取消', '取消')]
    return next((label for label, keyword in patterns if keyword in text),
                '其他错误，请查看软件原始日志' if text else None)


def task_status():
    script = """
 $t = Get-ScheduledTask -TaskName 'VirtualRun-Morning-0700' -ErrorAction Stop
 $i = Get-ScheduledTaskInfo -TaskName 'VirtualRun-Morning-0700' -ErrorAction Stop
 [pscustomobject]@{state=[string]$t.State;next_run=$i.NextRunTime.ToString('o');last_run=$i.LastRunTime.ToString('o');last_result=$i.LastTaskResult;missed=$i.NumberOfMissedRuns} | ConvertTo-Json -Compress
 """
    try:
        powershell = str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
        return json.loads(command([powershell, '-NoProfile', '-NonInteractive', '-Command', script]))
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        return {'state': 'unavailable'}


def state_summary(state, expected):
    current = state.get('date') == expected and state.get('mode') == 'scheduled'
    result = {'current_day': current}
    if not current:
        return result
    for key in ('status', 'start_confirmed', 'finish_confirmed', 'started_at', 'completed_at'):
        result[key] = state.get(key)
    result['error_category'] = classify_error(state)
    return result


def track_summary(directory):
    result = {'points': 0, 'elapsed_s': None, 'distance_m': None, 'last_time': None}
    lines = tail(directory / 'track.jsonl').splitlines()
    for line in lines:
        try:
            row = json.loads(line)
            if isinstance(row.get('elapsed'), (int, float)) and isinstance(row.get('distance'), (int, float)):
                result.update(elapsed_s=round(row['elapsed'], 1), distance_m=round(row['distance'], 1),
                              last_time=row.get('time'))
                result['points'] += 1
        except (ValueError, TypeError):
            continue
    return result


def emulator_status(config):
    result = {'connected': False, 'gps_available': False}
    # Avoid even starting an ADB server during a diagnostic.
    try:
        with socket.create_connection(('127.0.0.1', 5037), timeout=1):
            pass
    except OSError:
        result['note'] = 'ADB 服务未运行；检查没有启动它或模拟器'
        return result
    executable = str(Path(config['sdk']) / 'platform-tools/adb.exe')
    def adb(args, serial=None):
        return command([executable] + (['-s', serial] if serial else []) + args)
    try:
        devices = [x.split()[0] for x in adb(['devices']).splitlines()
                   if x.startswith('emulator-') and x.split()[1:2] == ['device']]
        serial = next((s for s in devices if adb(['emu', 'avd', 'name'], s).splitlines()[0].strip() == config['avd']), None)
        if serial is None:
            return result
        result.update(connected=True, serial=serial,
                      boot_completed=adb(['shell', 'getprop', 'sys.boot_completed'], serial).strip() == '1')
        activity = adb(['shell', 'dumpsys', 'activity', 'activities'], serial)
        resumed = next((x for x in activity.splitlines() if 'mResumedActivity' in x or 'topResumedActivity' in x), '')
        match = re.search(re.escape(PACKAGE) + r'/([\w.$]+)', resumed)
        result['app_activity'] = match.group(1) if match else None
        result['sport_page_foreground'] = bool(match and 'SportMapActivity' in match.group(1))
        try:
            gps = parse_gps_location(adb(['shell', 'dumpsys', 'location'], serial))
            gap = round(distance_meters(gps, config['anchor_wgs84']))
            result.update(gps_available=True, gap_from_anchor_m=gap,
                          in_anchor_range=gap <= config.get('max_origin_gap_m', 2000))
        except RuntimeError:
            pass
    except (RuntimeError, OSError, KeyError, IndexError, subprocess.TimeoutExpired):
        result['note'] = '部分只读检查失败'
    return result


def conclusion(snapshot):
    current = snapshot['observed_at'][:10] == snapshot['expected_date']
    state = snapshot['daily']
    if not current:
        return '安装时基线检查，明日晨跑尚未发生。'
    clock = snapshot['observed_at'][11:16]
    if not state['current_day']:
        return '未发现当天定时任务状态；检查触发、停用标记及日志。'
    if state.get('status') in ('failed', 'cancelled'):
        return '任务失败或取消：' + str(state.get('error_category') or '请查看原始日志')
    if clock < '07:00':
        return '已创建当天状态，仍在准备；等待开始。'
    if clock < '08:00':
        if state.get('start_confirmed') and snapshot['track']['points']:
            return '脚本已确认开始，且有坐标发送记录；平台是否认可尚未验证。'
        return '尚未同时确认开始和坐标发送，需查看日志。'
    if state.get('status') == 'finished' and state.get('start_confirmed') and state.get('finish_confirmed'):
        return '脚本报告正常完成并确认结束；请在安小信核对运动记录及有效性。'
    return '结束状态未完整确认，需查看日志。'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--date', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    datetime.strptime(args.date, '%Y-%m-%d')
    path = Path(args.config)
    config = read_json(path)
    if not config:
        raise RuntimeError('无法读取晨跑配置')
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    now = datetime.now(TZ)
    state = read_json(path.parent / 'daily-state.json')
    daily = state_summary(state, args.date)
    directory = Path(state.get('log_directory', ''))
    # Only inspect runner logs below its own logs directory.
    valid = daily['current_day'] and directory.resolve().is_relative_to((path.parent / 'logs').resolve())
    track = track_summary(directory) if valid else track_summary(output / 'absent')
    log = tail(directory / 'run.log') if valid else ''
    snapshot = {'observed_at': now.isoformat(timespec='seconds'), 'expected_date': args.date,
                'task': task_status(), 'disabled': (path.parent / 'disabled.flag').exists(),
                'stop_requested': (path.parent / 'stop-all.request').exists(),
                'daily': daily, 'track': track, 'emulator': emulator_status(config),
                'log_signals': {label: phrase in log for label, phrase in
                                [('prepared', '起点准备完成'), ('failed', '任务失败'),
                                 ('cancelled', '任务已取消'), ('result_saved', '结果报告')]},
                'read_only': True, 'record_modified': False, 'coordinates_sent': False}
    snapshot['conclusion'] = conclusion(snapshot)
    destination = output / (now.strftime('%Y-%m-%d_%H%M%S') + '.json')
    destination.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    items = [read_json(p) for p in sorted(output.glob('*.json')) if p.name != 'registration.json']
    lines = ['# 次日晨跑检查报告', '', f'目标日期：{args.date}，北京时间 07:00–08:00。', '',
             '本检查仅读取状态，不点击、不发送定位、不开始或结束记录。坐标发送日志不能证明平台认可记录。', '',
             '| 检查时间 | 判断 | Windows 任务 | 脚本状态 | 发送里程（米） | 结束确认 |',
             '| --- | --- | --- | --- | --- | --- |']
    for item in items:
        if 'observed_at' not in item:
            continue
        lines.append(f"| {item['observed_at']} | {item['conclusion']} | {item['task']['state']} | {item['daily'].get('status', '无当天状态')} | {item['track']['distance_m']} | {item['daily'].get('finish_confirmed')} |")
    lines.extend(['', 'JSON 文件包含模拟器连接、活动页面、GPS 可用性及起点距离，未保存账号、密码、令牌或完整坐标。',
                  '若检查时间明显晚于计划时间，说明系统补执行，不能证明当时正在运行。'])
    (output / '晨跑检查报告.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({'snapshot': str(destination), 'conclusion': snapshot['conclusion']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
