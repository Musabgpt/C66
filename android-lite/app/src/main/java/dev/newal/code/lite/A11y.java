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

    /** The answer of an action that changes the screen, with the screen as it is now in "screen": the agent sees what
     *  its action did without another round trip. After opening an app or a link, waits (up to ~3 s) until something
     *  other than this app is in front. Without the service, the answer as it was. */
    static org.json.JSONObject withScreen(org.json.JSONObject answer, boolean switching) {
        PhoneControlService s = PhoneControlService.on;
        if (s == null || answer == null) {
            return answer;
        }
        try {
            String own = "(" + s.getPackageName() + ")";
            for (int i = 0; i < 7; i++) {
                String text = s.screen().optString("text");
                boolean away = !text.contains(own) && !text.contains("(com.android.systemui)");
                if (!switching || (away && text.contains("[1]")) || i == 6) {
                    answer.put("screen", text);
                    break;
                }
                Thread.sleep(450);
            }
        } catch (Exception ignored) {
            // the action is done; only the look at the screen is missing
        }
        return answer;
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
