package dev.newal.code.lite;

import android.accessibilityservice.GestureDescription;
import android.content.Context;
import android.content.Intent;
import android.graphics.Path;
import android.graphics.Rect;
import android.view.accessibility.AccessibilityNodeInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Carries out a task-memory procedure on the phone, step by step, through the same phone actions the agent uses
 * (Phone.handle: open_app, tap, type, key, scroll...). A tap tries every label the step has (the English and the
 * Arabic interface, another app version) and waits for a slow screen; an optional step is skipped when its button is
 * not there. Where the screen differs from the procedure the run stops and says so, with the screen as it is now, so
 * the agent (or the person) goes on from there. Never runs on the main thread: taps wait for Android.
 */
final class TaskRunner {
    private TaskRunner() {
    }

    interface Listener {
        void step(int index, int total, String what);
    }

    /** What the runner needs from the phone: the phone tool's actions, launching an app, what is in front. On the
     *  phone it is Phone.handle and Android; the unit tests give it a simulated app (TaskRunnerTest). */
    interface Device {
        JSONObject act(JSONObject action) throws Exception;

        /** Starts the app when it is installed; false when it is not. */
        boolean launch(String pkg) throws Exception;

        /** The package in front, "" when unknown (no accessibility service). */
        String foreground();

        /** Whether the screen can be read and tapped (the accessibility service is on). */
        boolean screenControl();

        boolean longPress(String text) throws Exception;

        void sleep(long ms) throws InterruptedException;
    }

    static Device android(Context ctx) {
        return new Device() {
            @Override
            public JSONObject act(JSONObject action) throws Exception {
                return Phone.handle(ctx, action);
            }

            @Override
            public boolean launch(String pkg) {
                Intent launch = ctx.getPackageManager().getLaunchIntentForPackage(pkg);
                if (launch == null) {
                    return false;
                }
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_RESET_TASK_IF_NEEDED);
                ctx.startActivity(launch);
                return true;
            }

            @Override
            public String foreground() {
                PhoneControlService s = PhoneControlService.on;
                if (s == null) {
                    return "";
                }
                try {
                    AccessibilityNodeInfo root = s.getRootInActiveWindow();
                    return root == null || root.getPackageName() == null ? "" : root.getPackageName().toString();
                } catch (RuntimeException e) {
                    return "";
                }
            }

            @Override
            public boolean screenControl() {
                return PhoneControlService.on != null || A11y.enabled(ctx);
            }

            @Override
            public boolean longPress(String text) throws Exception {
                return TaskRunner.longPress(text);
            }

            @Override
            public void sleep(long ms) throws InterruptedException {
                Thread.sleep(ms);
            }
        };
    }

    static final class Result {
        boolean ok;
        boolean done;
        int stoppedAt = -1;
        String message = "";
        String screen = "";
        final List<String> log = new ArrayList<>();

        JSONObject toJson() throws Exception {
            JSONObject o = new JSONObject();
            o.put("ok", true);
            o.put("done", done);
            o.put("stopped_at", stoppedAt);
            o.put("text", message);
            o.put("log", new JSONArray(log));
            if (!screen.isEmpty()) {
                o.put("screen", screen);
            }
            return o;
        }
    }

    /** {1}, {2}... filled with the values (a value inside a link is URL-encoded). */
    static String fill(String s, List<String> values, boolean url) {
        if (s == null) {
            return "";
        }
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            int close = c == '{' ? s.indexOf('}', i) : -1;
            if (close > i + 1 && s.substring(i + 1, close).matches("\\d{1,2}")) {
                int k = Integer.parseInt(s.substring(i + 1, close)) - 1;
                String v = k >= 0 && k < values.size() ? values.get(k) : "";
                if (url) {
                    try {
                        v = URLEncoder.encode(v, "UTF-8");
                    } catch (Exception ignored) {
                        // UTF-8 is always there
                    }
                }
                out.append(v);
                i = close;
            } else {
                out.append(c);
            }
        }
        return out.toString();
    }

    /** Whether the procedure needs values the caller has not given ({n} with an empty value). */
    static List<Integer> missing(TaskMemory.Proc p, List<String> values) {
        List<Integer> out = new ArrayList<>();
        String text = p.steps().toString();
        for (int k = 1; k <= 9; k++) {
            if (text.contains("{" + k + "}") && (k > values.size() || values.get(k - 1).trim().isEmpty())) {
                out.add(k);
            }
        }
        return out;
    }

    static Result run(Context ctx, TaskMemory.Proc p, List<String> values, Listener listener, AtomicBoolean cancel) {
        return run(android(ctx), p, values, listener, cancel);
    }

    static Result run(Device ctx, TaskMemory.Proc p, List<String> values, Listener listener, AtomicBoolean cancel) {
        Result r = new Result();
        JSONArray steps = p.steps();
        if (!p.exec) {
            r.message = "Procedure " + p.ref() + " is a recorded demonstration (guidance), not a replay: follow its "
                    + "steps by hand, adapting to the screen. Steps: " + TaskMemory.describe(p);
            return r;
        }
        List<Integer> gaps = missing(p, values);
        if (!gaps.isEmpty()) {
            r.message = "Procedure " + p.ref() + " needs values for " + gaps + " (" + p.slotNames() + ")";
            return r;
        }
        boolean needsScreen = false;
        for (int i = 0; i < steps.length(); i++) {
            String a = steps.optJSONObject(i) == null ? "" : steps.optJSONObject(i).optString("a");
            needsScreen |= a.equals("tap") || a.equals("type") || a.equals("long_press") || a.equals("key")
                    || a.equals("scroll");
        }
        if (needsScreen && !ctx.screenControl()) {
            r.message = "ACCESSIBILITY_OFF: the taps need MusabAI's phone-control accessibility service. Turn it on in "
                    + "Settings > Accessibility > Installed apps > MusabAI: phone control.";
            return r;
        }
        for (int i = 0; i < steps.length(); i++) {
            if (cancel != null && cancel.get()) {
                r.stoppedAt = i;
                r.message = "Stopped by the person at step " + (i + 1) + ".";
                return r;
            }
            JSONObject s = steps.optJSONObject(i);
            if (s == null) {
                continue;
            }
            String what = label(s, values);
            if (listener != null) {
                listener.step(i, steps.length(), what);
            }
            try {
                String did = step(ctx, p, s, values);
                r.log.add((i + 1) + ". " + did);
            } catch (Exception e) {
                r.stoppedAt = i;
                r.screen = screen(ctx);
                r.message = "Stopped at step " + (i + 1) + " of " + steps.length() + " (" + what + "): "
                        + (e.getMessage() == null ? e.toString() : e.getMessage())
                        + (r.log.isEmpty() ? "" : ". Done before it: " + String.join("; ", r.log));
                return r;
            }
        }
        r.ok = true;
        String check = fill(p.check(), values, false).trim();
        r.screen = settle(ctx, check);
        if (check.isEmpty()) {
            r.done = true;
            r.message = "Done: " + steps.length() + " steps (" + String.join("; ", r.log) + ").";
        } else if (shows(r.screen, check)) {
            r.done = true;
            r.message = "Done: " + steps.length() + " steps; checked: \"" + check + "\" is on the screen.";
        } else {
            r.message = "All " + steps.length() + " steps ran, but \"" + check + "\" is not on the screen now, so it "
                    + "may not have gone through: look at the screen before saying it is done.";
        }
        return r;
    }

    private static String label(JSONObject s, List<String> values) {
        String a = s.optString("a");
        JSONArray t = s.optJSONArray("t");
        if (t != null && t.length() > 0) {
            return a + " \"" + fill(t.optString(0), values, false) + "\"";
        }
        if (s.has("text")) {
            return a + " \"" + fill(s.optString("text"), values, false) + "\"";
        }
        if (s.has("name")) {
            return a + " " + s.optString("name");
        }
        if (s.has("url")) {
            return a + " " + fill(s.optString("url"), values, true);
        }
        if (s.has("direction")) {
            return a + " " + s.optString("direction");
        }
        if ("phone".equals(a)) {
            return s.optString("action");
        }
        return a;
    }

    private static JSONObject call(Device ctx, String action, Object... kv) throws Exception {
        JSONObject o = new JSONObject().put("action", action);
        for (int i = 0; i + 1 < kv.length; i += 2) {
            o.put((String) kv[i], kv[i + 1]);
        }
        JSONObject res = ctx.act(o);
        if (res != null && !res.optBoolean("ok", true)) {
            throw new IllegalStateException(res.optString("error", res.optString("text", action + " failed")));
        }
        return res;
    }

    private static String step(Device ctx, TaskMemory.Proc p, JSONObject s, List<String> values) throws Exception {
        String a = s.optString("a");
        switch (a) {
            case "open_app":
                return openApp(ctx, p, s);
            case "open_url": {
                String url = fill(s.optString("url"), values, true);
                if (s.has("package")) {
                    call(ctx, "open_url", "url", url, "package", s.optString("package"));
                } else {
                    call(ctx, "open_url", "url", url);
                }
                ctx.sleep(1200);
                return "opened " + url;
            }
            case "tap":
            case "long_press":
                return tap(ctx, s, values, "long_press".equals(a));
            case "type": {
                String text = fill(s.optString("text"), values, false);
                call(ctx, "type", "text", text);
                return "typed \"" + text + "\"";
            }
            case "key": {
                String name = s.optString("name", "back");
                try {
                    call(ctx, "key", "name", name);
                } catch (Exception e) {
                    if (!"enter".equals(name)) {
                        throw e;
                    }
                    // no field takes "enter" here: the search button of the screen does the same
                    tapAny(ctx, new String[] {"Search", "بحث", "Go", "انتقال", "Done", "تم"}, 1);
                }
                return "pressed " + name;
            }
            case "scroll":
            case "swipe":
                call(ctx, a, "direction", s.optString("direction", "down"));
                return a + " " + s.optString("direction", "down");
            case "wait":
                ctx.sleep(Math.max(0, Math.min(10, s.optInt("seconds", 1))) * 1000L);
                return "waited";
            case "phone": {
                JSONObject o = new JSONObject();
                for (Iterator<String> it = s.keys(); it.hasNext(); ) {
                    String k = it.next();
                    if (k.equals("a") || k.equals("hint") || k.equals("final") || k.equals("optional")) {
                        continue;
                    }
                    Object v = s.get(k);
                    o.put(k, v instanceof String ? fill((String) v, values, false) : v);
                }
                JSONObject res = ctx.act(o);
                if (res != null && !res.optBoolean("ok", true)) {
                    throw new IllegalStateException(res.optString("error", "failed"));
                }
                return res == null ? o.optString("action") : res.optString("text", o.optString("action"));
            }
            default:
                throw new IllegalStateException("this step needs the agent: " + (s.has("hint") ? s.optString("hint") : a));
        }
    }

    private static String openApp(Device ctx, TaskMemory.Proc p, JSONObject s) throws Exception {
        List<String> pkgs = new ArrayList<>();
        if (!s.optString("pkg").isEmpty()) {
            pkgs.add(s.optString("pkg"));
        }
        JSONArray alt = s.optJSONArray("alt");
        for (int i = 0; alt != null && i < alt.length(); i++) {
            if (!pkgs.contains(alt.optString(i))) {
                pkgs.add(alt.optString(i));
            }
        }
        for (String pk : p.packages()) {
            if (!pkgs.contains(pk)) {
                pkgs.add(pk);
            }
        }
        for (String pk : pkgs) {
            if (ctx.launch(pk)) {
                waitFor(ctx, pk, 8000);
                return "opened " + s.optString("name", pk) + " (" + pk + ")";
            }
        }
        String name = s.optString("name", p.app);
        call(ctx, "open_app", "name", name);
        ctx.sleep(1500);
        return "opened " + name;
    }

    /** Waits until the app is in front (or the time is up): its first screen has to be there before a tap. */
    private static void waitFor(Device ctx, String pkg, long ms) throws InterruptedException {
        ctx.sleep(700);
        for (long waited = 700; waited < ms; waited += 300) {
            String front = ctx.foreground();
            if (front.isEmpty() && !ctx.screenControl()) {
                ctx.sleep(1500);
                return;
            }
            if (pkg.equals(front)) {
                ctx.sleep(600);
                return;
            }
            ctx.sleep(300);
        }
    }

    private static String tap(Device ctx, JSONObject s, List<String> values, boolean longPress) throws Exception {
        JSONArray t = s.optJSONArray("t");
        if (t == null || t.length() == 0) {
            if (s.optBoolean("optional")) {
                return "skipped (no label)";
            }
            throw new IllegalStateException("this step needs the agent: " + s.optString("hint", "an unnamed button"));
        }
        String[] labels = new String[t.length()];
        for (int i = 0; i < labels.length; i++) {
            labels[i] = fill(t.optString(i), values, false);
        }
        int rounds = s.optBoolean("optional") ? 1 : 6;
        if (longPress) {
            for (int round = 0; round < rounds; round++) {
                for (String l : labels) {
                    if (ctx.longPress(l)) {
                        return "long-pressed \"" + l + "\"";
                    }
                }
                ctx.sleep(700);
            }
        } else {
            String hit = tapAny(ctx, labels, rounds);
            if (hit != null) {
                return "tapped \"" + hit + "\"";
            }
        }
        if (s.optBoolean("optional")) {
            return "skipped \"" + labels[0] + "\" (not on this screen)";
        }
        throw new IllegalStateException("nothing on the screen says " + quoteAll(labels));
    }

    /** Taps the label that is on the screen, rounds times about 0.7 s apart. Of a step's labels the one that fits an
     *  item best is taken (the same words, then the start, then inside): "POST" must not tap the title "Create post"
     *  when the button says "نشر". */
    private static String tapAny(Device ctx, String[] labels, int rounds) throws Exception {
        for (int round = 0; round < rounds; round++) {
            String best = bestLabel(screen(ctx), labels);
            List<String> order = new ArrayList<>();
            if (best != null) {
                order.add(best);
            }
            for (String l : labels) {
                if (!l.trim().isEmpty() && !order.contains(l) && best == null) {
                    order.add(l);                  // no screen to compare with: each label in turn
                }
            }
            for (String l : order) {
                try {
                    call(ctx, "tap", "text", l);
                    return l;
                } catch (Exception e) {
                    String m = String.valueOf(e.getMessage());
                    if (m.contains("ACCESSIBILITY")) {
                        throw e;
                    }
                }
            }
            if (round + 1 < rounds) {
                ctx.sleep(700);
            }
        }
        return null;
    }

    private static final java.util.regex.Pattern ITEM =
            java.util.regex.Pattern.compile("^\\[(\\d+)\\]\\s+(.*?)(?:\\s+·\\s+[^·]*)?$");

    /** The label that best fits an item of the screen listing, null when none fits (or there is no listing). */
    static String bestLabel(String screen, String[] labels) {
        if (screen == null || screen.isEmpty()) {
            return null;
        }
        List<String> items = new ArrayList<>();
        for (String line : screen.split("\n")) {
            java.util.regex.Matcher m = ITEM.matcher(line.trim());
            if (m.matches()) {
                String t = m.group(2).trim();
                if (t.startsWith("hint: ")) {
                    t = t.substring(6);
                }
                items.add(TextFold.fold(t));
            }
        }
        String best = null;
        int bestScore = 0;
        for (String l : labels) {
            String f = TextFold.fold(l.trim());
            if (f.isEmpty()) {
                continue;
            }
            for (String it : items) {
                int s = it.equals(f) ? 3 : it.startsWith(f) ? 2 : it.contains(f) ? 1 : 0;
                if (s > bestScore) {
                    bestScore = s;
                    best = l;
                }
            }
        }
        return best;
    }

    private static String quoteAll(String[] labels) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < labels.length && i < 4; i++) {
            sb.append(i == 0 ? "" : " / ").append('"').append(labels[i]).append('"');
        }
        return sb.toString();
    }

    /** A long press (about 0.9 s) on what shows these words; false when nothing does. */
    static boolean longPress(String text) throws Exception {
        PhoneControlService s = PhoneControlService.on;
        if (s == null) {
            throw new IllegalStateException("ACCESSIBILITY_OFF: a long press needs the phone-control accessibility service");
        }
        AccessibilityNodeInfo root = s.getRootInActiveWindow();
        AccessibilityNodeInfo n = find(root, TextFold.fold(text), 0);
        if (n == null) {
            return false;
        }
        Rect r = new Rect();
        n.getBoundsInScreen(r);
        return press(s, r.centerX(), r.centerY(), 900);
    }

    static boolean press(PhoneControlService s, int x, int y, long ms) throws Exception {
        Path path = new Path();
        path.moveTo(Math.max(0, x), Math.max(0, y));
        GestureDescription g = new GestureDescription.Builder()
                .addStroke(new GestureDescription.StrokeDescription(path, 0, ms)).build();
        CountDownLatch done = new CountDownLatch(1);
        boolean[] ok = {false};
        boolean sent = Phone.onMain(() -> s.dispatchGesture(g, new android.accessibilityservice.AccessibilityService
                .GestureResultCallback() {
            @Override
            public void onCompleted(GestureDescription d) {
                ok[0] = true;
                done.countDown();
            }

            @Override
            public void onCancelled(GestureDescription d) {
                done.countDown();
            }
        }, null));
        return sent && done.await(5, TimeUnit.SECONDS) && ok[0];
    }

    private static AccessibilityNodeInfo find(AccessibilityNodeInfo n, String want, int depth) {
        if (n == null || depth > 60 || want.isEmpty()) {
            return null;
        }
        CharSequence t = n.getText() != null && n.getText().length() > 0 ? n.getText() : n.getContentDescription();
        if (t != null && n.isVisibleToUser() && TextFold.fold(t).contains(want)) {
            return n;
        }
        for (int i = 0; i < n.getChildCount(); i++) {
            AccessibilityNodeInfo f = find(n.getChild(i), want, depth + 1);
            if (f != null) {
                return f;
            }
        }
        return null;
    }

    private static String screen(Device ctx) {
        try {
            JSONObject o = ctx.act(new JSONObject().put("action", "screen"));
            return o == null ? "" : o.optString("text", "");
        } catch (Exception e) {
            return "";
        }
    }

    /** The screen once the checked text is on it (a post or a message can take a moment to show). */
    private static String settle(Device ctx, String want) {
        String screen = screen(ctx);
        for (int i = 0; i < 5 && !want.isEmpty() && !shows(screen, want); i++) {
            try {
                ctx.sleep(600);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
            screen = screen(ctx);
        }
        return screen;
    }

    static boolean shows(String screen, String text) {
        String t = TextFold.fold(text);
        if (t.length() > 40) {
            t = t.substring(0, 40);
        }
        return !t.isEmpty() && TextFold.fold(screen).contains(t);
    }

    static String utf8(byte[] b) {
        return new String(b, StandardCharsets.UTF_8);
    }
}
