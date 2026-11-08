package dev.newal.code.lite;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.RemoteInput;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

/**
 * Inline notification reply receiver (inspired by OpenClaw Node's ConversationReplyReceiver).
 * Allows the user to reply directly from a MusabAI notification in the Android notification shade.
 */
public final class NotificationReplyReceiver extends BroadcastReceiver {
    public static final String KEY_TEXT_REPLY = "key_text_reply";
    public static final String EXTRA_NOTIFICATION_ID = "notification_id";

    @Override
    public void onReceive(Context context, Intent intent) {
        if (context == null || intent == null) return;
        Bundle results = RemoteInput.getResultsFromIntent(intent);
        if (results == null) return;
        CharSequence cs = results.getCharSequence(KEY_TEXT_REPLY);
        String reply = cs == null ? "" : cs.toString().trim();
        if (reply.isEmpty()) return;

        Shared.fromNotificationReply(reply);
        try {
            JSONObject row = new JSONObject();
            row.put("time", System.currentTimeMillis());
            row.put("reply", reply);
            File f = new File(context.getFilesDir(), "musabai-user-replies.jsonl");
            try (FileOutputStream out = new FileOutputStream(f, true)) {
                out.write((row + "\n").getBytes(StandardCharsets.UTF_8));
            }
        } catch (Exception ignored) {
        }

        int id = intent.getIntExtra(EXTRA_NOTIFICATION_ID, 11);
        NotificationManager nm = context.getSystemService(NotificationManager.class);
        if (nm != null) {
            Notification confirmed = new Notification.Builder(context, "agent")
                    .setSmallIcon(R.drawable.ic_stat)
                    .setContentTitle("MusabAI")
                    .setContentText("تم استلام الرد: " + reply)
                    .setAutoCancel(true)
                    .build();
            nm.notify(id, confirmed);
        }
    }
}
