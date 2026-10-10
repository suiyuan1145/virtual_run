"""Create Windows shortcuts through the packaged native launcher."""
import json
from pathlib import Path
import subprocess
import uuid
from desktop_support import APP_DIR, DATA_DIR


def create_shortcuts(kind='all'):
    if kind not in ('desktop', 'start-menu', 'all'):
        raise ValueError('快捷方式选项无效。')
    launcher = APP_DIR / '虚拟跑步.exe'
    if not launcher.is_file():
        raise RuntimeError('请从完整软件包的 EXE 打开软件后创建快捷方式。')
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    report = DATA_DIR / ('shortcut-' + uuid.uuid4().hex + '.json')
    try:
        result = subprocess.run([str(launcher), '--create-shortcuts', kind, str(report)],
                                timeout=120, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if not report.is_file():
            raise RuntimeError('未收到创建结果，请重新打开 EXE 后重试。')
        value = json.loads(report.read_text(encoding='utf-8-sig'))
        if result.returncode or value.get('error'):
            raise RuntimeError(value.get('error') or '创建快捷方式失败。')
        return [Path(p) for p in value['paths']]
    finally:
        report.unlink(missing_ok=True)
