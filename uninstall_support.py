"""Launch the native uninstall wizard without deleting anything in the GUI."""
import subprocess
from desktop_support import DATA_DIR


def launch_uninstaller():
    executable = DATA_DIR / '卸载虚拟跑步.exe'
    if not executable.is_file() or not (DATA_DIR / '.installation.json').is_file():
        raise RuntimeError('请先从新版 EXE 正常打开软件，登记安装信息后再卸载。')
    return subprocess.Popen([str(executable), '--data-dir', str(DATA_DIR)],
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
