package dev.newal.code.lite;

import android.app.Service;
import android.content.Intent;
import android.os.IBinder;

/** Explicit non-exported target; only our PendingIntent lets Termux deliver a result. */
public final class TermuxResultService extends Service {
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        new Thread(() -> {
            try { if (intent != null) TermuxJobs.receive(this, intent); }
            catch (Exception ignored) { /* status remains pending; never claim success or replay */ }
            finally { stopSelf(startId); }
        }, "termux-result").start();
        return START_NOT_STICKY;
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}
