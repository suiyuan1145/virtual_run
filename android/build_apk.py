"""Dependency-free Android build using installed SDK tools and Java 21."""
from pathlib import Path
import argparse
import json
import os
import secrets
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent


def run(command, environment):
    result = subprocess.run([str(item) for item in command], env=environment, cwd=ROOT,
                            capture_output=True, encoding='utf-8', errors='replace')
    if result.returncode:
        raise RuntimeError(result.stdout + '\n' + result.stderr)
    if result.stdout.strip():
        print(result.stdout.strip())


def build():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sdk', default=r'C:\Program Files (x86)\Android\android-sdk')
    parser.add_argument('--java', default=r'C:\Program Files\Eclipse Adoptium\jdk-21.0.10.7-hotspot')
    args = parser.parse_args()
    sdk, java = Path(args.sdk), Path(args.java)
    tools = sdk / 'build-tools/36.0.0'
    android_jar = sdk / 'platforms/android-35/android.jar'
    if not android_jar.is_file():
        raise RuntimeError('需要 Android SDK Platform 35 和 Build Tools 36.0.0。')
    environment = os.environ.copy()
    environment['JAVA_HOME'] = str(java)
    environment['PATH'] = str(java / 'bin') + os.pathsep + environment.get('PATH', '')
    output = ROOT / 'build'
    output.mkdir(exist_ok=True)
    assets = ROOT / 'assets'
    assets.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / 'README.md', assets / 'README.md')
    shutil.copyfile(ROOT / '使用指南.md', assets / 'guide.md')
    compiled = output / 'resources.zip'
    # Native SDK tools on Windows handle non-ASCII working directories but some
    # versions cannot open absolute UTF-8 filenames. Use relative project paths.
    run([tools / 'aapt2.exe', 'compile', '--dir', 'res', '-o', 'build/resources.zip'], environment)
    generated = output / 'generated'
    generated.mkdir(exist_ok=True)
    base = output / 'resources.apk'
    run([tools / 'aapt2.exe', 'link', '-I', android_jar, '--manifest', 'AndroidManifest.xml',
         '--java', 'build/generated', '--auto-add-overlay', '-A', 'assets',
         '-o', 'build/resources.apk', 'build/resources.zip'], environment)
    classes = output / 'classes'
    classes.mkdir(exist_ok=True)
    sources = list((ROOT / 'src').rglob('*.java')) + list(generated.rglob('*.java'))
    run([java / 'bin/javac.exe', '-J-Duser.language=en', '-J-Duser.country=US', '-encoding', 'UTF-8', '-source', '8', '-target', '8',
         '-classpath', android_jar, '-d', classes, *sources], environment)
    jar = output / 'classes.jar'
    with zipfile.ZipFile(jar, 'w', zipfile.ZIP_DEFLATED) as archive:
        for file in classes.rglob('*.class'):
            archive.write(file, file.relative_to(classes).as_posix())
    dex = output / 'dex'
    dex.mkdir(exist_ok=True)
    run([java / 'bin/java.exe', '-cp', tools / 'lib/d8.jar', 'com.android.tools.r8.D8',
         '--release', '--min-api', '26', '--lib', android_jar, '--output', dex, jar], environment)
    unsigned = output / 'unsigned.apk'
    shutil.copyfile(base, unsigned)
    with zipfile.ZipFile(unsigned, 'a', zipfile.ZIP_DEFLATED) as archive:
        for file in dex.glob('*.dex'):
            archive.write(file, file.name)
    aligned = output / 'aligned.apk'
    run([tools / 'zipalign.exe', '-f', '-p', '4', 'build/unsigned.apk', 'build/aligned.apk'], environment)
    signing = ROOT / 'signing'
    signing.mkdir(exist_ok=True)
    settings_file = signing / 'local-signing.json'
    if settings_file.is_file():
        signing_settings = json.loads(settings_file.read_text(encoding='utf-8'))
    else:
        signing_settings = {'password': secrets.token_urlsafe(32)}
        settings_file.write_text(json.dumps(signing_settings), encoding='utf-8')
    environment['VIRTUALRUN_KEY_PASS'] = signing_settings['password']
    keystore = signing / 'virtualrun.jks'
    if not keystore.is_file():
        run([java / 'bin/keytool.exe', '-genkeypair', '-keystore', keystore, '-storetype', 'JKS',
             '-alias', 'virtualrun', '-keyalg', 'RSA', '-keysize', '3072', '-validity', '10000',
             '-storepass:env', 'VIRTUALRUN_KEY_PASS', '-keypass:env', 'VIRTUALRUN_KEY_PASS',
             '-dname', 'CN=VirtualRun Android, O=Local Development, C=CN'], environment)
    release = PROJECT / 'dist/android'
    release.mkdir(parents=True, exist_ok=True)
    apk = release / '虚拟跑步-Android-1.0.0.apk'
    run([java / 'bin/java.exe', '-jar', tools / 'lib/apksigner.jar', 'sign', '--ks', keystore,
         '--ks-key-alias', 'virtualrun', '--ks-pass', 'env:VIRTUALRUN_KEY_PASS',
         '--key-pass', 'env:VIRTUALRUN_KEY_PASS', '--out', apk, aligned], environment)
    run([java / 'bin/java.exe', '-jar', tools / 'lib/apksigner.jar', 'verify', '--verbose', apk], environment)
    shutil.copyfile(ROOT / 'README.md', release / 'README.md')
    shutil.copyfile(ROOT / '使用指南.md', release / '使用指南.md')
    print(f'APK built: {apk}')


if __name__ == '__main__':
    build()
