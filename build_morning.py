"""Package the daily scripts using the existing trusted Python interpreter."""
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'dist/morning'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # This source-only package uses the user's installed Python interpreter.
    for name in ('morning_run.py', 'morning_support.py', 'android_location_test.py', 'desktop_support.py', 'location_tools.py', 'login_support.py', 'emulator_preview.py', 'stop_support.py'):
        shutil.copyfile(ROOT / name, OUT / name)
    for source in (ROOT / 'morning').iterdir():
        if source.is_file() and (source.suffix in ('.ps1', '.cmd', '.py') or source.name == '使用方法.md'):
            if source.suffix == '.ps1':
                (OUT / source.name).write_text(source.read_text(encoding='utf-8-sig'), encoding='utf-8-sig')
            else:
                shutil.copyfile(source, OUT / source.name)
    shutil.copytree(ROOT / 'licenses', OUT / 'licenses', dirs_exist_ok=True)
    shutil.copyfile(ROOT / 'THIRD_PARTY_LICENSES.txt', OUT / 'THIRD_PARTY_LICENSES.txt')
    shutil.copyfile(ROOT / 'LICENSE', OUT / 'LICENSE')
    with zipfile.ZipFile(ROOT / 'dist/晨跑定时器-Windows.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in OUT.rglob('*'):
            if file.is_file() and '__pycache__' not in file.parts and 'logs' not in file.parts and file.name not in ('daily-state.json', 'morning.lock', 'login.dat', '晨跑配置.json', '即时测试配置.json', '验证结果.md', 'disabled.flag', 'stop-all.request') and file.suffix not in ('.exe', '.request', '.tmp'):
                archive.write(file, file.relative_to(OUT.parent))
    print('Built', OUT)


if __name__ == '__main__':
    main()
