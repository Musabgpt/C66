package dev.newal.code.lite;

import android.app.Notification;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import android.os.Bundle;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

/** Optional, user-enabled notification reader. It never sends or dismisses notifications. */
public final class LocalNotificationAgentService extends NotificationListenerService {
    static volatile LocalNotificationAgentService on;
    @Override public void onListenerConnected() { on = this; }
    @Override public void onListenerDisconnected() { on = null; }
    @Override public void onNotificationPosted(StatusBarNotification item) {
        try {
            Notification n = item.getNotification(); Bundle e = n.extras;
            JSONObject row = new JSONObject();
            row.put("time", item.getPostTime()); row.put("package", item.getPackageName());
            CharSequence title = e.getCharSequence(Notification.EXTRA_TITLE);
            CharSequence text = e.getCharSequence(Notification.EXTRA_TEXT);
            row.put("title", title == null ? "" : String.valueOf(title));
            row.put("text", text == null ? "" : String.valueOf(text));
            synchronized (LocalNotificationAgentService.class) {
                File f = new File(getFilesDir(), "musabai-notifications.jsonl");
                try (FileOutputStream out = new FileOutputStream(f, true)) { out.write((row + "\n").getBytes(StandardCharsets.UTF_8)); }
            }
        } catch (Exception ignored) { }
    }
    static String read(android.content.Context context) throws Exception {
        File f = new File(context.getFilesDir(), "musabai-notifications.jsonl");
        if (!f.isFile()) return "(no notification records)";
        byte[] data = java.nio.file.Files.readAllBytes(f.toPath());
        return new String(data, StandardCharsets.UTF_8).substring(Math.max(0, data.length > 20000 ? data.length - 20000 : 0));
    }
}
