"""Build a folder launcher and a self-contained Windows GUI executable."""
from pathlib import Path
import hashlib
import os
import subprocess
from desktop_support import VERSION

ROOT = Path(__file__).resolve().parent


def compile_launcher(destination, payload=None):
    framework = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319'
    compiler = framework / 'csc.exe'
    if not compiler.is_file():
        raise RuntimeError('未找到 Windows .NET Framework C# 编译器。')
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [str(compiler), '/nologo', '/target:winexe', '/platform:x64', '/optimize+',
               '/out:' + str(destination), '/win32icon:' + str(ROOT / 'assets/app.ico'),
               '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
               '/reference:System.Web.Extensions.dll',
               '/reference:' + str(framework / 'System.IO.Compression.dll')]
    if payload:
        checksum = hashlib.sha256(Path(payload).read_bytes()).hexdigest()
        metadata = ROOT / 'build/exe-payload.txt'
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(VERSION + '\n' + checksum + '\n', encoding='utf-8')
        command.extend(['/resource:' + str(payload) + ',VirtualRun.Payload',
                        '/resource:' + str(metadata) + ',VirtualRun.Metadata'])
    command.append(str(ROOT / 'launcher/VirtualRunLauncher.cs'))
    subprocess.run(command, check=True)
    return destination


def compile_uninstaller(destination):
    framework = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'Microsoft.NET/Framework64/v4.0.30319'
    subprocess.run([str(framework / 'csc.exe'), '/nologo', '/target:winexe', '/platform:x64',
                    '/optimize+', '/out:' + str(destination), '/win32icon:' + str(ROOT / 'assets/app.ico'),
                    '/reference:System.Windows.Forms.dll', '/reference:System.Drawing.dll',
                    '/reference:System.Web.Extensions.dll', '/reference:System.Management.dll',
                    str(ROOT / 'launcher/VirtualRunUninstaller.cs')], check=True)
    return Path(destination)


def main():
    from build_complete import main as build_package
    build_package()
    archive = ROOT / 'dist' / ('虚拟跑步-' + VERSION + '-完整软件包.zip')
    executable = compile_launcher(ROOT / 'dist' / ('虚拟跑步-' + VERSION + '-Windows.exe'), archive)
    print('Created:', executable)
    print('Size:', executable.stat().st_size)


if __name__ == '__main__':
    main()
