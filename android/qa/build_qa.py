"""Build the separate instrumentation APK; never included in the release."""
from pathlib import Path
import json
import os
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent.parent
SDK = Path(r'C:\Program Files (x86)\Android\android-sdk')
JAVA = Path(r'C:\Program Files\Eclipse Adoptium\jdk-21.0.10.7-hotspot\bin')
TOOLS = SDK / 'build-tools/36.0.0'
JAR = SDK / 'platforms/android-35/android.jar'
OUT = ROOT / 'build/qa'
OUT.mkdir(parents=True, exist_ok=True)
ENV = os.environ.copy()
ENV['VIRTUALRUN_KEY_PASS'] = json.loads((ROOT / 'signing/local-signing.json').read_text())['password']


def run(command):
    output = subprocess.run([str(v) for v in command], cwd=ROOT, env=ENV, capture_output=True, encoding='utf-8', errors='replace')
    if output.returncode:
        raise RuntimeError(output.stdout + output.stderr)


run([TOOLS / 'aapt2.exe', 'link', '-I', JAR, '--manifest', 'qa/AndroidManifest.xml', '-o', 'build/qa/base.apk'])
classes = OUT / 'classes'
classes.mkdir(exist_ok=True)
run([JAVA / 'javac.exe', '-J-Duser.language=en', '-encoding', 'UTF-8', '-source', '8', '-target', '8',
     '-classpath', str(JAR) + os.pathsep + str(ROOT / 'build/classes'), '-d', classes, ROOT / 'qa/RouteInstrumentation.java'])
jar = OUT / 'classes.jar'
with zipfile.ZipFile(jar, 'w', zipfile.ZIP_DEFLATED) as archive:
    for file in classes.rglob('*.class'):
        archive.write(file, file.relative_to(classes).as_posix())
dex = OUT / 'dex'
dex.mkdir(exist_ok=True)
run([JAVA / 'java.exe', '-cp', TOOLS / 'lib/d8.jar', 'com.android.tools.r8.D8', '--min-api', '26',
     '--lib', JAR, '--classpath', ROOT / 'build/classes.jar', '--output', dex, jar])
with zipfile.ZipFile(OUT / 'base.apk', 'a', zipfile.ZIP_DEFLATED) as archive:
    archive.write(dex / 'classes.dex', 'classes.dex')
run([TOOLS / 'zipalign.exe', '-f', '4', 'build/qa/base.apk', 'build/qa/aligned.apk'])
run([JAVA / 'java.exe', '-jar', TOOLS / 'lib/apksigner.jar', 'sign', '--ks', ROOT / 'signing/virtualrun.jks',
     '--ks-pass', 'env:VIRTUALRUN_KEY_PASS', '--key-pass', 'env:VIRTUALRUN_KEY_PASS',
     '--out', OUT / 'checks.apk', OUT / 'aligned.apk'])
print('Built separate device check APK')
