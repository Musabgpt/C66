package dev.newal.code.lite;

import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import org.json.JSONObject;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** Result callbacks for real Termux commands; pending jobs are never replayed on restart. */
final class TermuxJobs {
    private static final ConcurrentHashMap<String, CountDownLatch> waiting = new ConcurrentHashMap<>();
    private static ConnectorVault vault(Context c) { return new ConnectorVault(c); }

    static synchronized void disconnect(Context c) { vault(c).remove("termux"); }

    static JSONObject test(Context c) throws Exception {
        return run(c, "printf 'musabai-termux-ready'", true);
    }

    static JSONObject run(Context c, String command, boolean probe) throws Exception {
        if (!Termux.installed(c) || !Termux.allowed(c))
            throw new IllegalStateException("Grant Termux RUN_COMMAND permission and enable allow-external-apps in Termux");
        if (command.isEmpty() || command.length() > 131072) throw new IllegalArgumentException("Invalid command");
        if (!probe && !vault(c).get("termux").optString("status").equals("connected"))
            throw new IllegalStateException("Test Termux connection before running commands");
        String id = UUID.randomUUID().toString();
        JSONObject rec = new JSONObject().put("id", id).put("status", "running").put("probe", probe).put("started_at", System.currentTimeMillis());
        vault(c).put("job-" + id, rec);
        if (probe) synchronized (TermuxJobs.class) { vault(c).put("termux", new JSONObject().put("status", "testing").put("generation", id)); }
        CountDownLatch done = new CountDownLatch(1);
        waiting.put(id, done);
        Intent callback = new Intent(c, TermuxResultService.class).setData(Uri.parse("musabai-job://result/" + id));
        PendingIntent pending = PendingIntent.getService(c, 0, callback, PendingIntent.FLAG_MUTABLE | PendingIntent.FLAG_ONE_SHOT);
        Intent run = new Intent("com.termux.RUN_COMMAND").setClassName("com.termux", "com.termux.app.RunCommandService")
                .putExtra("com.termux.RUN_COMMAND_PATH", "/data/data/com.termux/files/usr/bin/bash")
                .putExtra("com.termux.RUN_COMMAND_ARGUMENTS", new String[]{"-lc", command})
                .putExtra("com.termux.RUN_COMMAND_WORKDIR", "/data/data/com.termux/files/home")
                .putExtra("com.termux.RUN_COMMAND_BACKGROUND", true)
                .putExtra("com.termux.RUN_COMMAND_PENDING_INTENT", pending);
        try {
            c.startForegroundService(run);
            done.await(probe ? 20 : 75, TimeUnit.SECONDS);
            JSONObject result = result(c, id);
            if (probe && (!result.optString("status").equals("completed") || !result.optBoolean("command_success"))) {
                synchronized (TermuxJobs.class) {
                    JSONObject state = vault(c).get("termux");
                    if (state.optString("generation").equals(id)) {
                        state.put("status", "error").put("error", "No successful callback received. Check allow-external-apps=true in Termux settings.");
                        vault(c).put("termux", state);
                    }
                }
                result.put("ok", false).put("error", "Termux test did not finish successfully; inspect job " + id);
            }
            return result;
        } catch (Exception e) {
            rec.put("status", "failed").put("error", "Termux command could not be started");
            vault(c).put("job-" + id, rec);
            pending.cancel();
            throw new IllegalStateException("Termux command could not be started");
        } finally { waiting.remove(id); }
    }

    static JSONObject result(Context c, String id) throws Exception {
        if (!id.matches("[0-9a-f-]{36}")) throw new IllegalArgumentException("Invalid job ID");
        JSONObject rec = vault(c).get("job-" + id);
        if (!rec.has("id")) throw new IllegalArgumentException("Unknown job ID");
        if (rec.optString("status").equals("running")) rec.put("text", "Awaiting callback; do not repeat the command. Read this job again.");
        return rec.put("ok", true);
    }

    static void receive(Context c, Intent intent) throws Exception {
        Uri uri = intent.getData();
        if (uri == null || !"musabai-job".equals(uri.getScheme())) return;
        String id = uri.getLastPathSegment();
        if (id == null || !id.matches("[0-9a-f-]{36}")) return;
        JSONObject rec = vault(c).get("job-" + id);
        if (!rec.optString("status").equals("running")) return;
        Bundle result = intent.getBundleExtra("result");
        String stdout = clip(result == null ? intent.getStringExtra("com.termux.RUN_COMMAND_RESULT_STDOUT") : result.getString("stdout", ""));
        String stderr = clip(result == null ? intent.getStringExtra("com.termux.RUN_COMMAND_RESULT_STDERR") : result.getString("stderr", ""));
        int exit = result == null ? intent.getIntExtra("com.termux.RUN_COMMAND_RESULT_EXIT_CODE", -1) : result.getInt("exitCode", -1);
        int error = result == null ? intent.getIntExtra("com.termux.RUN_COMMAND_RESULT_ERRNO", android.app.Activity.RESULT_OK) : result.getInt("err", android.app.Activity.RESULT_OK);
        boolean callbackOk = error == 0 || error == android.app.Activity.RESULT_OK;
        rec.put("status", callbackOk ? "completed" : "failed").put("exit_code", exit).put("stdout", stdout).put("stderr", stderr)
                .put("finished_at", System.currentTimeMillis()).put("command_success", callbackOk && exit == 0);
        if (!callbackOk) rec.put("error", "Termux execution error " + error);
        vault(c).put("job-" + id, rec);
        if (rec.optBoolean("probe")) {
            boolean success = callbackOk && exit == 0 && stdout.trim().equals("musabai-termux-ready");
            synchronized (TermuxJobs.class) {
                JSONObject state = vault(c).get("termux");
                if (state.optString("generation").equals(id)) {
                    state.put("status", success ? "connected" : "error").put("error", success ? "" : "Termux probe failed").put("tested_at", System.currentTimeMillis());
                    vault(c).put("termux", state);
                }
            }
        }
        CountDownLatch done = waiting.get(id);
        if (done != null) done.countDown();
    }

    private static String clip(String s) { return s.length() > 65536 ? s.substring(0, 65536) : s; }
}
