package dev.newal.code.lite;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.os.Build;
import android.util.Log;

import java.io.File;
import java.util.ArrayList;
import java.util.List;

/**
 * Wakes the app for its scheduled tasks (connectors/scheduled_tasks.py). The engine looks at its tasks every ten
 * seconds only while it runs, and Android stops a background app's process to free memory: a reminder or a task due
 * then waited until the app was opened again. Now the next due time is an alarm, which starts the engine's service at
 * that time; its scheduler runs what is due. The engine asks for a new alarm after every change to its tasks
 * ("task_alarm"), and the phone sets it again after a restart or an update of the app (TaskAlarmReceiver). The time
 * is read from the tasks' own database, so neither the engine nor the network is needed for it.
 */
final class TaskAlarms {
    static final String ACTION = "dev.newal.code.lite.TASK_DUE";
    /** A task already due when the alarm is set (the engine runs it once it is up): look again in a minute. */
    static final long RETRY_MS = 60_000L;
    /** The engine could not be started: the user was told, so the next look waits longer. */
    static final long REFUSED_RETRY_MS = 15 * 60_000L;

    private TaskAlarms() {
    }

    static File database(Context c) {
        return new File(new Setup(c).home, ".newal-code/scheduled-tasks.sqlite3");
    }

    /** The earliest due time of an active task, in epoch milliseconds, or 0 when none is waiting. */
    static long nextDue(Context c) {
        File db = database(c);
        if (!db.isFile()) return 0;
        try (SQLiteDatabase d = SQLiteDatabase.openDatabase(db.getPath(), null, SQLiteDatabase.OPEN_READONLY);
             Cursor q = d.rawQuery("SELECT MIN(next_run) FROM scheduled_tasks WHERE status = 'active'", null)) {
            if (q.moveToFirst() && !q.isNull(0)) return (long) (q.getDouble(0) * 1000);
        } catch (RuntimeException e) {
            Log.w("NewAlCode", "scheduled tasks unreadable", e);
        }
        return 0;
    }

    /** Sets (or clears) the alarm for the next due task. */
    static void schedule(Context c) {
        schedule(c, RETRY_MS);
    }

    static void schedule(Context c, long retryMs) {
        AlarmManager am = c.getSystemService(AlarmManager.class);
        if (am == null) return;
        PendingIntent pi = PendingIntent.getBroadcast(c, 0,
                new Intent(c, TaskAlarmReceiver.class).setAction(ACTION),
                PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        long due = nextDue(c);
        if (due <= 0) {
            am.cancel(pi);
            return;
        }
        long now = System.currentTimeMillis();
        long at = due > now ? due : now + retryMs;
        try {
            if (Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()) {
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);
            } else {
                am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);   // Doze may move it by some minutes
            }
        } catch (SecurityException e) {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi);
        }
    }

    /**
     * The engine could not be started from the background (Android refused the service): the user is told what is
     * due, and opening the app runs it.
     */
    static void notifyDue(Context c) {
        List<String> titles = new ArrayList<>();
        File db = database(c);
        if (db.isFile()) {
            try (SQLiteDatabase d = SQLiteDatabase.openDatabase(db.getPath(), null, SQLiteDatabase.OPEN_READONLY);
                 Cursor q = d.rawQuery("SELECT title FROM scheduled_tasks WHERE status = 'active' AND next_run <= ? "
                         + "ORDER BY next_run LIMIT 3", new String[] {String.valueOf(System.currentTimeMillis() / 1000.0)})) {
                while (q.moveToNext()) titles.add(q.getString(0));
            } catch (RuntimeException e) {
                Log.w("NewAlCode", "scheduled tasks unreadable", e);
            }
        }
        if (titles.isEmpty()) return;
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        if (nm == null) return;
        nm.createNotificationChannel(new NotificationChannel("agent", "NewAl Code: from the agent",
                NotificationManager.IMPORTANCE_DEFAULT));
        String text = String.join("، ", titles) + " — افتح MusabAI لتنفيذها.";
        Notification.Builder nb = new Notification.Builder(c, "agent").setContentTitle("مهمة مجدولة حان وقتها")
                .setContentText(text).setStyle(new Notification.BigTextStyle().bigText(text))
                .setSmallIcon(R.drawable.ic_stat).setAutoCancel(true).setOnlyAlertOnce(true);
        Intent open = c.getPackageManager().getLaunchIntentForPackage(c.getPackageName());
        if (open != null) {
            open.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            nb.setContentIntent(PendingIntent.getActivity(c, 8, open,
                    PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT));
        }
        nm.notify(9, nb.build());
    }
}
