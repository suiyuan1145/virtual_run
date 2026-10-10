package com.virtualrun.android;

public final class RunConfigTest {
    static void check(boolean value, String message) { if (!value) throw new AssertionError(message); }
    public static void main(String[] args) {
        RunConfig c = new RunConfig(31, 121, 150, 6, 30, 10);
        double[] start = RunConfig.position(c.lat, c.lon, c.radius, 0);
        check(Math.abs(start[0] - 31) < 1e-10 && Math.abs(start[1] - 121) < 1e-10, "origin");
        double[] loop = RunConfig.position(31, 121, 150, Math.PI * 300);
        check(RunConfig.meters(31, 121, loop[0], loop[1]) < .001, "closed loop");
        double[] end = RunConfig.position(31, 121, 150, 6 / 3.6 * 30);
        check(Math.abs(RunConfig.meters(31, 121, end[0], end[1]) - 50) < .3, "speed and distance");
        check(RunConfig.position(31, 179.999, 150, 1000)[1] >= -180, "longitude wrap");
        check(c.sameRoute(new RunConfig(31, 121, 150, 12, 20, 0)), "speed change allows continue");
        check(!c.sameRoute(new RunConfig(31, 121, 151, 6, 30, 10)), "radius change invalidates route");
        int rejected = 0;
        double[][] bad = {{Double.NaN,121,150,6,30}, {86,121,150,6,30}, {31,181,150,6,30},
                {31,121,0,6,30}, {31,121,150,31,30}, {31,121,150,6,0}};
        for (double[] v : bad) try { new RunConfig(v[0],v[1],v[2],v[3],v[4],10); } catch (IllegalArgumentException e) { rejected++; }
        check(rejected == bad.length, "invalid inputs");
        System.out.println("PASS: origin, closed loop, speed/distance, longitude, resume, invalid input");
    }
}
