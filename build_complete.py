"""Bundle CPython runtime, desktop sources, original APK and morning tools."""
from pathlib import Path
import json
import hashlib
import shutil
import sys
import zipfile
from desktop_support import VERSION, sdk_directory

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'dist' / ('虚拟跑步-' + VERSION)


def main():
    apk = ROOT / 'analysis/input.apk'
    if not apk.is_file():
        raise FileNotFoundError('开源仓库不包含第三方 APK。请先将有权使用的原版安装包放到 analysis/input.apk；详见 README。')
    OUT.mkdir(parents=True, exist_ok=True)
    runtime = OUT / 'runtime'
    runtime.mkdir(exist_ok=True)
    python_home = Path(sys.base_prefix)
    for pattern in ('python*.exe', 'python*.dll', 'vcruntime*.dll', 'LICENSE.txt'):
        for file in python_home.glob(pattern):
            shutil.copyfile(file, runtime / file.name)
    shutil.copytree(python_home / 'DLLs', runtime / 'DLLs', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('_test*', '__pycache__'))
    shutil.copytree(python_home / 'Lib', runtime / 'Lib', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('site-packages', '__pycache__', 'test', 'tests', 'ensurepip', 'idlelib'))
    shutil.copytree(python_home / 'tcl', runtime / 'tcl', dirs_exist_ok=True)
    for name in ('android_location_test.py', 'desktop_support.py', 'location_tools.py',
                 'android_setup.py', 'morning_run.py', 'morning_support.py', 'login_support.py', 'shortcut_support.py', 'uninstall_support.py', 'stop_support.py', 'click_run_test.py',
                 '测试自动点击跑步.cmd', '试跑晨跑60秒.cmd', 'README.md', 'LICENSE', 'THIRD_PARTY_LICENSES.txt'):
        shutil.copyfile(ROOT / name, OUT / name)
    for name in ('docs', 'assets', 'licenses'):
        shutil.copytree(ROOT / name, OUT / name, dirs_exist_ok=True)
    bundled = OUT / 'bundled-adb'
    bundled.mkdir(exist_ok=True)
    for name in ('adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll', 'NOTICE.txt'):
        shutil.copyfile(sdk_directory() / 'platform-tools' / name, bundled / name)
    morning = OUT / 'morning'
    morning.mkdir(exist_ok=True)
    for name in ('启动晨跑.ps1', '启用每日晨跑.ps1', '停用每日晨跑.ps1',
                 '启用每日晨跑.cmd', '停用每日晨跑.cmd', '检查晨跑环境.cmd',
                 '试跑60秒.ps1', '试跑60秒.cmd', '使用方法.md'):
        file = ROOT / 'morning' / name
        if file.suffix == '.ps1':
            (morning / file.name).write_text(file.read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
        else:
            shutil.copyfile(file, morning / file.name)
    (morning / '晨跑配置模板.json').write_text(json.dumps({'start': '07:00', 'end': '08:00',
                        'speed_kmh': 12, 'radius_m': 150, 'anchor_wgs84': None,
                        'headless': False}, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copyfile(apk, OUT / '安小信.apk')
    launcher = '@echo off\nchcp 65001 >nul\nsetlocal\ncd /d "%~dp0"\nstart "" "%~dp0虚拟跑步.exe"\n'
    (OUT / '启动虚拟跑步.cmd').write_text(launcher, encoding='utf-8')
    shutil.copyfile(ROOT / 'docs/首次安装与登录.md', OUT / '首次安装与登录.md')
    shutil.copyfile(ROOT / 'docs/使用指南.md', OUT / '使用指南.md')
    shutil.copyfile(ROOT / 'docs/快速开始.txt', OUT / '使用方法.txt')
    from build_exe import compile_launcher, compile_uninstaller
    compile_launcher(OUT / '虚拟跑步.exe')
    compile_uninstaller(OUT / '卸载虚拟跑步.exe')
    private_names = {'login.dat', 'settings.json', '晨跑配置.json', '即时测试配置.json', '验证结果.md',
                     'daily-state.json', 'morning.lock', 'disabled.flag', 'stop-all.request', '.complete', '.shortcuts-created', '.installation.json'}
    def packaged(file):
        return file.is_file() and '__pycache__' not in file.parts and 'logs' not in file.parts and file.name not in private_names and not file.name.endswith('.request')
    manifest = {'product': 'VirtualRunDesktop', 'version': VERSION, 'files': [
        {'path': file.relative_to(OUT).as_posix(), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}
        for file in sorted(OUT.rglob('*')) if packaged(file) and file.name != 'package-manifest.json']}
    (OUT / 'package-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    archive = ROOT / 'dist' / ('虚拟跑步-' + VERSION + '-完整软件包.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for file in OUT.rglob('*'):
            if packaged(file):
                z.write(file, file.relative_to(OUT.parent))
    print('Created:', archive)
    print('Size:', archive.stat().st_size)


if __name__ == '__main__':
    main()
