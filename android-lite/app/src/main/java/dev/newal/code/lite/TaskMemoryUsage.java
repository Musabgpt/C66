package dev.newal.code.lite;

import android.content.Context;
import android.content.SharedPreferences;

/** How often each procedure ran and how often it finished, shown on the memory screen. */
final class TaskMemoryUsage {
    private static final String PREFS = "task_memory_usage";

    private TaskMemoryUsage() {
    }

    static synchronized void record(Context ctx, String ref, boolean done) {
        SharedPreferences p = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        p.edit().putInt("runs:" + ref, p.getInt("runs:" + ref, 0) + 1)
                .putInt("done:" + ref, p.getInt("done:" + ref, 0) + (done ? 1 : 0))
                .putInt("total_runs", p.getInt("total_runs", 0) + 1)
                .putInt("total_done", p.getInt("total_done", 0) + (done ? 1 : 0))
                .putString("last", ref).apply();
    }

    static int runs(Context ctx, String ref) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt("runs:" + ref, 0);
    }

    static int done(Context ctx, String ref) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt("done:" + ref, 0);
    }

    static int totalRuns(Context ctx) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt("total_runs", 0);
    }

    static int totalDone(Context ctx) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getInt("total_done", 0);
    }
}
