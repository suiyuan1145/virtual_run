package com.virtualrun.android;

import android.content.Intent;

final class RunConfig {
    final double lat, lon, radius, speed, seconds;
    final int countdown;
    RunConfig(double lat, double lon, double radius, double speed, double seconds, int countdown) {
        if (!Double.isFinite(lat) || !Double.isFinite(lon) || !Double.isFinite(radius)
                || !Double.isFinite(speed) || !Double.isFinite(seconds) || lat < -85 || lat > 85
                || lon < -180 || lon > 180 || radius < 10 || radius > 10000
                || speed < .5 || speed > 30 || seconds < 1 || seconds > 3600
                || countdown < 0 || countdown > 60) {
            throw new IllegalArgumentException("纬度 ±85、经度 ±180；半径 10–10000 米；速度 0.5–30 km/h；时长 1–3600 秒；倒计时 0–60 秒。");
        }
        this.lat = lat; this.lon = lon; this.radius = radius;
        this.speed = speed; this.seconds = seconds; this.countdown = countdown;
    }
    boolean sameRoute(RunConfig other) {
        return other != null && lat == other.lat && lon == other.lon && radius == other.radius;
    }
    Intent put(Intent intent) {
        return intent.putExtra("lat", lat).putExtra("lon", lon).putExtra("radius", radius)
                .putExtra("speed", speed).putExtra("seconds", seconds).putExtra("countdown", countdown);
    }
    static RunConfig from(Intent intent) {
        return new RunConfig(intent.getDoubleExtra("lat", Double.NaN), intent.getDoubleExtra("lon", Double.NaN),
                intent.getDoubleExtra("radius", 150), intent.getDoubleExtra("speed", 6),
                intent.getDoubleExtra("seconds", 30), intent.getIntExtra("countdown", 10));
    }
    static double[] position(double lat, double lon, double radius, double distance) {
        double a = distance / radius;
        double east = radius * Math.sin(a), north = radius * (1 - Math.cos(a));
        double bearing = Math.atan2(east, north), angle = Math.hypot(east, north) / 6371000;
        double phi = Math.toRadians(lat), lam = Math.toRadians(lon);
        double p = Math.asin(Math.sin(phi) * Math.cos(angle) + Math.cos(phi) * Math.sin(angle) * Math.cos(bearing));
        double l = lam + Math.atan2(Math.sin(bearing) * Math.sin(angle) * Math.cos(phi),
                Math.cos(angle) - Math.sin(phi) * Math.sin(p));
        return new double[] {Math.toDegrees(p), (Math.toDegrees(l) + 540) % 360 - 180, east, north};
    }
    static double meters(double lat, double lon, double p, double q) {
        double x = Math.toRadians(p - lat), y = Math.toRadians(q - lon);
        double h = Math.sin(x/2) * Math.sin(x/2) + Math.cos(Math.toRadians(lat))
                * Math.cos(Math.toRadians(p)) * Math.sin(y/2) * Math.sin(y/2);
        return 12742000 * Math.asin(Math.min(1, Math.sqrt(h)));
    }
}
