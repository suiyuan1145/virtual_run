package com.virtualrun.android;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.location.*;
import android.os.*;
import org.json.JSONObject;
import java.io.*;
import java.text.SimpleDateFormat;
import java.util.*;

public final class RunService extends Service {
    static final String PREPARE = "prepare", RUN = "run", PAUSE = "pause", RELEASE = "release";
    private static final int NOTICE = 42;
    private static final String CHANNEL = "route_test";
    private final Handler handler = new Handler(Looper.getMainLooper());
    private LocationManager manager;
    private final ArrayList<String> providers = new ArrayList<>();
    private PowerManager.WakeLock wake;
    private BufferedWriter writer;
    private long phaseStart, lastNotice;
    private double distanceBefore, elapsedBefore;

    static boolean mockAllowed(Context context) {
        AppOpsManager ops = (AppOpsManager) context.getSystemService(APP_OPS_SERVICE);
        return ops.checkOpNoThrow(AppOpsManager.OPSTR_MOCK_LOCATION, android.os.Process.myUid(), context.getPackageName()) == AppOpsManager.MODE_ALLOWED;
    }
    @Override public void onCreate() {
        super.onCreate();
        manager = (LocationManager) getSystemService(LOCATION_SERVICE);
        NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "模拟定位测试", NotificationManager.IMPORTANCE_LOW));
        wake = ((PowerManager) getSystemService(POWER_SERVICE)).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "VirtualRun:route");
    }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) { stopSelf(); return START_NOT_STICKY; }
        String action = intent.getAction();
        if (RELEASE.equals(action)) { release(); return START_NOT_STICKY; }
        if (PAUSE.equals(action)) {
            if (RunState.mode == RunState.Mode.PREPARING) release();
            else if (RunState.active()) {
                try {
                    if (RunState.mode == RunState.Mode.RUNNING) { updateProgress(); record(); }
                    RunState.mode = RunState.Mode.PAUSED;
                    RunState.message = "已暂停，保持当前位置。请在安小信内自行暂停记录。";
                    unlock();
                    inject(RunState.lat, RunState.lon, 0); updateNotice();
                } catch (Exception e) { fail(e); }
            } else stopSelf();
            return START_NOT_STICKY;
        }
        try {
            // Promote immediately: startForegroundService has a short deadline,
            // including paths that reject a missing mock-location permission.
            foreground();
            if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED)
                throw new SecurityException("请先授予精确定位权限。");
            if (!manager.isProviderEnabled(LocationManager.GPS_PROVIDER))
                throw new IllegalStateException("请先开启手机系统定位。");
            if (!mockAllowed(this)) throw new SecurityException("请在开发者选项的“选择模拟位置信息应用”中选择“虚拟跑步”。");
            RunConfig requested = RunConfig.from(intent);
            if (PREPARE.equals(action)) {
                if (RunState.active()) throw new IllegalStateException("请先结束当前测试并恢复真实定位，再准备新的起点。");
                attachProviders();
                RunState.config = requested;
                RunState.distance = RunState.elapsed = 0;
                RunState.lat = requested.lat; RunState.lon = requested.lon;
                RunState.points.clear();
                openTrack();
                RunState.mode = RunState.Mode.PREPARING;
                phaseStart = SystemClock.elapsedRealtime();
                lock(120000);
                handler.removeCallbacks(tick);
                handler.post(tick);
            } else if (RUN.equals(action)) {
                if (RunState.mode != RunState.Mode.READY && RunState.mode != RunState.Mode.PAUSED)
                    throw new IllegalStateException("请先准备起点，或暂停后继续。");
                if (!requested.sameRoute(RunState.config)) throw new IllegalStateException("起点或半径已改变，请先结束测试再重新准备。");
                RunState.config = requested;
                distanceBefore = RunState.distance; elapsedBefore = RunState.elapsed;
                phaseStart = SystemClock.elapsedRealtime();
                RunState.mode = RunState.Mode.COUNTDOWN;
                lock((long)((requested.seconds + requested.countdown + 90) * 1000));
                handler.removeCallbacks(tick);
                handler.post(tick);
            } else throw new IllegalArgumentException("未知操作。");
        } catch (Exception e) { fail(e); }
        return START_NOT_STICKY;
    }
    private void foreground() {
        if (Build.VERSION.SDK_INT >= 29)
            startForeground(NOTICE, notification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
        else startForeground(NOTICE, notification());
    }
    @SuppressWarnings("deprecation") private void attachProviders() {
        for (String name : new String[] {LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER}) {
            // Do not remove any provider that this instance does not own.
            manager.addTestProvider(name, false, false, false, false, true, true, true, Criteria.POWER_LOW, Criteria.ACCURACY_FINE);
            providers.add(name);
            manager.setTestProviderEnabled(name, true);
        }
    }
    private void inject(double lat, double lon, double speedKmh) {
        double bearing = RunState.config == null ? 0 : (90 - Math.toDegrees(RunState.distance / RunState.config.radius) % 360 + 360) % 360;
        for (String provider : providers) {
            Location fix = new Location(provider);
            fix.setLatitude(lat); fix.setLongitude(lon); fix.setAltitude(0);
            fix.setAccuracy(5); fix.setSpeed((float)(speedKmh / 3.6)); fix.setBearing((float)bearing);
            fix.setTime(System.currentTimeMillis()); fix.setElapsedRealtimeNanos(SystemClock.elapsedRealtimeNanos());
            fix.setVerticalAccuracyMeters(5); fix.setSpeedAccuracyMetersPerSecond(.2f); fix.setBearingAccuracyDegrees(5);
            manager.setTestProviderLocation(provider, fix);
        }
    }
    private final Runnable tick = new Runnable() {
        @Override public void run() {
            try {
                RunConfig c = RunState.config;
                double seconds = (SystemClock.elapsedRealtime() - phaseStart) / 1000.0;
                if (RunState.mode == RunState.Mode.PREPARING) {
                    inject(c.lat, c.lon, 0);
                    if (seconds >= 15) {
                        RunState.mode = RunState.Mode.READY;
                        RunState.message = "起点已准备。先在安小信开始新记录，再回到这里开始测试。";
                        unlock();
                    } else RunState.message = "静止准备起点，还需 " + (int)Math.ceil(15 - seconds) + " 秒。请勿记录跑步。";
                } else if (RunState.mode == RunState.Mode.COUNTDOWN) {
                    inject(RunState.lat, RunState.lon, 0);
                    if (seconds >= c.countdown) {
                        RunState.mode = RunState.Mode.RUNNING;
                        phaseStart = SystemClock.elapsedRealtime();
                        RunState.message = "正在发送环形路线，切到安小信查看定位。";
                    } else RunState.message = "还有 " + (int)Math.ceil(c.countdown - seconds) + " 秒开始，可切回安小信。";
                } else if (RunState.mode == RunState.Mode.RUNNING) {
                    updateProgress();
                    inject(RunState.lat, RunState.lon, RunState.config.speed);
                    record();
                    if ((SystemClock.elapsedRealtime() - phaseStart) / 1000.0 >= c.seconds) {
                        RunState.mode = RunState.Mode.PAUSED;
                        RunState.message = "本段已结束，保持终点。可继续下一段；结束后再恢复真实定位。";
                        inject(RunState.lat, RunState.lon, 0);
                        unlock();
                    }
                } else if (RunState.mode == RunState.Mode.READY || RunState.mode == RunState.Mode.PAUSED) {
                    inject(RunState.lat, RunState.lon, 0);
                } else return;
                if (SystemClock.elapsedRealtime() - lastNotice >= 1000) { updateNotice(); lastNotice = SystemClock.elapsedRealtime(); }
                handler.postDelayed(this, 1000);
            } catch (Exception e) { fail(e); }
        }
    };
    private void updateProgress() {
        double seconds = Math.min((SystemClock.elapsedRealtime() - phaseStart) / 1000.0, RunState.config.seconds);
        RunState.elapsed = elapsedBefore + seconds;
        RunState.distance = distanceBefore + seconds * RunState.config.speed / 3.6;
        double[] p = RunConfig.position(RunState.config.lat, RunState.config.lon, RunState.config.radius, RunState.distance);
        RunState.lat = p[0]; RunState.lon = p[1];
    }
    private void record() throws Exception {
        JSONObject sample = new JSONObject();
        sample.put("elapsed", RunState.elapsed); sample.put("distance", RunState.distance);
        sample.put("lat", RunState.lat); sample.put("lon", RunState.lon);
        sample.put("speed_kmh", RunState.config.speed); sample.put("timestamp", System.currentTimeMillis());
        writer.write(sample.toString()); writer.newLine(); writer.flush();
        double[] p = RunConfig.position(RunState.config.lat, RunState.config.lon, RunState.config.radius, RunState.distance);
        RunState.points.add(new double[] {p[2], p[3]});
        if (RunState.points.size() > 5000) RunState.points.remove(0);
    }
    private void openTrack() throws IOException {
        File directory = new File(getFilesDir(), "tracks");
        if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("无法创建轨迹目录。");
        File file = new File(directory, "route-" + new SimpleDateFormat("yyyyMMdd-HHmmss-SSS", Locale.US).format(new Date()) + ".jsonl");
        writer = new BufferedWriter(new OutputStreamWriter(new FileOutputStream(file), "UTF-8"));
        RunState.trackFile = file.getAbsolutePath();
        getSharedPreferences("preferences", MODE_PRIVATE).edit().putString("last_track", RunState.trackFile).apply();
    }
    private Notification notification() {
        PendingIntent content = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent pause = PendingIntent.getService(this, 1, new Intent(this, RunService.class).setAction(PAUSE), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        PendingIntent release = PendingIntent.getService(this, 2, new Intent(this, RunService.class).setAction(RELEASE), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL).setSmallIcon(com.virtualrun.android.R.drawable.ic_launcher)
                .setContentTitle("虚拟跑步 · 定位测试")
                .setContentText(String.format(Locale.CHINA, "%.1f 米 · %s", RunState.distance, RunState.message))
                .setContentIntent(content).setOngoing(true).setOnlyAlertOnce(true)
                .addAction(new Notification.Action.Builder(null, "暂停", pause).build())
                .addAction(new Notification.Action.Builder(null, "恢复真实定位", release).build()).build();
    }
    private void updateNotice() { ((NotificationManager)getSystemService(NOTIFICATION_SERVICE)).notify(NOTICE, notification()); }
    private void lock(long timeout) { unlock(); wake.acquire(timeout); }
    private void unlock() { if (wake != null && wake.isHeld()) wake.release(); }
    private void clean() {
        handler.removeCallbacks(tick); unlock();
        for (String name : providers) { try { manager.removeTestProvider(name); } catch (Exception ignored) { } }
        providers.clear();
        if (writer != null) { try { writer.close(); } catch (IOException ignored) { } writer = null; }
        stopForeground(STOP_FOREGROUND_REMOVE);
    }
    private void release() {
        clean(); RunState.mode = RunState.Mode.IDLE;
        RunState.message = "已结束测试并恢复真实定位。下次请重新读取和准备起点。";
        stopSelf();
    }
    private void fail(Exception error) {
        clean(); RunState.mode = RunState.Mode.ERROR;
        RunState.message = "无法测试：" + (error.getMessage() == null ? error.getClass().getSimpleName() : error.getMessage());
        stopSelf();
    }
    @Override public void onDestroy() {
        clean();
        if (RunState.active()) { RunState.mode = RunState.Mode.IDLE; RunState.message = "定位服务已停止，请重新准备起点。"; }
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}
