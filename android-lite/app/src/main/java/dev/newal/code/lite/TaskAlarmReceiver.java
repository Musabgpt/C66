package dev.newal.code.lite;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

/**
 * A scheduled task's alarm (TaskAlarms): starts the engine's service, whose scheduler runs what is due. After the phone
 * restarts or the app is updated, Android has dropped the alarm: it is set again from the tasks' database.
 */
public class TaskAlarmReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        long retry = TaskAlarms.RETRY_MS;
        if (intent != null && TaskAlarms.ACTION.equals(intent.getAction())) {
            try {
                context.startForegroundService(new Intent(context, AgentService.class));
            } catch (RuntimeException e) {          // ForegroundServiceStartNotAllowedException
                Log.w("NewAlCode", "cannot start the engine for a scheduled task", e);
                TaskAlarms.notifyDue(context);
                retry = TaskAlarms.REFUSED_RETRY_MS;
            }
        }
        TaskAlarms.schedule(context, retry);
    }
}
