package dev.newal.code.lite;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.provider.Settings;

/**
 * The phone-control accessibility service as the phone tool needs it. Android rebinds the service a moment after the
 * app's process was restarted, so a call that comes in just then waits for it; when it is off (or switched on but
 * stopped by Android after an update or a crash) the error says exactly what the user has to do, and its settings
 * page is opened for them (at most once a minute).
 */
final class A11y {
    private static long settingsOpened;

    private A11y() {
    }

    static PhoneControlService need(Context ctx) {
        PhoneControlService s = PhoneControlService.on;
        boolean enabled = enabled(ctx);
        for (int i = 0; s == null && enabled && i < 20; i++) {
            try {
                Thread.sleep(200);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
            s = PhoneControlService.on;
        }
        if (s != null) {
            return s;
        }
        openSettings(ctx);
        if (enabled) {
            throw new IllegalStateException("ACCESSIBILITY_STOPPED: the phone-control accessibility service is switched "
                    + "on but Android has stopped it (this happens after an app update or a crash). The user must "
                    + "switch it off and on again in Settings > Accessibility > Installed apps > NewAl Code: phone "
                    + "control (the page has been opened for them). Do not retry the screen, taps or typing until the "
                    + "user says it is on.");
        }
        throw new IllegalStateException("ACCESSIBILITY_OFF: the screen, taps and typing need the phone-control "
                + "accessibility service, and it is off. The user turns it on in Settings > Accessibility > Installed "
                + "apps > NewAl Code: phone control (the page has been opened for them). Do not retry the screen, taps "
                + "or typing until the user says it is on.");
    }

    static boolean enabled(Context ctx) {
        try {
            String list = Settings.Secure.getString(ctx.getContentResolver(),
                    Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
            if (list == null) {
                return false;
            }
            ComponentName me = new ComponentName(ctx, PhoneControlService.class);
            for (String item : list.split(":")) {
                if (item.equalsIgnoreCase(me.flattenToString()) || item.equalsIgnoreCase(me.flattenToShortString())) {
                    return true;
                }
            }
        } catch (RuntimeException ignored) {
            // no answer: treated as off
        }
        return false;
    }

    private static void openSettings(Context ctx) {
        long now = System.currentTimeMillis();
        if (now - settingsOpened < 60_000L) {
            return;
        }
        settingsOpened = now;
        try {
            ctx.startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
        } catch (RuntimeException ignored) {
            // Android may refuse to open it from the background: the message still says where it is
        }
    }
}
