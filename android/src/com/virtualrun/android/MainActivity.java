package com.virtualrun.android;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.location.*;
import android.net.Uri;
import android.os.*;
import android.provider.Settings;
import android.text.InputType;
import android.view.*;
import android.widget.*;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.*;
import java.util.*;

public final class MainActivity extends Activity {
    private static final int INK = Color.rgb(35, 54, 74), TEAL = Color.rgb(7, 135, 121);
    private final Handler handler = new Handler(Looper.getMainLooper());
    private LinearLayout body, tabs;
    private TextView status, metrics, permissions, originLabel;
    private EditText latitude, longitude, radius, speed, seconds, countdown;
    private Button prepare, start, pause, release, read;
    private RouteView route;
    private android.content.SharedPreferences preferences;
    private LocationManager locationManager;
    private LocationListener listener;
    private Runnable afterPermission;
    private boolean reading;
    private int page;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        preferences = getSharedPreferences("preferences", MODE_PRIVATE);
        locationManager = (LocationManager) getSystemService(LOCATION_SERVICE);
        getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL); root.setBackgroundColor(Color.rgb(242, 246, 248));
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets();
        });
        setContentView(root); root.requestApplyInsets();
        TextView title = text("虚拟跑步", 25, true); title.setPadding(dp(18), dp(16), dp(18), dp(4)); root.addView(title);
        TextView subtitle = text("安卓版 · 定位与路线测试", 13, false); subtitle.setPadding(dp(18), 0, dp(18), dp(10)); root.addView(subtitle);
        tabs = new LinearLayout(this); tabs.setPadding(dp(12), 0, dp(12), dp(5)); root.addView(tabs);
        String[] labels = {"跑步测试", "使用指南", "README"};
        for (int i = 0; i < labels.length; i++) {
            final int index = i;
            Button tab = button(labels[i], () -> showPage(index), false);
            tabs.addView(tab, weighted());
        }
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true);
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
        body = new LinearLayout(this); body.setOrientation(LinearLayout.VERTICAL); body.setPadding(dp(16), dp(4), dp(16), dp(24));
        scroll.addView(body);
        showPage(0);
        if (state != null && !RunState.active()) {
            latitude.setText(state.getString("origin_lat", ""));
            longitude.setText(state.getString("origin_lon", ""));
        }
    }
    private int dp(float value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private TextView text(String value, int size, boolean bold) {
        TextView v = new TextView(this); v.setText(value); v.setTextColor(INK); v.setTextSize(size);
        v.setLineSpacing(dp(3), 1);
        if (bold) v.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        return v;
    }
    private GradientDrawable background(int color) {
        GradientDrawable bg = new GradientDrawable(); bg.setColor(color); bg.setCornerRadius(dp(12)); return bg;
    }
    private LinearLayout card() {
        LinearLayout card = new LinearLayout(this); card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(14), dp(14), dp(14), dp(14)); card.setBackground(background(Color.WHITE));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2); lp.bottomMargin = dp(12); body.addView(card, lp); return card;
    }
    private LinearLayout.LayoutParams weighted() {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, -2, 1); lp.setMargins(dp(3), dp(3), dp(3), dp(3)); return lp;
    }
    private Button button(String label, Runnable action, boolean primary) {
        Button b = new Button(this); b.setText(label); b.setTextSize(14); b.setAllCaps(false);
        b.setMinHeight(dp(48)); b.setPadding(dp(6), dp(8), dp(6), dp(8));
        b.setBackground(background(primary ? TEAL : Color.rgb(229, 239, 242)));
        b.setTextColor(primary ? Color.WHITE : INK);
        b.setOnClickListener(v -> { try { action.run(); } catch (Exception error) { alert(error.getMessage()); } });
        return b;
    }
    private EditText field(LinearLayout row, String label, String value, boolean signed) {
        LinearLayout group = new LinearLayout(this); group.setOrientation(LinearLayout.VERTICAL);
        row.addView(group, weighted()); group.addView(text(label, 12, false));
        EditText entry = new EditText(this); entry.setSingleLine(true); entry.setTextSize(15); entry.setText(value);
        entry.setSelectAllOnFocus(true); entry.setMinHeight(dp(48));
        entry.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_FLAG_DECIMAL | (signed ? InputType.TYPE_NUMBER_FLAG_SIGNED : 0));
        group.addView(entry, new LinearLayout.LayoutParams(-1, -2)); return entry;
    }
    private void showPage(int index) {
        if (page == 0 && latitude != null) saveInputs();
        page = index; body.removeAllViews();
        for (int i = 0; i < tabs.getChildCount(); i++) {
            Button tab = (Button)tabs.getChildAt(i); tab.setTextColor(i == index ? Color.WHITE : INK);
            tab.setBackground(background(i == index ? TEAL : Color.rgb(229, 239, 242)));
        }
        if (index == 0) buildRunPage(); else document(index == 1 ? "guide.md" : "README.md");
    }
    private void buildRunPage() {
        LinearLayout access = card();
        access.addView(text("连接与权限", 17, true));
        permissions = text("", 12, false); access.addView(permissions);
        LinearLayout row = new LinearLayout(this); access.addView(row);
        row.addView(button("定位权限", () -> withLocation(() -> toast("已获得精确定位权限")), false), weighted());
        row.addView(button("开发者选项", () -> startActivity(new Intent(Settings.ACTION_APPLICATION_DEVELOPMENT_SETTINGS)), false), weighted());
        row = new LinearLayout(this); access.addView(row);
        read = button("读取系统位置", () -> withLocation(this::readOrigin), false); row.addView(read, weighted());
        row.addView(button("打开安小信", this::openTarget, false), weighted());
        originLabel = text("起点使用 WGS84。先读取系统 GPS；请在安小信地图中核对位置。", 12, false); access.addView(originLabel);

        LinearLayout form = card(); form.addView(text("起点与路线", 17, true));
        RunConfig c = RunState.active() ? RunState.config : null;
        String previousLat = latitude == null ? "" : latitude.getText().toString();
        String previousLon = longitude == null ? "" : longitude.getText().toString();
        row = new LinearLayout(this); form.addView(row);
        latitude = field(row, "纬度 WGS84", c == null ? previousLat : String.format(Locale.US, "%.8f", c.lat), true);
        longitude = field(row, "经度 WGS84", c == null ? previousLon : String.format(Locale.US, "%.8f", c.lon), true);
        row = new LinearLayout(this); form.addView(row);
        radius = field(row, "半径 / 米", c == null ? preferences.getString("radius", "150") : String.valueOf(c.radius), false);
        speed = field(row, "速度 / km/h", preferences.getString("speed", "6"), false);
        seconds = field(row, "本段时长 / 秒", preferences.getString("seconds", "30"), false);
        row = new LinearLayout(this); form.addView(row);
        countdown = field(row, "开始倒计时 / 秒", preferences.getString("countdown", "10"), false);
        TextView hint = text("6 km/h × 30 秒 ≈ 50 米\n倒计时便于切回安小信", 12, false); row.addView(hint, weighted());
        form.addView(text("先准备起点，再在安小信开始新记录。", 12, false));
        row = new LinearLayout(this); form.addView(row);
        prepare = button("准备起点（15秒）", () -> withLocation(() -> command(RunService.PREPARE)), true);
        start = button("开始 / 继续", () -> withLocation(() -> command(RunService.RUN)), true);
        row.addView(prepare, weighted()); row.addView(start, weighted());
        row = new LinearLayout(this); form.addView(row);
        pause = button("暂停定位", () -> control(RunService.PAUSE), false);
        release = button("恢复真实定位", () -> control(RunService.RELEASE), false);
        row.addView(pause, weighted()); row.addView(release, weighted());

        LinearLayout preview = card(); metrics = text("0.0 米 · 0.0 秒", 20, true); preview.addView(metrics);
        status = text(RunState.message, 13, false); status.setPadding(0, dp(5), 0, dp(10)); preview.addView(status);
        route = new RouteView(this); preview.addView(route, new LinearLayout.LayoutParams(-1, dp(190)));
        row = new LinearLayout(this); preview.addView(row);
        row.addView(button("导出轨迹 JSON", this::exportTrack, false), weighted());
        row.addView(button("应用系统设置", () -> startActivity(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getPackageName()))), false), weighted());
        TextView note = text("Android 模拟定位带有测试标记，安小信可能拒绝采用。先用短段验证。暂停保持当前位置；在安小信结束记录后，再恢复真实定位。", 12, false); body.addView(note);
        refreshUI();
    }
    private void document(String asset) {
        LinearLayout doc = card();
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(getAssets().open(asset), "UTF-8"))) {
            String line;
            while ((line = reader.readLine()) != null) {
                if (line.isEmpty()) continue;
                boolean title = line.startsWith("# "), heading = line.startsWith("## ");
                TextView paragraph = text(line.replaceFirst("^#+ ", "").replace("`", ""), title ? 24 : heading ? 18 : 14, title || heading);
                paragraph.setTextIsSelectable(true); paragraph.setPadding(0, dp(heading ? 16 : 4), 0, dp(8)); doc.addView(paragraph);
            }
        } catch (IOException e) { doc.addView(text("文档读取失败：" + e.getMessage(), 14, false)); }
    }
    private void saveInputs() {
        if (radius == null) return;
        preferences.edit().putString("radius", radius.getText().toString()).putString("speed", speed.getText().toString())
                .putString("seconds", seconds.getText().toString()).putString("countdown", countdown.getText().toString()).apply();
    }
    private RunConfig config() {
        try {
            return new RunConfig(Double.parseDouble(latitude.getText().toString()), Double.parseDouble(longitude.getText().toString()),
                    Double.parseDouble(radius.getText().toString()), Double.parseDouble(speed.getText().toString()),
                    Double.parseDouble(seconds.getText().toString()), Integer.parseInt(countdown.getText().toString()));
        } catch (NumberFormatException e) { throw new IllegalArgumentException("请先读取起点，并填写有效的参数；倒计时使用整数。"); }
    }
    private void command(String action) {
        if (!RunService.mockAllowed(this)) {
            alert("请进入开发者选项 → 选择模拟位置信息应用 → 选择“虚拟跑步”，然后返回这里。"); return;
        }
        if (!locationManager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
            alert("请先打开手机系统定位，再准备起点。"); return;
        }
        RunConfig requested = config(); saveInputs();
        startForegroundService(requested.put(new Intent(this, RunService.class).setAction(action)));
    }
    private void control(String action) {
        if (!RunState.active()) { toast("当前没有运行中的测试"); return; }
        startService(new Intent(this, RunService.class).setAction(action));
    }
    private void withLocation(Runnable action) {
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
            action.run(); askNotification(); return;
        }
        afterPermission = action;
        requestPermissions(new String[] {Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, 10);
    }
    private void askNotification() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            requestPermissions(new String[] {Manifest.permission.POST_NOTIFICATIONS}, 11);
    }
    @Override public void onRequestPermissionsResult(int code, String[] names, int[] results) {
        super.onRequestPermissionsResult(code, names, results);
        if (code == 10) {
            Runnable action = afterPermission; afterPermission = null;
            if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED) {
                try { if (action != null) action.run(); askNotification(); } catch (Exception e) { alert(e.getMessage()); }
            } else alert("需要精确定位权限。请在权限页面启用“使用精确位置”后再试。");
        }
    }
    private void readOrigin() {
        if (RunState.active()) { alert("模拟定位正在运行。先在安小信结束记录，再恢复真实定位，然后读取新的起点。"); return; }
        cancelRead();
        Location cached = null;
        for (String provider : new String[] {LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER}) {
            try {
                Location fix = locationManager.getLastKnownLocation(provider);
                if (usable(fix) && (cached == null || fix.getAccuracy() < cached.getAccuracy())) cached = fix;
            } catch (Exception ignored) { }
        }
        if (cached != null) { setOrigin(cached); return; }
        reading = true;
        listener = new LocationListener() {
            @Override public void onLocationChanged(Location fix) {
                if (usable(fix)) { setOrigin(fix); cancelRead(); }
            }
            @Override public void onProviderEnabled(String provider) { }
            @Override public void onProviderDisabled(String provider) { }
            @Override public void onStatusChanged(String provider, int status, Bundle extras) { }
        };
        boolean requested = false;
        for (String provider : new String[] {LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER}) {
            if (locationManager.isProviderEnabled(provider)) {
                locationManager.requestLocationUpdates(provider, 1000, 0, listener); requested = true;
            }
        }
        if (!requested) { cancelRead(); alert("请先打开手机定位开关。"); return; }
        handler.postDelayed(readTimeout, 25000);
    }
    private boolean usable(Location fix) {
        return fix != null && !fix.isFromMockProvider() && fix.hasAccuracy() && fix.getAccuracy() <= 100
                && SystemClock.elapsedRealtimeNanos() - fix.getElapsedRealtimeNanos() <= 60_000_000_000L;
    }
    private void setOrigin(Location fix) {
        latitude.setText(String.format(Locale.US, "%.8f", fix.getLatitude()));
        longitude.setText(String.format(Locale.US, "%.8f", fix.getLongitude()));
        originLabel.setText(String.format(Locale.CHINA, "已读取 %s · 精度约 %.0f 米，请在安小信核对位置。", fix.getProvider(), fix.getAccuracy()));
        toast("已读取系统位置");
    }
    private final Runnable readTimeout = () -> { cancelRead(); alert("没有获得近期的真实 GPS。到室外开启定位后重试，或手动填写 WGS84 起点。"); };
    private void cancelRead() {
        if (listener != null) { locationManager.removeUpdates(listener); listener = null; }
        reading = false; handler.removeCallbacks(readTimeout);
    }
    private void openTarget() {
        Intent launch = getPackageManager().getLaunchIntentForPackage("cn.edu.aiit.axx");
        if (launch == null) { alert("此设备尚未安装安小信。请先安装原版 APK。"); return; }
        startActivity(launch);
    }
    private void exportTrack() {
        String file = RunState.trackFile.isEmpty() ? preferences.getString("last_track", "") : RunState.trackFile;
        if (file.isEmpty()) { alert("没有可导出的轨迹，请先运行一段定位测试。"); return; }
        try {
            JSONArray samples = new JSONArray();
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(new FileInputStream(file), "UTF-8"))) {
                String line; while ((line = reader.readLine()) != null) if (!line.trim().isEmpty()) samples.put(new JSONObject(line));
            }
            if (samples.length() == 0) { alert("暂无移动轨迹，请先开始一段测试。"); return; }
            JSONObject data = new JSONObject(); data.put("type", "android-mock-location-test"); data.put("coordinates", "WGS84"); data.put("samples", samples);
            try (OutputStream cache = new FileOutputStream(new File(getCacheDir(), "export-track.json"))) {
                cache.write(data.toString(2).getBytes("UTF-8"));
            }
            Intent save = new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("application/json").putExtra(Intent.EXTRA_TITLE, "virtualrun-track.json");
            startActivityForResult(save, 20);
        } catch (Exception e) { alert("读取轨迹失败：" + e.getMessage()); }
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == 20 && result == RESULT_OK && data != null && data.getData() != null) {
            try (InputStream cache = new FileInputStream(new File(getCacheDir(), "export-track.json"));
                 OutputStream stream = getContentResolver().openOutputStream(data.getData(), "wt")) {
                if (stream == null) throw new IOException("无法打开保存位置");
                byte[] buffer = new byte[8192]; int size;
                while ((size = cache.read(buffer)) != -1) stream.write(buffer, 0, size);
                toast("轨迹已导出");
            } catch (IOException e) { alert("保存失败：" + e.getMessage()); }
        }
    }
    private void refreshUI() {
        if (page != 0 || permissions == null) return;
        boolean fine = checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED;
        boolean mock = RunService.mockAllowed(this), active = RunState.active();
        permissions.setText("精确定位：" + (fine ? "已授权" : "未授权") + "   模拟位置应用：" + (mock ? "已选择" : "未选择"));
        prepare.setEnabled(!active && !reading); read.setEnabled(!active && !reading);
        start.setEnabled(RunState.mode == RunState.Mode.READY || RunState.mode == RunState.Mode.PAUSED);
        pause.setEnabled(active && RunState.mode != RunState.Mode.PAUSED && RunState.mode != RunState.Mode.READY);
        release.setEnabled(active);
        latitude.setEnabled(!active); longitude.setEnabled(!active); radius.setEnabled(!active);
        boolean edit = !active || RunState.mode == RunState.Mode.READY || RunState.mode == RunState.Mode.PAUSED;
        speed.setEnabled(edit); seconds.setEnabled(edit); countdown.setEnabled(edit);
        metrics.setText(String.format(Locale.CHINA, "%.1f 米 · %.1f 秒", RunState.distance, RunState.elapsed));
        status.setText(reading ? "正在读取真实系统位置，最多等待 25 秒……" : RunState.message);
        route.invalidate();
    }
    private final Runnable update = new Runnable() {
        @Override public void run() { refreshUI(); handler.postDelayed(this, 300); }
    };
    @Override protected void onResume() { super.onResume(); handler.post(update); }
    @Override protected void onSaveInstanceState(Bundle state) {
        if (latitude != null) {
            state.putString("origin_lat", latitude.getText().toString());
            state.putString("origin_lon", longitude.getText().toString());
        }
        super.onSaveInstanceState(state);
    }
    @Override protected void onPause() { saveInputs(); handler.removeCallbacks(update); cancelRead(); super.onPause(); }
    @Override protected void onDestroy() { handler.removeCallbacks(update); cancelRead(); super.onDestroy(); }
    private void toast(String value) { Toast.makeText(this, value, Toast.LENGTH_SHORT).show(); }
    private void alert(String value) { new AlertDialog.Builder(this).setTitle("虚拟跑步").setMessage(value == null ? "操作失败" : value).setPositiveButton("知道了", null).show(); }
}
