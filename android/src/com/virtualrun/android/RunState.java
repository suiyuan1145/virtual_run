package com.virtualrun.android;

import java.util.ArrayList;

final class RunState {
    enum Mode {IDLE, PREPARING, READY, COUNTDOWN, RUNNING, PAUSED, ERROR}
    static volatile Mode mode = Mode.IDLE;
    static volatile String message = "先读取系统位置，再准备起点。";
    static volatile double distance, elapsed, lat, lon;
    static volatile RunConfig config;
    static volatile String trackFile = "";
    static final ArrayList<double[]> points = new ArrayList<>();
    static boolean active() {
        return mode != Mode.IDLE && mode != Mode.ERROR;
    }
}
