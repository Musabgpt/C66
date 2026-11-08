package dev.newal.code.lite;

import android.app.Notification;
import android.app.PendingIntent;
import android.app.RemoteInput;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Optional, user-enabled notification listener and inline action/reply node (inspired by OpenClaw Node's
 * DeviceNotificationListenerService). Reads incoming notifications and, when explicitly requested by the user's
 * agent tool call, can reply via RemoteInput, trigger an inline notification action, or dismiss a notification.
 */
public final class LocalNotificationAgentService extends NotificationListenerService {
    static volatile LocalNotificationAgentService on;
    private static final int MAX_LIVE = 60;
    private static final Map<String, LiveEntry> LIVE = Collections.synchronizedMap(
            new LinkedHashMap<String, LiveEntry>(64, 0.75f, false) {
                @Override
                protected boolean removeEldestEntry(Map.Entry<String, LiveEntry> eldest) {
                    return size() > MAX_LIVE;
                }
            });

    static final class LiveEntry {
        final String key;
        final String pkg;
        final String title;
        final String text;
        final long time;
        final Notification.Action replyAction;
        final RemoteInput[] remoteInputs;
        final List<Notification.Action> actions;

        LiveEntry(String key, String pkg, String title, String text, long time,
                  Notification.Action replyAction, RemoteInput[] remoteInputs,
                  List<Notification.Action> actions) {
            this.key = key;
            this.pkg = pkg;
            this.title = title;
            this.text = text;
            this.time = time;
            this.replyAction = replyAction;
            this.remoteInputs = remoteInputs;
            this.actions = actions;
        }
    }

    @Override
    public void onListenerConnected() {
        on = this;
        try {
            StatusBarNotification[] active = getActiveNotifications();
            if (active != null) {
                for (StatusBarNotification sbn : active) {
                    cacheLive(sbn);
                }
            }
        } catch (Exception ignored) {
        }
    }

    @Override
    public void onListenerDisconnected() {
        on = null;
    }

    @Override
    public void onNotificationRemoved(StatusBarNotification item) {
        if (item != null && item.getKey() != null) {
            LIVE.remove(item.getKey());
        }
    }

    private static LiveEntry cacheLive(StatusBarNotification item) {
        if (item == null || item.getNotification() == null) return null;
        Notification n = item.getNotification();
        Bundle e = n.extras != null ? n.extras : Bundle.EMPTY;
        CharSequence titleCs = e.getCharSequence(Notification.EXTRA_TITLE);
        CharSequence textCs = e.getCharSequence(Notification.EXTRA_BIG_TEXT);
        if (textCs == null || textCs.length() == 0) {
            textCs = e.getCharSequence(Notification.EXTRA_TEXT);
        }
        String title = titleCs == null ? "" : String.valueOf(titleCs);
        String text = textCs == null ? "" : String.valueOf(textCs);
        String key = item.getKey() != null ? item.getKey() : (item.getPackageName() + ":" + item.getId());
        Notification.Action replyAct = null;
        RemoteInput[] replyInputs = null;
        List<Notification.Action> actionList = new ArrayList<>();
        if (n.actions != null) {
            for (Notification.Action act : n.actions) {
                if (act == null) continue;
                actionList.add(act);
                RemoteInput[] inputs = act.getRemoteInputs();
                if (replyAct == null && inputs != null && inputs.length > 0 && act.actionIntent != null) {
                    replyAct = act;
                    replyInputs = inputs;
                }
            }
        }
        LiveEntry entry = new LiveEntry(key, item.getPackageName(), title, text, item.getPostTime(),
                replyAct, replyInputs, actionList);
        LIVE.put(key, entry);
        return entry;
    }

    static JSONObject toJsonRow(LiveEntry entry) throws Exception {
        JSONObject row = new JSONObject();
        row.put("time", entry.time);
        row.put("key", entry.key);
        row.put("package", entry.pkg);
        row.put("title", entry.title);
        row.put("text", entry.text);
        row.put("replyable", entry.replyAction != null);
        if (!entry.actions.isEmpty()) {
            JSONArray acts = new JSONArray();
            for (Notification.Action act : entry.actions) {
                if (act.title != null && act.title.length() > 0) {
                    acts.put(String.valueOf(act.title));
                }
            }
            if (acts.length() > 0) {
                row.put("actions", acts);
            }
        }
        return row;
    }

    @Override
    public void onNotificationPosted(StatusBarNotification item) {
        try {
            LiveEntry entry = cacheLive(item);
            if (entry == null) return;
            JSONObject row = toJsonRow(entry);
            synchronized (LocalNotificationAgentService.class) {
                File f = new File(getFilesDir(), "musabai-notifications.jsonl");
                try (FileOutputStream out = new FileOutputStream(f, true)) {
                    out.write((row + "\n").getBytes(StandardCharsets.UTF_8));
                }
            }
        } catch (Exception ignored) {
        }
    }

    static String read(Context context) throws Exception {
        return readFiltered(context, "", 0);
    }

    static String readFiltered(Context context, String query, int limit) throws Exception {
        File f = new File(context.getFilesDir(), "musabai-notifications.jsonl");
        String q = query == null ? "" : query.trim();
        if (!f.isFile() && LIVE.isEmpty()) {
            return "(no notification records)";
        }
        String raw = "";
        if (f.isFile()) {
            byte[] data = java.nio.file.Files.readAllBytes(f.toPath());
            raw = new String(data, StandardCharsets.UTF_8).substring(
                    Math.max(0, data.length > 20000 ? data.length - 20000 : 0));
        }
        if (q.isEmpty() && limit <= 0) {
            return raw.isEmpty() ? "(no notification records)" : raw;
        }
        List<String> matched = new ArrayList<>();
        for (String line : raw.split("\n")) {
            String trimmed = line.trim();
            if (trimmed.isEmpty()) continue;
            if (q.isEmpty() || TextFold.containsFolded(trimmed, q)) {
                matched.add(trimmed);
            }
        }
        if (limit > 0 && matched.size() > limit) {
            matched = matched.subList(matched.size() - limit, matched.size());
        }
        return matched.isEmpty() ? "(no matching notification records)" : String.join("\n", matched);
    }

    private static LiveEntry findTarget(String key, String pkgOrQuery, boolean requireReplyable) {
        List<LiveEntry> snapshot;
        synchronized (LIVE) {
            snapshot = new ArrayList<>(LIVE.values());
        }
        Collections.reverse(snapshot);
        String k = key == null ? "" : key.trim();
        String q = pkgOrQuery == null ? "" : pkgOrQuery.trim();
        for (LiveEntry e : snapshot) {
            if (requireReplyable && e.replyAction == null) continue;
            if (!k.isEmpty() && k.equals(e.key)) return e;
            if (!q.isEmpty()) {
                String hay = e.pkg + " " + e.title + " " + e.text;
                if (e.pkg.equalsIgnoreCase(q) || TextFold.containsFolded(hay, q)) {
                    return e;
                }
            } else if (k.isEmpty()) {
                return e;
            }
        }
        return null;
    }

    static JSONObject reply(Context ctx, JSONObject a) throws Exception {
        String text = a.optString("text", a.optString("reply", "")).trim();
        if (text.isEmpty()) {
            throw new IllegalArgumentException("notification_reply needs text (the reply message)");
        }
        String key = a.optString("key", "");
        String target = a.optString("target", a.optString("name", a.optString("package", a.optString("query", ""))));
        LiveEntry entry = findTarget(key, target, true);
        if (entry == null) {
            throw new IllegalStateException(on == null
                    ? "notification listener is off: enable NewAl Code in Settings > Notification access"
                    : "no replyable notification matches \"" + (target.isEmpty() ? key : target) + "\"");
        }
        Intent fill = new Intent();
        Bundle results = new Bundle();
        for (RemoteInput ri : entry.remoteInputs) {
            results.putCharSequence(ri.getResultKey(), text);
        }
        RemoteInput.addResultsToIntent(entry.remoteInputs, fill, results);
        try {
            entry.replyAction.actionIntent.send(ctx, 0, fill);
        } catch (PendingIntent.CanceledException e) {
            LIVE.remove(entry.key);
            throw new IllegalStateException("that notification's reply intent has expired");
        }
        return Phone.ok("replied to " + entry.pkg + " (" + entry.title + "): " + text);
    }

    static JSONObject triggerAction(Context ctx, JSONObject a) throws Exception {
        String key = a.optString("key", "");
        String target = a.optString("target", a.optString("name", a.optString("package", a.optString("query", ""))));
        String button = a.optString("button", a.optString("action_name", a.optString("label", ""))).trim();
        int index = a.optInt("item", a.optInt("index", 0));
        LiveEntry entry = findTarget(key, target, false);
        if (entry == null || entry.actions.isEmpty()) {
            throw new IllegalStateException(on == null
                    ? "notification listener is off: enable NewAl Code in Settings > Notification access"
                    : "no active notification with actions matches \"" + (target.isEmpty() ? key : target) + "\"");
        }
        Notification.Action chosen = null;
        if (index > 0 && index <= entry.actions.size()) {
            chosen = entry.actions.get(index - 1);
        } else if (!button.isEmpty()) {
            for (Notification.Action act : entry.actions) {
                String t = act.title == null ? "" : String.valueOf(act.title);
                if (TextFold.containsFolded(t, button)) {
                    chosen = act;
                    break;
                }
            }
        }
        if (chosen == null || chosen.actionIntent == null) {
            throw new IllegalArgumentException("no notification action matching \"" + button + "\" on " + entry.title);
        }
        chosen.actionIntent.send(ctx, 0, new Intent());
        return Phone.ok("triggered action \"" + chosen.title + "\" on " + entry.pkg + " (" + entry.title + ")");
    }

    static JSONObject dismiss(Context ctx, JSONObject a) throws Exception {
        LocalNotificationAgentService svc = on;
        if (svc == null) {
            throw new IllegalStateException("notification listener is off: enable NewAl Code in Settings > Notification access");
        }
        String key = a.optString("key", "");
        String target = a.optString("target", a.optString("name", a.optString("package", a.optString("query", ""))));
        if ("all".equalsIgnoreCase(target) || a.optBoolean("all", false)) {
            int count = LIVE.size();
            LIVE.clear();
            svc.cancelAllNotifications();
            return Phone.ok("dismissed " + count + " notifications");
        }
        LiveEntry entry = findTarget(key, target, false);
        if (entry == null) {
            throw new IllegalStateException("no active notification matches \"" + (target.isEmpty() ? key : target) + "\"");
        }
        LIVE.remove(entry.key);
        svc.cancelNotification(entry.key);
        return Phone.ok("dismissed notification from " + entry.pkg + " (" + entry.title + ")");
    }
}
