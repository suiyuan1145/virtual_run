"""Create private, portable morning configuration from the desktop UI."""
import json
from pathlib import Path
import shutil
import sys
from desktop_support import APP_DIR, DATA_DIR, RESOURCE_DIR, VERSION


def runtime_python():
    for candidate in (APP_DIR / 'runtime/python.exe',
                      APP_DIR / 'dist' / ('虚拟跑步-' + VERSION) / 'runtime/python.exe'):
        if candidate.is_file():
            return candidate
    return Path(sys.executable).with_name('python.exe')


def configure(sdk, avd, lat, lon, speed=12, radius=150, auto_login=False, headless=False):
    from morning_run import validate
    config = validate({'python': str(runtime_python()), 'sdk': str(sdk), 'avd': avd,
                       'start': '07:00', 'end': '08:00', 'speed_kmh': float(speed),
                       'radius_m': float(radius), 'anchor_wgs84': [float(lat), float(lon)],
                       'max_origin_gap_m': 2000})
    if not runtime_python().is_file():
        raise RuntimeError('未找到软件自带的 Python 运行环境。')
    target = DATA_DIR / 'morning'
    target.mkdir(parents=True, exist_ok=True)
    if auto_login and not (target / 'login.dat').is_file():
        raise RuntimeError('请先填写账号密码，勾选“为晨跑保存登录信息”并保存。')
    config.update(auto_login=bool(auto_login), credential_file=str(target / 'login.dat'),
                  headless=bool(headless))
    copy_runtime_files(target)
    temporary = target / 'config.tmp'
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target / '晨跑配置.json')
    return target


def copy_runtime_files(target):
    for name in ('morning_run.py', 'android_location_test.py', 'desktop_support.py', 'location_tools.py', 'login_support.py', 'stop_support.py'):
        shutil.copyfile(RESOURCE_DIR / name, target / name)
    for name in ('启动晨跑.ps1', '启用每日晨跑.ps1', '停用每日晨跑.ps1',
                 '启用每日晨跑.cmd', '停用每日晨跑.cmd', '检查晨跑环境.cmd',
                 '试跑60秒.ps1', '试跑60秒.cmd', '使用方法.md'):
        source = RESOURCE_DIR / 'morning' / name
        if source.suffix == '.ps1':
            (target / name).write_text(source.read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
        else:
            shutil.copyfile(source, target / name)


def configure_test(sdk, avd, speed, radius, headless=False):
    """Use a separate config and the shared lock without altering daily settings."""
    from morning_run import validate
    config = validate({'python': str(runtime_python()), 'sdk': str(sdk), 'avd': avd,
                       'start': '07:00', 'end': '08:00', 'speed_kmh': float(speed),
                       'radius_m': float(radius), 'anchor_wgs84': None,
                       'max_origin_gap_m': 2000, 'headless': bool(headless)}, auto_origin=True)
    if not runtime_python().is_file():
        raise RuntimeError('未找到 Python 运行环境。')
    target = DATA_DIR / 'morning'
    target.mkdir(parents=True, exist_ok=True)
    try:
        daily = json.loads((target / '晨跑配置.json').read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        daily = {}
    config.update(auto_login=bool(daily.get('auto_login', True)) and (target / 'login.dat').is_file(),
                  credential_file=str(target / 'login.dat'))
    copy_runtime_files(target)
    path = target / '即时测试配置.json'
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)
    return path
