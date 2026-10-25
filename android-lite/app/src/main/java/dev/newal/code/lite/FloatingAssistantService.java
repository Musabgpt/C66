package dev.newal.code.lite;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.drawable.GradientDrawable;
import android.os.*;
import android.provider.Settings;
import android.speech.*;
import android.view.*;
import android.widget.*;

import org.json.*;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Small, user-switchable overlay. Uses the existing authenticated agent API,
 * not an independent AI model or a separate conversation backend.
 * The microphone is active only after an explicit user toggle and has a persistent notification.
 */
public final class FloatingAssistantService extends Service implements RecognitionListener {
    static final String ACTION_START = "musabai.overlay.START";
    static final String ACTION_STOP = "musabai.overlay.STOP";
    static final String ACTION_LISTEN = "musabai.overlay.LISTEN";
    private static final String CHANNEL = "musabai-overlay";
    private static final int NOTIFICATION = 607;
    private static final int MAX_COMMAND = 4000;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private WindowManager wm;
    private View bubble;
    private LinearLayout panel;
    private WindowManager.LayoutParams bubbleParams;
    private TextView status;
    private TextView micButton;
    private TextView bubbleText;
    private EditText input;
    private SpeechRecognizer speech;
    private boolean listening;
    private boolean busy;
    private boolean destroyed;
    private int speechFailures;
    private String sessionId = "";
    private String lastSpeech = "";
    private long lastSpeechAt;
    private final Runnable restartSpeech = this::startRecognition;
    private android.speech.tts.TextToSpeech tts;
    private boolean ttsReady;

    @Override public void onCreate() {
        super.onCreate();
        wm = (WindowManager) getSystemService(WINDOW_SERVICE);
        sessionId = getSharedPreferences("musabai-floating", MODE_PRIVATE).getString("session", "");
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL, "MusabAI voice assistant",
                NotificationManager.IMPORTANCE_LOW));
        try {
            tts = new android.speech.tts.TextToSpeech(this, state -> {
                ttsReady = state == android.speech.tts.TextToSpeech.SUCCESS;
                if (ttsReady && tts != null)
                    tts.setLanguage(new java.util.Locale("ar"));
            });
        } catch (RuntimeException ignored) { tts = null; }
    }

    @Override public int onStartCommand(Intent intent, int flags, int id) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopSelf(); return START_NOT_STICKY;
        }
        foreground();
        if (Build.VERSION.SDK_INT >= 23 && !Settings.canDrawOverlays(this)) {
            postNotice("اسمح لـ MusabAI بالظهور فوق التطبيقات من الإعدادات.");
            return START_STICKY;
        }
        if (bubble == null) showBubble();
        if (intent != null && ACTION_LISTEN.equals(intent.getAction())) setListening(true);
        return START_STICKY;
    }

    private void foreground() {
        Intent open = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pending = PendingIntent.getActivity(this, 607, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent stop = new Intent(this, FloatingAssistantService.class).setAction(ACTION_STOP);
        PendingIntent stopping = PendingIntent.getService(this, 608, stop,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_stat).setContentTitle("MusabAI")
                .setContentText("المساعد العائم جاهز — يمكنك إيقافه من هنا")
                .setOngoing(true).setContentIntent(pending)
                .addAction(new Notification.Action.Builder(null, "إيقاف المساعد", stopping).build())
                .build();
        int type = Build.VERSION.SDK_INT >= 34 ? ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE : 0;
        if (Build.VERSION.SDK_INT >= 34 && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED)
            type |= ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE;
        try {
            if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIFICATION, n, type);
            else startForeground(NOTIFICATION, n);
        } catch (RuntimeException failure) {
            // An audio foreground service may be rejected if Android has revoked
            // microphone access in the background; stay text-only in that case.
            if (Build.VERSION.SDK_INT >= 34)
                startForeground(NOTIFICATION, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
            else startForeground(NOTIFICATION, n);
        }
    }

    private GradientDrawable bg(int color, float round) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color); d.setCornerRadius(dp(round)); return d;
    }
    private int dp(float value) { return (int) (getResources().getDisplayMetrics().density * value + .5f); }
    private TextView label(String text, int size, int color) {
        TextView t = new TextView(this);
        t.setText(text); t.setTextSize(size); t.setTextColor(color);
        t.setGravity(Gravity.CENTER_VERTICAL); t.setPadding(dp(10), dp(9), dp(10), dp(9));
        return t;
    }

    private void showBubble() {
        bubbleText = label("✦", 29, Color.WHITE);
        bubbleText.setGravity(Gravity.CENTER);
        bubbleText.setBackground(bg(0xff087e70, 80));
        bubbleText.setElevation(dp(9));
        bubble = bubbleText;
        bubbleParams = new WindowManager.LayoutParams(dp(60), dp(60),
                Build.VERSION.SDK_INT >= 26 ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY :
                    WindowManager.LayoutParams.TYPE_PHONE,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                    | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                PixelFormat.TRANSLUCENT);
        bubbleParams.gravity = Gravity.TOP | Gravity.LEFT;
        bubbleParams.x = getResources().getDisplayMetrics().widthPixels - dp(74);
        bubbleParams.y = dp(230);
        try { wm.addView(bubble, bubbleParams); }
        catch (RuntimeException e) { postNotice("تعذّر إظهار فقاعة MusabAI: " + e.getClass().getSimpleName()); return; }
        bubble.setOnTouchListener(new View.OnTouchListener() {
            int x0, y0; float tx, ty; boolean dragged;
            @Override public boolean onTouch(View view, android.view.MotionEvent event) {
                if (event.getActionMasked() == MotionEvent.ACTION_DOWN) {
                    x0 = bubbleParams.x; y0 = bubbleParams.y;
                    tx = event.getRawX(); ty = event.getRawY(); dragged = false; return true;
                }
                if (event.getActionMasked() == MotionEvent.ACTION_MOVE) {
                    int dx = Math.round(event.getRawX() - tx), dy = Math.round(event.getRawY() - ty);
                    if (Math.abs(dx) + Math.abs(dy) > dp(10)) dragged = true;
                    bubbleParams.x = Math.max(0, Math.min(getResources().getDisplayMetrics().widthPixels-dp(60), x0+dx));
                    bubbleParams.y = Math.max(dp(30), y0+dy);
                    try { wm.updateViewLayout(bubble, bubbleParams); } catch (RuntimeException ignored) {}
                    return true;
                }
                if (event.getActionMasked() == MotionEvent.ACTION_UP) {
                    if (!dragged) togglePanel();
                    return true;
                }
                return true;
            }
        });
    }

    private void togglePanel() {
        if (panel != null) {
            wm.removeView(panel); panel = null; return;
        }
        panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setPadding(dp(10), dp(12), dp(10), dp(10));
        panel.setBackground(bg(0xff14202c, 22));
        panel.setElevation(dp(18));

        TextView title = label("✦ MusabAI     •     مساعدك العائم", 17, Color.WHITE);
        panel.addView(title);
        status = label("جاهز. اكتب الأمر، أو فعّل الاستماع مرة واحدة.", 13, 0xffb6d5e1);
        status.setMaxLines(5); panel.addView(status);
        input = new EditText(this);
        input.setTextColor(Color.WHITE); input.setHintTextColor(0xff9daeb5);
        input.setTextSize(15); input.setSingleLine(false); input.setMaxLines(4);
        input.setHint("شو بدك أعمل؟");
        input.setBackground(bg(0xff2a3b48, 12)); input.setPadding(dp(12), dp(7), dp(12), dp(7));
        LinearLayout.LayoutParams editParams = new LinearLayout.LayoutParams(-1, dp(76));
        editParams.setMargins(0, dp(5), 0, dp(8)); panel.addView(input, editParams);

        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);
        TextView send = label("إرسال ↗", 15, Color.WHITE);
        send.setGravity(Gravity.CENTER); send.setBackground(bg(0xff087e70, 12));
        actions.addView(send, new LinearLayout.LayoutParams(0, dp(45), 1));
        send.setOnClickListener(v -> {
            String text = input.getText().toString().trim();
            if (!text.isEmpty()) { input.setText(""); sendCommand(text); }
        });
        micButton = label("", 14, Color.WHITE);
        micButton.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams mp = new LinearLayout.LayoutParams(0, dp(45), 1);
        mp.setMargins(dp(8), 0, 0, 0); actions.addView(micButton, mp);
        micButton.setBackground(bg(0xff435262, 12));
        micButton.setOnClickListener(v -> setListening(!listening));
        updateMic();
        panel.addView(actions);

        TextView close = label("تصغير الفقاعة     •     إيقاف: من الإشعار", 12, 0xffa1b3be);
        close.setGravity(Gravity.CENTER);
        close.setOnClickListener(v -> togglePanel()); panel.addView(close);
        int width = Math.min(getResources().getDisplayMetrics().widthPixels - dp(20), dp(350));
        WindowManager.LayoutParams params = new WindowManager.LayoutParams(width, -2,
                Build.VERSION.SDK_INT >= 26 ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY :
                    WindowManager.LayoutParams.TYPE_PHONE,
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                    | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        params.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL;
        params.y = Math.max(dp(65), Math.min(getResources().getDisplayMetrics().heightPixels - dp(390), bubbleParams.y + dp(65)));
        params.softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE;
        try { wm.addView(panel, params); }
        catch (RuntimeException e) { panel = null; postNotice("تعذّر فتح نافذة المحادثة"); }
    }

    private void postNotice(String text) {
        ui.post(() -> { if (status != null) status.setText(text); });
    }

    private void updateMic() {
        if (micButton != null) micButton.setText(listening ? "● إيقاف الاستماع" : "🎙 استماع مستمر");
        if (bubbleText != null) bubbleText.setText(listening ? "◉" : "✦");
    }

    private void setListening(boolean enable) {
        if (!enable) {
            listening = false; ui.removeCallbacks(restartSpeech);
            if (speech != null) { try { speech.cancel(); speech.destroy(); } catch (RuntimeException ignored) {} speech = null; }
            updateMic(); postNotice("الاستماع متوقف. يمكنك كتابة الأوامر."); return;
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            postNotice("يلزم السماح بالميكروفون من التهيئة داخل التطبيق."); return;
        }
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            postNotice("خدمة تحويل الصوت إلى نص غير متوفرة على الجهاز."); return;
        }
        try {
            foreground();   // microphone FGS type is required on Android 14+
            listening = true; speechFailures = 0; updateMic();
            startRecognition();
        } catch (RuntimeException problem) {
            listening = false; updateMic();
            postNotice("أندرويد منع تشغيل الميكروفون بالخلفية؛ فعّله من داخل التطبيق.");
        }
    }

    private void startRecognition() {
        if (!listening || destroyed) return;
        try {
            if (speech != null) { speech.destroy(); speech = null; }
            speech = SpeechRecognizer.createSpeechRecognizer(this);
            speech.setRecognitionListener(this);
            Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ar-SY");
            intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false);
            intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
            speech.startListening(intent);
            postNotice("🎙 عم اسمعك — احكي مباشرة بدون ضغط.");
        } catch (RuntimeException e) {
            speechFailures++;
            if (speechFailures >= 10) { setListening(false); postNotice("توقفت خدمة التعرف الصوتي؛ جرّب تفعيلها مجدداً."); }
            else retrySpeech();
        }
    }

    private void retrySpeech() {
        if (!listening || destroyed) return;
        ui.removeCallbacks(restartSpeech);
        ui.postDelayed(restartSpeech, Math.min(8000, 800L * (1L << Math.min(3, speechFailures))));
    }
    @Override public void onReadyForSpeech(Bundle p) {}
    @Override public void onBeginningOfSpeech() {}
    @Override public void onRmsChanged(float rms) {}
    @Override public void onBufferReceived(byte[] b) {}
    @Override public void onEndOfSpeech() {}
    @Override public void onPartialResults(Bundle b) {}
    @Override public void onEvent(int eventType, Bundle params) {}
    @Override public void onError(int error) {
        if (!listening) return;
        // No match and speech timeout are normal silence, not permanent errors.
        if (error != SpeechRecognizer.ERROR_NO_MATCH && error != SpeechRecognizer.ERROR_SPEECH_TIMEOUT)
            speechFailures++;
        if (speechFailures >= 10 || error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS) {
            setListening(false); postNotice("تعذر استمرار الاستماع. افتح التطبيق وافحص صلاحية الصوت."); return;
        }
        retrySpeech();
    }
    @Override public void onResults(Bundle data) {
        if (!listening) return;
        speechFailures = 0;
        ArrayList<String> words = data.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        String transcript = words == null || words.isEmpty() ? "" : words.get(0).trim();
        if (!transcript.isEmpty()) {
            long now = SystemClock.elapsedRealtime();
            if (!transcript.equals(lastSpeech) || now - lastSpeechAt > 5000) {
                lastSpeech = transcript; lastSpeechAt = now;
                sendCommand(transcript);
            }
        }
        retrySpeech();
    }

    private JSONObject request(String method, String url, JSONObject payload) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL("http://127.0.0.1:" + Setup.PORT + url).openConnection();
        c.setConnectTimeout(4000); c.setReadTimeout(12000); c.setRequestMethod(method);
        c.setRequestProperty("X-NewAl-Key", new Setup(this).key());
        c.setRequestProperty("Accept", "application/json");
        if (payload != null) {
            c.setDoOutput(true); c.setRequestProperty("Content-Type", "application/json");
            byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
            try (OutputStream stream = c.getOutputStream()) { stream.write(body); }
        }
        try {
            int code = c.getResponseCode();
            InputStream in = code < 400 ? c.getInputStream() : c.getErrorStream();
            if (in == null) throw new IOException("Local engine: HTTP " + code);
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            byte[] chunk = new byte[2048]; int n;
            while ((n = in.read(chunk)) != -1 && bytes.size() < 500000) bytes.write(chunk, 0, n);
            if (code >= 400) throw new IOException("Local engine: HTTP " + code);
            return new JSONObject(bytes.toString("UTF-8"));
        } finally { c.disconnect(); }
    }

    private void sendCommand(String message) {
        if (message.length() > MAX_COMMAND) { postNotice("الطلب طويل؛ الحد 4000 حرف."); return; }
        if (busy) { postNotice("MusabAI عم ينفذ مهمة سابقة؛ جرّب بعد ما يخلص."); return; }
        busy = true;
        postNotice("⏳ عم نفّذ: " + message);
        worker.execute(() -> {
            try {
                // A user-chosen model remains the default. NVIDIA is available in
                // the ordinary model picker; the bubble does not replace it.
                if (sessionId.isEmpty()) {
                    JSONObject state = request("GET", "/api/state", null);
                    String model = state.optJSONObject("settings") == null ? "auto" :
                            state.optJSONObject("settings").optString("model", "auto");
                    JSONObject spec = new JSONObject()
                            .put("root", new Setup(this).home.getAbsolutePath())
                            .put("model", model).put("mode", "full-auto").put("warm", false);
                    sessionId = request("POST", "/api/sessions", spec).getString("id");
                    getSharedPreferences("musabai-floating", MODE_PRIVATE)
                            .edit().putString("session", sessionId).apply();
                }
                JSONObject r = request("POST", "/api/sessions/" + sessionId + "/send",
                        new JSONObject().put("text", message).put("lang", "ar"));
                if (!r.optBoolean("started", false)) {
                    String reply = r.optString("reply", "تم إرسال الطلب");
                    busy = false; postNotice(reply); return;
                }
                for (int t = 0; t < 900 && !destroyed; t++) {
                    Thread.sleep(1800);
                    JSONObject result = request("GET", "/api/sessions/" + sessionId, null);
                    if (result.optBoolean("busy", false)) continue;
                    JSONArray events = result.optJSONArray("events");
                    String answer = "";
                    if (events != null) for (int i = events.length() - 1; i >= 0; i--) {
                        JSONObject ev = events.optJSONObject(i);
                        if (ev == null) continue;
                        String eventType = ev.optString("type");
                        if ("turn_end".equals(eventType) || "reply".equals(eventType)) {
                            answer = ev.optString("answer", ev.optString("text", ""));
                            break;
                        }
                    }
                    final String text = answer.isEmpty() ? "انتهت المهمة. افتح MusabAI للتفاصيل." : answer;
                    busy = false; postNotice(text.length() > 300 ? text.substring(0, 300) : text);
                    break;
                }
                if (busy) { busy = false; postNotice("ما قدرت أتأكد من انتهاء المهمة؛ افحص محادثة MusabAI."); }
            } catch (Exception ex) {
                busy = false;
                sessionId = "";
                getSharedPreferences("musabai-floating", MODE_PRIVATE).edit().remove("session").apply();
                postNotice("تعذّر الاتصال بالمحرّك: " + ex.getClass().getSimpleName()
                        + " — افتح MusabAI وتأكد أنه شغال.");
            }
        });
    }

    @Override public IBinder onBind(Intent intent) { return null; }

    @Override public void onDestroy() {
        destroyed = true; listening = false;
        ui.removeCallbacksAndMessages(null);
        if (speech != null) { try { speech.cancel(); speech.destroy(); } catch (RuntimeException ignored) {} }
        if (panel != null) { try { wm.removeView(panel); } catch (RuntimeException ignored) {} panel = null; }
        if (bubble != null) { try { wm.removeView(bubble); } catch (RuntimeException ignored) {} bubble = null; }
        if (tts != null) tts.shutdown();
        worker.shutdownNow();
        super.onDestroy();
    }
}
