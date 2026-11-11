package dev.newal.code.lite;

import android.Manifest;
import android.app.Notification;
import android.app.PendingIntent;
import android.app.RemoteInput;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.location.Location;
import android.location.LocationManager;
import android.media.AudioManager;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.CalendarContract;
import android.provider.ContactsContract;
import android.view.KeyEvent;

import org.json.JSONObject;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Date;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/**
 * On-device node capabilities inspired by REA analysis of OpenClaw Node (ai.openclaw.app v2026.10.70):
 * - Contacts lookup & Arabic/folded name resolution for SMS and calls (ContactsContract)
 * - Calendar agenda reading & event creation (CalendarContract)
 * - Device location snapshot with Maps URL (LocationManager)
 * - Active media playback control (AudioManager + KeyEvent)
 * - Hardware sensor & step/motion snapshot (SensorManager)
 * - Network/readiness node status & inline notification RemoteInput reply attachment
 */
public final class OpenClawNode {
    private OpenClawNode() {
    }

    static boolean hasPermission(Context ctx, String perm) {
        return ctx != null && ctx.checkSelfPermission(perm) == PackageManager.PERMISSION_GRANTED;
    }

    /** Pure helper for filtering [name, number, typeLabel] rows with Arabic/Latin folding (unit-tested). */
    public static List<String> filterContactRows(List<String[]> rows, String query, int limit) {
        List<String> out = new ArrayList<>();
        if (rows == null) return out;
        String q = query == null ? "" : query.trim();
        int max = limit > 0 ? limit : 25;
        Map<String, String> unique = new LinkedHashMap<>();
        for (String[] r : rows) {
            if (r == null || r.length < 2) continue;
            String name = r[0] == null ? "" : r[0].trim();
            String num = r[1] == null ? "" : r[1].trim();
            String type = r.length > 2 && r[2] != null ? r[2].trim() : "";
            if (name.isEmpty() && num.isEmpty()) continue;
            String hay = name + " " + num;
            if (!q.isEmpty() && !TextFold.containsFolded(hay, q)) continue;
            String key = TextFold.fold(name) + "|" + num.replaceAll("[\\s\\-()]", "");
            if (!unique.containsKey(key)) {
                unique.put(key, name + ": " + num + (type.isEmpty() ? "" : " (" + type + ")"));
                if (unique.size() >= max) break;
            }
        }
        out.addAll(unique.values());
        return out;
    }

    /** Pure helper: resolves a contact name to the best matching phone number using TextFold scoring (unit-tested). */
    public static String bestNumberForName(List<String[]> rows, String query) {
        if (query == null) return "";
        String trimmed = query.trim();
        if (trimmed.isEmpty()) return "";
        // If it already looks like a phone number (only digits, +, -, spaces, parens)
        if (trimmed.matches("^[+0-9][0-9\\s\\-().]{2,}$")) {
            return trimmed;
        }
        if (rows == null || rows.isEmpty()) return trimmed;
        String want = TextFold.fold(trimmed);
        if (want.isEmpty()) return trimmed;
        String bestNum = "";
        int bestScore = 0;
        for (String[] r : rows) {
            if (r == null || r.length < 2) continue;
            String name = TextFold.fold(r[0] == null ? "" : r[0]);
            String num = r[1] == null ? "" : r[1].trim();
            if (name.isEmpty() || num.isEmpty()) continue;
            int score = name.equals(want) ? 3 : name.startsWith(want) ? 2 : name.contains(want) ? 1 : 0;
            if (score > bestScore) {
                bestScore = score;
                bestNum = num;
            }
        }
        return bestScore > 0 ? bestNum : trimmed;
    }

    private static List<String[]> loadDeviceContacts(Context ctx) {
        List<String[]> rows = new ArrayList<>();
        if (!hasPermission(ctx, Manifest.permission.READ_CONTACTS)) {
            return rows;
        }
        String[] proj = {
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
                ContactsContract.CommonDataKinds.Phone.NUMBER,
                ContactsContract.CommonDataKinds.Phone.TYPE
        };
        try (Cursor c = ctx.getContentResolver().query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                proj, null, null,
                ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " ASC")) {
            if (c != null) {
                while (c.moveToNext()) {
                    String name = c.getString(0);
                    String num = c.getString(1);
                    int type = c.getInt(2);
                    CharSequence label = ContactsContract.CommonDataKinds.Phone.getTypeLabel(
                            ctx.getResources(), type, "");
                    rows.add(new String[] {name, num, label == null ? "" : label.toString()});
                }
            }
        } catch (Exception ignored) {
        }
        return rows;
    }

    /** Resolves a contact name to a number when READ_CONTACTS is granted; returns input unchanged otherwise. */
    static String resolveContactNumber(Context ctx, String numberOrName) {
        if (numberOrName == null) return "";
        String t = numberOrName.trim();
        if (t.isEmpty() || t.matches("^[+0-9][0-9\\s\\-().]{2,}$")) {
            return t;
        }
        if (!hasPermission(ctx, Manifest.permission.READ_CONTACTS)) {
            return t;
        }
        return bestNumberForName(loadDeviceContacts(ctx), t);
    }

    static JSONObject contacts(Context ctx, JSONObject a) throws Exception {
        if (!hasPermission(ctx, Manifest.permission.READ_CONTACTS)) {
            throw new IllegalStateException("READ_CONTACTS permission is not granted: allow Contacts access for "
                    + "MusabAI in Android Settings > Apps > MusabAI > Permissions");
        }
        String q = a.optString("query", a.optString("name", a.optString("text", ""))).trim();
        int limit = Math.max(1, Math.min(100, a.optInt("limit", 25)));
        List<String> matches = filterContactRows(loadDeviceContacts(ctx), q, limit);
        if (matches.isEmpty()) {
            return Phone.ok(q.isEmpty() ? "(no contacts on this phone)" : "(no contacts matching \"" + q + "\")");
        }
        return Phone.ok(String.join("\n", matches));
    }

    static JSONObject contactAdd(Context ctx, JSONObject a) throws Exception {
        String name = a.optString("name", a.optString("title", "")).trim();
        String phone = a.optString("number", a.optString("phone", a.optString("text", ""))).trim();
        String email = a.optString("email", "").trim();
        String company = a.optString("company", "").trim();
        if (name.isEmpty() && phone.isEmpty()) {
            throw new IllegalArgumentException("contact_add needs name or number");
        }
        Intent i = new Intent(ContactsContract.Intents.Insert.ACTION);
        i.setType(ContactsContract.RawContacts.CONTENT_TYPE);
        if (!name.isEmpty()) i.putExtra(ContactsContract.Intents.Insert.NAME, name);
        if (!phone.isEmpty()) i.putExtra(ContactsContract.Intents.Insert.PHONE, phone);
        if (!email.isEmpty()) i.putExtra(ContactsContract.Intents.Insert.EMAIL, email);
        if (!company.isEmpty()) i.putExtra(ContactsContract.Intents.Insert.COMPANY, company);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        ctx.startActivity(i);
        Thread.sleep(700);
        return Phone.ok("opened contact editor for " + (name.isEmpty() ? phone : name + " (" + phone + ")"));
    }

    static JSONObject calendar(Context ctx, JSONObject a) throws Exception {
        if (!hasPermission(ctx, Manifest.permission.READ_CALENDAR)) {
            throw new IllegalStateException("READ_CALENDAR permission is not granted: allow Calendar access for "
                    + "MusabAI in Android Settings > Apps > MusabAI > Permissions");
        }
        String q = a.optString("query", a.optString("text", a.optString("name", ""))).trim();
        int days = Math.max(1, Math.min(90, a.optInt("days", 7)));
        int limit = Math.max(1, Math.min(50, a.optInt("limit", 20)));
        long now = System.currentTimeMillis();
        long until = now + days * 86_400_000L;
        String[] proj = {
                CalendarContract.Events.TITLE,
                CalendarContract.Events.DTSTART,
                CalendarContract.Events.DTEND,
                CalendarContract.Events.EVENT_LOCATION,
                CalendarContract.Events.DESCRIPTION
        };
        String sel = CalendarContract.Events.DTSTART + " >= ? AND " + CalendarContract.Events.DTSTART + " <= ? AND "
                + CalendarContract.Events.DELETED + " = 0";
        String[] args = {String.valueOf(now - 3_600_000L), String.valueOf(until)};
        List<String> events = new ArrayList<>();
        SimpleDateFormat fmt = new SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.ROOT);
        try (Cursor c = ctx.getContentResolver().query(
                CalendarContract.Events.CONTENT_URI, proj, sel, args,
                CalendarContract.Events.DTSTART + " ASC")) {
            if (c != null) {
                while (c.moveToNext() && events.size() < limit) {
                    String title = c.getString(0) == null ? "(untitled)" : c.getString(0).trim();
                    long start = c.getLong(1);
                    long end = c.getLong(2);
                    String loc = c.getString(3) == null ? "" : c.getString(3).trim();
                    String desc = c.getString(4) == null ? "" : c.getString(4).trim();
                    String hay = title + " " + loc + " " + desc;
                    if (!q.isEmpty() && !TextFold.containsFolded(hay, q)) continue;
                    StringBuilder line = new StringBuilder();
                    line.append(fmt.format(new Date(start)));
                    if (end > start) {
                        line.append(" – ").append(fmt.format(new Date(end)));
                    }
                    line.append(" · ").append(title);
                    if (!loc.isEmpty()) line.append(" @ ").append(loc);
                    events.add(line.toString());
                }
            }
        }
        if (events.isEmpty()) {
            return Phone.ok("(no calendar events in the next " + days + " days"
                    + (q.isEmpty() ? ")" : " matching \"" + q + "\")"));
        }
        return Phone.ok(String.join("\n", events));
    }

    static JSONObject calendarAdd(Context ctx, JSONObject a) throws Exception {
        String title = a.optString("title", a.optString("name", a.optString("text", ""))).trim();
        if (title.isEmpty()) {
            throw new IllegalArgumentException("calendar_add needs title (the event name)");
        }
        String desc = a.optString("description", "").trim();
        String loc = a.optString("location", "").trim();
        long startMs = a.optLong("begin", a.optLong("start", 0L));
        if (startMs <= 0L) {
            int inMinutes = a.optInt("in_minutes", 60);
            startMs = System.currentTimeMillis() + inMinutes * 60_000L;
        }
        long durationMin = Math.max(5, a.optInt("duration_minutes", a.optInt("minutes", 60)));
        long endMs = a.optLong("end", startMs + durationMin * 60_000L);

        Intent i = new Intent(Intent.ACTION_INSERT)
                .setData(CalendarContract.Events.CONTENT_URI)
                .putExtra(CalendarContract.Events.TITLE, title)
                .putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, startMs)
                .putExtra(CalendarContract.EXTRA_EVENT_END_TIME, endMs);
        if (!desc.isEmpty()) i.putExtra(CalendarContract.Events.DESCRIPTION, desc);
        if (!loc.isEmpty()) i.putExtra(CalendarContract.Events.EVENT_LOCATION, loc);
        i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        ctx.startActivity(i);
        Thread.sleep(700);
        return Phone.ok("opened calendar event editor for \"" + title + "\"");
    }

    static JSONObject location(Context ctx) throws Exception {
        boolean fine = hasPermission(ctx, Manifest.permission.ACCESS_FINE_LOCATION);
        boolean coarse = hasPermission(ctx, Manifest.permission.ACCESS_COARSE_LOCATION);
        if (!fine && !coarse) {
            throw new IllegalStateException("location permission is not granted: allow Location access for "
                    + "MusabAI in Android Settings > Apps > MusabAI > Permissions");
        }
        LocationManager lm = ctx.getSystemService(LocationManager.class);
        if (lm == null) {
            throw new IllegalStateException("LocationManager is unavailable on this device");
        }
        Location best = null;
        for (String provider : lm.getProviders(true)) {
            try {
                Location l = lm.getLastKnownLocation(provider);
                if (l == null) continue;
                if (best == null || l.getTime() > best.getTime()
                        || (Math.abs(l.getTime() - best.getTime()) < 60_000L && l.getAccuracy() < best.getAccuracy())) {
                    best = l;
                }
            } catch (SecurityException ignored) {
            }
        }
        if (best == null) {
            return Phone.ok("location: no recent fix available (enable GPS/Location services and try again)");
        }
        long ageSec = Math.max(0L, (System.currentTimeMillis() - best.getTime()) / 1000L);
        String mapsUrl = String.format(Locale.ROOT, "https://maps.google.com/?q=%.6f,%.6f",
                best.getLatitude(), best.getLongitude());
        String summary = String.format(Locale.ROOT,
                "location: %.6f, %.6f (±%.0f m, provider=%s, age=%ds) %s",
                best.getLatitude(), best.getLongitude(), best.getAccuracy(),
                best.getProvider(), ageSec, mapsUrl);
        JSONObject o = Phone.ok(summary);
        o.put("latitude", best.getLatitude());
        o.put("longitude", best.getLongitude());
        o.put("accuracy_m", best.getAccuracy());
        o.put("provider", best.getProvider());
        o.put("age_seconds", ageSec);
        o.put("maps_url", mapsUrl);
        return o;
    }

    /** Pure helper mapping media command names to Android KeyEvent codes (unit-tested). */
    public static int mediaKeyCode(String cmd) {
        String c = cmd == null ? "" : cmd.trim().toLowerCase(Locale.ROOT);
        switch (c) {
            case "play":
                return KeyEvent.KEYCODE_MEDIA_PLAY;
            case "pause":
                return KeyEvent.KEYCODE_MEDIA_PAUSE;
            case "next":
            case "skip":
            case "تالي":
            case "التالي":
                return KeyEvent.KEYCODE_MEDIA_NEXT;
            case "previous":
            case "prev":
            case "سابق":
            case "السابق":
                return KeyEvent.KEYCODE_MEDIA_PREVIOUS;
            case "stop":
            case "ايقاف":
            case "إيقاف":
                return KeyEvent.KEYCODE_MEDIA_STOP;
            case "mute":
            case "كتم":
                return KeyEvent.KEYCODE_VOLUME_MUTE;
            case "play_pause":
            case "toggle":
            case "تشغيل":
            case "ايقاف مؤقت":
            default:
                return KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE;
        }
    }

    static JSONObject media(Context ctx, JSONObject a) throws Exception {
        AudioManager am = ctx.getSystemService(AudioManager.class);
        if (am == null) {
            throw new IllegalStateException("AudioManager is unavailable");
        }
        String cmd = a.optString("command", a.optString("name", a.optString("text", "play_pause"))).trim();
        int keyCode = mediaKeyCode(cmd);
        am.dispatchMediaKeyEvent(new KeyEvent(KeyEvent.ACTION_DOWN, keyCode));
        am.dispatchMediaKeyEvent(new KeyEvent(KeyEvent.ACTION_UP, keyCode));
        Thread.sleep(250);
        int cur = am.getStreamVolume(AudioManager.STREAM_MUSIC);
        int max = Math.max(1, am.getStreamMaxVolume(AudioManager.STREAM_MUSIC));
        int pct = Math.round(100f * cur / max);
        return Phone.ok("media " + (cmd.isEmpty() ? "play_pause" : cmd)
                + " sent (music_active=" + am.isMusicActive() + ", volume=" + pct + "%)");
    }

    static JSONObject sensors(Context ctx) throws Exception {
        SensorManager sm = ctx.getSystemService(SensorManager.class);
        if (sm == null) {
            return Phone.ok("sensors: unavailable");
        }
        int[] types = {
                Sensor.TYPE_STEP_COUNTER,
                Sensor.TYPE_ACCELEROMETER,
                Sensor.TYPE_LIGHT,
                Sensor.TYPE_PRESSURE,
                Sensor.TYPE_PROXIMITY,
                Sensor.TYPE_AMBIENT_TEMPERATURE
        };
        String[] labels = {"steps", "accelerometer_m_s2", "light_lux", "pressure_hpa", "proximity_cm", "ambient_c"};
        List<String> parts = new ArrayList<>();
        for (int i = 0; i < types.length; i++) {
            Sensor s = sm.getDefaultSensor(types[i]);
            if (s == null) continue;
            float[] vals = readSensorOnce(sm, s, 250);
            if (vals != null && vals.length > 0) {
                if (types[i] == Sensor.TYPE_ACCELEROMETER && vals.length >= 3) {
                    parts.add(String.format(Locale.ROOT, "%s=(%.2f, %.2f, %.2f)", labels[i], vals[0], vals[1], vals[2]));
                } else {
                    parts.add(String.format(Locale.ROOT, "%s=%.1f", labels[i], vals[0]));
                }
            } else {
                parts.add(labels[i] + "=present (" + s.getName() + ")");
            }
        }
        parts.add(Phone.battery(ctx));
        return Phone.ok("sensors: " + String.join("; ", parts));
    }

    private static float[] readSensorOnce(SensorManager sm, Sensor sensor, long timeoutMs) {
        final float[][] result = {null};
        CountDownLatch latch = new CountDownLatch(1);
        SensorEventListener listener = new SensorEventListener() {
            @Override
            public void onSensorChanged(SensorEvent event) {
                if (event != null && event.values != null) {
                    result[0] = event.values.clone();
                }
                latch.countDown();
            }

            @Override
            public void onAccuracyChanged(Sensor s, int accuracy) {
            }
        };
        try {
            Handler h = new Handler(Looper.getMainLooper());
            if (sm.registerListener(listener, sensor, SensorManager.SENSOR_DELAY_NORMAL, h)) {
                latch.await(timeoutMs, TimeUnit.MILLISECONDS);
            }
        } catch (Exception ignored) {
        } finally {
            try {
                sm.unregisterListener(listener);
            } catch (Exception ignored) {
            }
        }
        return result[0];
    }

    static String networkSummary(Context ctx) {
        try {
            ConnectivityManager cm = ctx.getSystemService(ConnectivityManager.class);
            if (cm == null) return "network: unknown";
            Network net = cm.getActiveNetwork();
            if (net == null) return "network: offline";
            NetworkCapabilities caps = cm.getNetworkCapabilities(net);
            if (caps == null) return "network: offline";
            List<String> transports = new ArrayList<>();
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) transports.add("wifi");
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) transports.add("cellular");
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) transports.add("ethernet");
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)) transports.add("vpn");
            boolean validated = caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED);
            return "network: " + (transports.isEmpty() ? "connected" : String.join("+", transports))
                    + (validated ? " (online)" : " (unvalidated)");
        } catch (Exception e) {
            return "network: unknown";
        }
    }

    static String nodeReadiness(Context ctx) {
        boolean a11y = PhoneControlService.on != null;
        boolean notif = LocalNotificationAgentService.on != null;
        boolean contacts = hasPermission(ctx, Manifest.permission.READ_CONTACTS);
        boolean calendar = hasPermission(ctx, Manifest.permission.READ_CALENDAR);
        boolean location = hasPermission(ctx, Manifest.permission.ACCESS_FINE_LOCATION)
                || hasPermission(ctx, Manifest.permission.ACCESS_COARSE_LOCATION);
        return String.format(Locale.ROOT,
                "%s; node(a11y=%s, notifications=%s, contacts=%s, calendar=%s, location=%s)",
                networkSummary(ctx),
                a11y ? "on" : "off",
                notif ? "on" : "off",
                contacts ? "on" : "off",
                calendar ? "on" : "off",
                location ? "on" : "off");
    }

    static void attachInlineReply(Context ctx, Notification.Builder nb, int notificationId) {
        try {
            RemoteInput remoteInput = new RemoteInput.Builder(NotificationReplyReceiver.KEY_TEXT_REPLY)
                    .setLabel("رد على MusabAI…")
                    .build();
            Intent replyIntent = new Intent(ctx, NotificationReplyReceiver.class)
                    .putExtra(NotificationReplyReceiver.EXTRA_NOTIFICATION_ID, notificationId);
            int flags = PendingIntent.FLAG_UPDATE_CURRENT
                    | (Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0);
            PendingIntent replyPending = PendingIntent.getBroadcast(ctx, notificationId, replyIntent, flags);
            Notification.Action replyAction = new Notification.Action.Builder(
                    R.drawable.ic_stat, "رد / Reply", replyPending)
                    .addRemoteInput(remoteInput)
                    .build();
            nb.addAction(replyAction);
        } catch (Exception ignored) {
        }
    }
}
