"""GPS/app coordinate reading, with offline BD-09 conversion for tests.

Coordinate equations adapted from wandergis/coordtransform (MIT).
See THIRD_PARTY_LICENSES.txt. Reverse conversion is approximate; prefer an
aligned system GPS fix when available.
"""
import math
import re

R = 6371000.0


def distance_meters(a, b):
    p, q = math.radians(a[0]), math.radians(b[0])
    h = math.sin((q - p) / 2)**2 + math.cos(p) * math.cos(q) * math.sin(math.radians(b[1] - a[1]) / 2)**2
    return 2 * R * math.asin(min(1, math.sqrt(h)))


def in_china(lat, lon):
    return 3.86 < lat < 53.55 and 73.66 < lon < 135.05


def wgs_to_gcj(lat, lon):
    if not in_china(lat, lon):
        return lat, lon
    x, y = lon - 105, lat - 35
    common = (20 * math.sin(6 * x * math.pi) + 20 * math.sin(2 * x * math.pi)) * 2 / 3
    dy = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x)) + common
    dy += (20*math.sin(y*math.pi) + 40*math.sin(y/3*math.pi) +
           160*math.sin(y/12*math.pi) + 320*math.sin(y/30*math.pi)) * 2/3
    dx = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x)) + common
    dx += (20*math.sin(x*math.pi) + 40*math.sin(x/3*math.pi) +
           150*math.sin(x/12*math.pi) + 300*math.sin(x/30*math.pi)) * 2/3
    phi = math.radians(lat)
    ellipsoid = 1 - .00669342162296594323 * math.sin(phi)**2
    dy *= 180 / (6378245 * (1-.00669342162296594323) / ellipsoid**1.5 * math.pi)
    dx *= 180 / (6378245 / math.sqrt(ellipsoid) * math.cos(phi) * math.pi)
    return lat + dy, lon + dx


def wgs_to_baidu(lat, lon):
    if not in_china(lat, lon):
        return lat, lon
    y, x = wgs_to_gcj(lat, lon)
    k = math.pi * 3000 / 180
    radius = math.hypot(x, y) + .00002 * math.sin(y * k)
    angle = math.atan2(y, x) + .000003 * math.cos(x * k)
    return radius*math.sin(angle) + .006, radius*math.cos(angle) + .0065


def baidu_to_wgs(lat, lon):
    if not in_china(lat, lon):
        return lat, lon
    # Initial inverse of the BD-09 polar offset, then iteratively invert GCJ-02.
    x, y = lon - .0065, lat - .006
    k = math.pi * 3000 / 180
    radius = math.hypot(x, y) - .00002 * math.sin(y * k)
    angle = math.atan2(y, x) - .000003 * math.cos(x * k)
    gcj = radius*math.sin(angle), radius*math.cos(angle)
    p, q = gcj
    for _ in range(8):
        fp, fq = wgs_to_gcj(p, q)
        p -= fp - gcj[0]
        q -= fq - gcj[1]
    return p, q


def parse_gps_location(text):
    section = re.search(r'^\s*gps provider:\s*\n(.*?)(?=^\s{4}\S.*provider:|\Z)', text, re.M | re.S)
    scope = section.group(1) if section else text
    match = re.search(r'^\s*(?:last location|lastLocation)\s*=\s*Location\[gps\s+([-+\d.]+),\s*([-+\d.]+)', scope, re.M)
    if match:
        p, q = map(float, match.groups())
        if math.isfinite(p) and math.isfinite(q) and -90 <= p <= 90 and -180 <= q <= 180:
            return p, q
    raise RuntimeError('未读取到有效的系统 GPS。请开启定位并进入智慧运动地图页，再重试。')


def parse_app_location(text, device_time, max_age=20):
    prefix = r'(?:E/sport\s*\(\s*\d+\):|\d+\s+\d+\s+E\s+sport\s*:)'
    pattern = r'^\s*(\d+(?:\.\d+)?)\s+' + prefix + r'\s*([-+\d.]+)/([-+\d.]+)\s*$'
    matches = list(re.finditer(pattern, text, re.M))
    for match in reversed(matches):
        timestamp, lat, lon = map(float, match.groups())
        if not all(math.isfinite(v) for v in (timestamp, lat, lon)) or not (-90 <= lat <= 90 and -180 <= lon <= 180):
            continue
        age = device_time - timestamp
        if age < -2 or age > max_age:
            raise RuntimeError('安小信定位日志已过期。请进入智慧运动地图页，等待 GPS 更新后重新读取。')
        return lat, lon, timestamp
    raise RuntimeError('没有读到安小信定位日志。请进入智慧运动地图页，等待 GPS 更新后重新读取。')


def align_app_location(app_lat, app_lon, gps=None):
    if gps is not None and distance_meters(wgs_to_baidu(*gps), (app_lat, app_lon)) < 20:
        return (*gps, '安小信定位已与系统 GPS 对齐，使用精确 WGS84 起点')
    lat, lon = baidu_to_wgs(app_lat, app_lon)
    source = '安小信 BD-09 已近似换算为 WGS84' if in_china(app_lat, app_lon) else '安小信海外 WGS84 定位'
    return lat, lon, source
