package com.virtualrun.android;

import android.content.Context;
import android.graphics.*;
import android.view.View;

final class RouteView extends View {
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    RouteView(Context context) { super(context); setContentDescription("环形路线预览"); }
    @Override protected void onDraw(Canvas canvas) {
        super.onDraw(canvas);
        float density = getResources().getDisplayMetrics().density;
        float w = getWidth(), h = getHeight(), r = Math.min(w, h) / 2 - 25 * density;
        canvas.drawColor(Color.rgb(18, 35, 55));
        paint.setColor(Color.rgb(93, 128, 150)); paint.setStyle(Paint.Style.STROKE); paint.setStrokeWidth(2 * density);
        paint.setPathEffect(new DashPathEffect(new float[] {6*density, 5*density}, 0));
        canvas.drawCircle(w/2, h/2, r, paint); paint.setPathEffect(null);
        double radius = RunState.config == null ? 150 : RunState.config.radius;
        float scale = (float)(r / radius);
        paint.setColor(Color.rgb(83, 222, 198)); paint.setStrokeWidth(3 * density);
        Path path = new Path(); boolean first = true;
        for (double[] point : RunState.points) {
            float x = w/2 + (float)point[0] * scale, y = h/2 - ((float)point[1] - (float)radius) * scale;
            if (first) { path.moveTo(x, y); first = false; } else path.lineTo(x, y);
        }
        canvas.drawPath(path, paint); paint.setStyle(Paint.Style.FILL);
        if (!RunState.points.isEmpty()) {
            double[] p = RunState.points.get(RunState.points.size() - 1);
            canvas.drawCircle(w/2 + (float)p[0]*scale, h/2 - ((float)p[1]-(float)radius)*scale, 5*density, paint);
        } else {
            paint.setColor(Color.rgb(175, 198, 216)); paint.setTextSize(13*density); paint.setTextAlign(Paint.Align.CENTER);
            canvas.drawText("准备起点后开始测试", w/2, h/2 + 4*density, paint);
        }
    }
}
