"""Download official SDK archives and create an AVD without Java/Android Studio."""
from __future__ import annotations
import copy
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

REPOSITORY = 'https://dl.google.com/android/repository/repository2-1.xml'
IMAGES = 'https://dl.google.com/android/repository/sys-img/google_apis/sys-img2-1.xml'
IMAGE = 'system-images;android-30;google_apis;x86_64'
REQUIRED = {'emulator': 'emulator.exe', 'platform-tools': 'adb.exe', IMAGE: 'system.img'}


def local(tag):
    return tag.rsplit('}', 1)[-1]


def child(element, name):
    return next((e for e in element if local(e.tag) == name), None)


def value(element, name, default=''):
    e = child(element, name)
    return e.text.strip() if e is not None and e.text else default


def packages(metadata, base_url, wanted):
    root = ET.fromstring(metadata)
    licenses = {e.get('id'): e.text or '' for e in root if local(e.tag) == 'license'}
    namespaces = dict(item for _, item in ET.iterparse(io.BytesIO(metadata), events=['start-ns']))
    found = {}
    for e in root:
        name = e.get('path')
        if local(e.tag) != 'remotePackage' or name not in wanted or child(e, 'obsolete') is not None:
            continue
        channel = child(e, 'channelRef')
        if channel is not None and channel.get('ref') != 'channel-0':
            continue
        revision = child(e, 'revision')
        version = tuple(int(value(revision, k, '0')) for k in ('major', 'minor', 'micro'))
        if child(revision, 'preview') is not None:
            continue
        archives = child(e, 'archives')
        for archive in archives:
            if value(archive, 'host-os', 'windows') != 'windows' or value(archive, 'host-bits', '64') != '64':
                continue
            complete = child(archive, 'complete')
            url = urllib.parse.urljoin(base_url, value(complete, 'url'))
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme != 'https' or parsed.hostname != 'dl.google.com':
                raise RuntimeError('下载清单包含非官方地址，已取消。')
            checksum = child(complete, 'checksum')
            algorithm = checksum.get('type', 'sha1') if checksum is not None else ''
            digest = (checksum.text or '').strip() if checksum is not None else ''
            if algorithm not in ('sha1', 'sha256') or not re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', digest):
                raise RuntimeError('官方清单缺少支持的校验值。')
            license_id = child(e, 'uses-license').get('ref')
            candidate = {'name': name, 'version': version, 'url': url, 'size': int(value(complete, 'size')),
                         'algorithm': algorithm, 'digest': digest.lower(), 'license_id': license_id,
                         'license': licenses[license_id], 'element': e, 'namespaces': namespaces}
            if name not in found or version > found[name]['version']:
                found[name] = candidate
    if set(found) != set(wanted):
        raise RuntimeError('官方清单未找到所需的稳定版 Windows 组件。')
    return list(found.values())


def opener(proxy=''):
    if proxy:
        p = urllib.parse.urlparse(proxy)
        if p.scheme not in ('http', 'https') or not p.hostname:
            raise ValueError('代理地址示例：http://127.0.0.1:7897')
        return urllib.request.build_opener(urllib.request.ProxyHandler({'http': proxy, 'https': proxy}))
    return urllib.request.build_opener()


def digest_file(path, algorithm):
    h = hashlib.new(algorithm)
    with path.open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def download(package, cache, connection, stop, report):
    name = Path(urllib.parse.urlparse(package['url']).path).name
    final = cache / name
    if final.is_file() and final.stat().st_size == package['size'] and digest_file(final, package['algorithm']) == package['digest']:
        report('使用已校验的下载缓存：' + name)
        return final
    partial = cache / (name + '.part')
    offset = partial.stat().st_size if partial.exists() else 0
    if offset >= package['size']:
        partial.unlink()
        offset = 0
    request = urllib.request.Request(package['url'], headers={'Range': f'bytes={offset}-'} if offset else {})
    with connection.open(request, timeout=45) as response:
        if offset and response.status == 206:
            if not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
                raise RuntimeError('服务器断点响应不正确。')
        else:
            offset = 0
        written, notified = offset, 0
        with partial.open('ab' if offset else 'wb') as target:
            while True:
                if stop.is_set():
                    raise RuntimeError('下载已停止；下次可以续传。')
                block = response.read(1024 * 1024)
                if not block:
                    break
                target.write(block)
                written += len(block)
                if time.monotonic() - notified >= 5:
                    report(f'{name}：{written / 1048576:.0f} / {package["size"] / 1048576:.0f} MB')
                    notified = time.monotonic()
    if partial.stat().st_size != package['size'] or digest_file(partial, package['algorithm']) != package['digest']:
        partial.rename(cache / (name + '.bad-' + str(time.time_ns())))
        raise RuntimeError('下载大小或校验值不符，未安装该组件；请重试。')
    partial.replace(final)
    return final


def extract_checked(archive, directory, stop):
    base = directory.resolve()
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            if stop.is_set():
                raise RuntimeError('解压已停止。')
            name = PurePosixPath(entry.filename.replace('\\', '/'))
            if name.is_absolute() or '..' in name.parts or ':' in entry.filename:
                raise RuntimeError('压缩包包含越界路径。')
            target = directory.joinpath(*name.parts)
            if not target.resolve().is_relative_to(base) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise RuntimeError('压缩包包含不安全的链接或路径。')
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(entry) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)


def package_path(sdk, name):
    return sdk.joinpath(*name.split(';'))


def install_archive(package, archive, sdk, stop):
    destination = package_path(sdk, package['name'])
    if destination.exists():
        raise RuntimeError(f'组件目录已有不完整安装，请检查或更换 SDK 目录：{destination}')
    stage = Path(tempfile.mkdtemp(prefix='.virtualrun-', dir=sdk))
    try:
        extract_checked(archive, stage, stop)
        root_name = package['name'].split(';')[-1]
        source = stage / root_name
        if not (source / REQUIRED[package['name']]).is_file():
            raise RuntimeError('官方压缩包结构与预期不符。')
        repository = ET.Element('repository', {
            'xmlns:' + prefix: uri for prefix, uri in package['namespaces'].items()
            if prefix and prefix not in ('xsi',) and not re.fullmatch(r'ns\d+', prefix)
        })
        element = copy.deepcopy(package['element'])
        element.tag = 'localPackage'
        for e in list(element):
            if local(e.tag) in ('archives', 'channelRef'):
                element.remove(e)
        repository.append(element)
        ET.ElementTree(repository).write(source / 'package.xml', encoding='utf-8', xml_declaration=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
    finally:
        # Only delete this call's staging directory within the chosen SDK.
        if stage.resolve().is_relative_to(sdk.resolve()) and stage.name.startswith('.virtualrun-'):
            shutil.rmtree(stage)


def create_avd(sdk, name='VirtualRun_API30', avd_home=None):
    if not re.fullmatch(r'[A-Za-z0-9_]+', name):
        raise ValueError('AVD 名称请使用字母、数字或下划线。')
    home = Path(avd_home or os.environ.get('ANDROID_AVD_HOME') or Path.home() / '.android/avd')
    home.mkdir(parents=True, exist_ok=True)
    directory = home / (name + '.avd')
    index = home / (name + '.ini')
    if directory.exists() or index.exists():
        if not index.is_file():
            raise RuntimeError('已有 AVD 目录但缺少索引；未覆盖，请更换 AVD 名称。')
        data = dict(line.split('=', 1) for line in index.read_text(encoding='utf-8-sig').splitlines() if '=' in line)
        config = Path(data.get('path', directory)) / 'config.ini'
        if not config.is_file():
            raise RuntimeError('已有 AVD 配置不完整；未覆盖，请更换名称。')
        return False
    directory.mkdir()
    config = {'AvdId': name, 'avd.ini.displayname': name, 'avd.ini.encoding': 'UTF-8',
              'abi.type': 'x86_64', 'tag.id': 'google_apis', 'tag.display': 'Google APIs',
              'image.sysdir.1': str(package_path(sdk.resolve(), IMAGE)) + os.sep,
              'hw.cpu.arch': 'x86_64', 'hw.cpu.ncore': str(min(4, os.cpu_count() or 2)),
              'hw.ramSize': '2048', 'hw.keyboard': 'yes', 'hw.gpu.enabled': 'yes',
              'hw.gpu.mode': 'swiftshader', 'hw.lcd.width': '1080', 'hw.lcd.height': '2220',
              'hw.lcd.density': '440', 'hw.mainKeys': 'no', 'disk.dataPartition.size': '4G',
              'skin.name': '1080x2220', 'skin.path': '1080x2220', 'fastboot.forceColdBoot': 'yes'}
    (directory / 'config.ini').write_text(''.join(f'{k}={v}\n' for k, v in config.items()), encoding='utf-8')
    index.write_text(f'avd.ini.encoding=UTF-8\npath={directory.resolve()}\ntarget=android-30\n', encoding='utf-8')
    return True


def install(sdk, cache, avd, proxy, accepted, stop, report, avd_home=None):
    if not accepted:
        raise RuntimeError('请先阅读并勾选 Android SDK 许可。')
    if platform.machine().lower() not in ('amd64', 'x86_64'):
        raise RuntimeError('此安装流程面向 64 位 Intel/AMD Windows 电脑。')
    sdk, cache = Path(sdk).resolve(), Path(cache).resolve()
    sdk.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)
    missing = {k for k, f in REQUIRED.items() if not (package_path(sdk, k) / f).is_file()}
    if missing:
        if shutil.disk_usage(sdk).free < 12 * 1024 ** 3:
            raise RuntimeError('SDK 所在磁盘至少需要 12 GB 可用空间。')
        connection = opener(proxy)
        selected = []
        for url, keys in ((REPOSITORY, missing - {IMAGE}), (IMAGES, missing & {IMAGE})):
            if keys:
                report('读取官方组件清单……')
                with connection.open(url, timeout=45) as response:
                    metadata = response.read()
                selected += packages(metadata, url, keys)
        report(f'将下载 {sum(p["size"] for p in selected) / 1048576:.0f} MB，校验后安装。')
        for p in selected:
            if stop.is_set():
                raise RuntimeError('安装已停止。')
            report('下载：' + p['name'])
            archive = download(p, cache, connection, stop, report)
            report('校验完成，解压：' + p['name'])
            install_archive(p, archive, sdk, stop)
            license_dir = sdk / 'licenses'
            license_dir.mkdir(exist_ok=True)
            hashed = hashlib.sha1(p['license'].encode('utf-8')).hexdigest()
            existing = license_dir / p['license_id']
            hashes = set(existing.read_text().split()) if existing.exists() else set()
            hashes.add(hashed)
            existing.write_text('\n'.join(sorted(hashes)) + '\n', encoding='utf-8')
    created = create_avd(sdk, avd, avd_home)
    report('已创建虚拟设备。' if created else '已有虚拟设备已保留，包括其中的登录和应用数据。')
    report('环境准备完成。接下来启动模拟器、安装内置 APK，再登录安小信。')
