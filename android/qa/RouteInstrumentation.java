package com.virtualrun.android;

import android.app.*;
import android.content.*;
import android.graphics.Bitmap;
import android.location.*;
import android.os.*;
import android.widget.*;
import java.io.*;
import java.lang.reflect.Field;

public final class RouteInstrumentation extends Instrumentation {
    @Override public void onCreate(Bundle arguments) { super.onCreate(arguments); start(); }
    private void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    private void waitMode(RunState.Mode desired, long timeout) throws Exception {
        long until = SystemClock.elapsedRealtime() + timeout;
        while (RunState.mode != desired && SystemClock.elapsedRealtime() < until) Thread.sleep(100);
        check(RunState.mode == desired, "Expected " + desired + ", got " + RunState.mode + ": " + RunState.message);
    }
    private void shell(String command) throws Exception {
        try (ParcelFileDescriptor descriptor = getUiAutomation().executeShellCommand(command);
             InputStream stream = new ParcelFileDescriptor.AutoCloseInputStream(descriptor)) {
            byte[] buffer = new byte[4096]; while (stream.read(buffer) != -1) { }
        }
    }
    private Object field(Object instance, String name) throws Exception {
        Field f = instance.getClass().getDeclaredField(name); f.setAccessible(true); return f.get(instance);
    }
    private void shot(String name) throws Exception {
        Bitmap bitmap = getUiAutomation().takeScreenshot(); check(bitmap != null, "screenshot");
        File directory = getTargetContext().getExternalMediaDirs()[0];
        if (!directory.isDirectory()) directory.mkdirs();
        try (OutputStream output = new FileOutputStream(new File(directory, name + ".png"))) {
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, output);
        } bitmap.recycle();
    }
    @Override public void onStart() {
        Bundle result = new Bundle();
        Context context = getTargetContext();
        try {
            final MainActivity activity = (MainActivity) startActivitySync(new Intent(context, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            Thread.sleep(700);
            runOnMainSync(() -> {
                try {
                    ((EditText)field(activity, "latitude")).setText("31.2304");
                    ((EditText)field(activity, "longitude")).setText("121.4737");
                    ((EditText)field(activity, "seconds")).setText("3");
                    ((EditText)field(activity, "countdown")).setText("1");
                } catch (Exception e) { throw new RuntimeException(e); }
            });
            shot("qa-main");
            LinearLayout tabs = (LinearLayout)field(activity, "tabs");
            runOnMainSync(() -> tabs.getChildAt(1).performClick()); Thread.sleep(400); shot("qa-guide");
            check(((LinearLayout)field(activity, "body")).getChildCount() > 0, "guide UI");
            runOnMainSync(() -> tabs.getChildAt(2).performClick()); Thread.sleep(400); shot("qa-readme");
            runOnMainSync(() -> tabs.getChildAt(0).performClick()); Thread.sleep(400);
            check(((EditText)field(activity, "latitude")).getText().toString().equals("31.2304"), "origin survives document tab changes");
            try (InputStream guide = context.getAssets().open("guide.md"); InputStream readme = context.getAssets().open("README.md")) {
                check(guide.available() > 1000 && readme.available() > 1000, "embedded documents");
            }
            RunConfig config = new RunConfig(31.2304, 121.4737, 150, 6, 3, 1);
            shell("appops set com.virtualrun.android android:mock_location deny");
            context.startForegroundService(config.put(new Intent(context, RunService.class).setAction(RunService.PREPARE)));
            waitMode(RunState.Mode.ERROR, 4000);
            check(RunState.message.contains("开发者"), "missing mock app permission explained");
            Thread.sleep(1000);
            shell("appops set com.virtualrun.android android:mock_location allow");
            context.startForegroundService(config.put(new Intent(context, RunService.class).setAction(RunService.PREPARE)));
            waitMode(RunState.Mode.READY, 19000);
            LocationManager lm = (LocationManager)context.getSystemService(Context.LOCATION_SERVICE);
            Location prepared = lm.getLastKnownLocation(LocationManager.GPS_PROVIDER);
            check(prepared != null && prepared.isFromMockProvider(), "mock flag stays present");
            check(RunConfig.meters(config.lat, config.lon, prepared.getLatitude(), prepared.getLongitude()) < .1, "stationary origin");
            check(prepared.getSpeed() == 0, "stationary velocity");
            shot("qa-ready");
            context.startForegroundService(config.put(new Intent(context, RunService.class).setAction(RunService.RUN)));
            waitMode(RunState.Mode.RUNNING, 4000);
            shell("input keyevent 3");
            waitMode(RunState.Mode.PAUSED, 6000);
            check(Math.abs(RunState.distance - 5) < .1, "6 km/h for three seconds is five meters while backgrounded");
            Location endpoint = lm.getLastKnownLocation(LocationManager.GPS_PROVIDER);
            check(endpoint != null && endpoint.getSpeed() == 0, "end keeps endpoint at zero speed");
            check(RunConfig.meters(config.lat, config.lon, endpoint.getLatitude(), endpoint.getLongitude()) > 4.8, "endpoint moves");
            shell("am start -W -n com.virtualrun.android/.MainActivity");
            Thread.sleep(500);
            RunConfig next = new RunConfig(config.lat, config.lon, config.radius, 7.2, 2, 0);
            context.startForegroundService(next.put(new Intent(context, RunService.class).setAction(RunService.RUN)));
            waitMode(RunState.Mode.RUNNING, 3000);
            waitMode(RunState.Mode.PAUSED, 5000);
            check(Math.abs(RunState.distance - 9) < .1, "continue adds distance without resetting origin");
            check(Math.abs(RunState.elapsed - 5) < .1, "elapsed excludes countdown");
            int samples = 0;
            try (BufferedReader input = new BufferedReader(new FileReader(RunState.trackFile))) {
                String line; while ((line = input.readLine()) != null) { new org.json.JSONObject(line); samples++; }
            }
            check(samples >= 5, "JSON track recorded");
            // A manual pause must also record its exact endpoint and preserve
            // it while waiting, rather than resetting to the route origin.
            RunConfig manual = new RunConfig(config.lat, config.lon, config.radius, 6, 10, 0);
            context.startForegroundService(manual.put(new Intent(context, RunService.class).setAction(RunService.RUN)));
            waitMode(RunState.Mode.RUNNING, 3000);
            Thread.sleep(1250);
            context.startService(new Intent(context, RunService.class).setAction(RunService.PAUSE));
            waitMode(RunState.Mode.PAUSED, 3000);
            double paused = RunState.distance;
            check(paused > 10.5, "manual pause advances distance");
            Thread.sleep(1200);
            check(RunState.distance == paused, "pause holds position");
            org.json.JSONObject last = null;
            try (BufferedReader input = new BufferedReader(new FileReader(RunState.trackFile))) {
                String line; while ((line = input.readLine()) != null) last = new org.json.JSONObject(line);
            }
            check(last != null && Math.abs(last.getDouble("distance") - paused) < .001, "manual endpoint exported");
            context.startService(new Intent(context, RunService.class).setAction(RunService.RELEASE));
            waitMode(RunState.Mode.IDLE, 3000);
            Thread.sleep(400);
            NotificationManager nm = (NotificationManager)context.getSystemService(Context.NOTIFICATION_SERVICE);
            check(nm.getActiveNotifications().length == 0, "foreground notification removed");
            result.putString("stream", "PASS: UI/documents, origin preserved, permission denial, 15s preparation, mock flag, background route, zero speed, continue, manual pause endpoint, JSON track, release\n");
            finish(Activity.RESULT_OK, result);
        } catch (Throwable error) {
            try { context.stopService(new Intent(context, RunService.class)); } catch (Exception ignored) { }
            StringWriter text = new StringWriter(); error.printStackTrace(new PrintWriter(text));
            result.putString("stream", "FAIL: " + text); finish(Activity.RESULT_CANCELED, result);
        }
    }
}
