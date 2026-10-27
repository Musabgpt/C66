package dev.newal.code.lite;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.content.pm.ServiceInfo;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.*;
import android.provider.Settings;
import android.speech.*;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.text.TextUtils;
import android.view.*;
import android.widget.*;

import org.json.*;

import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Interactive Arabic voice & task floating assistant overlay for MusabAI Preview.
 * Supports natural wake/attention words ("ام", "يا مصعب", "اسمع", "مرحبا"), instant native phone actions,
 * multi-step screen/agent task execution with live step-by-step progress in a non-focusable status pill,
 * voice interruption ("وقف"), and two-way Arabic TTS conversation.
 */
public final class FloatingAssistantService extends Service implements RecognitionListener {
    static final String ACTION_START = "musabai.overlay.START";
    static final String ACTION_STOP = "musabai.overlay.STOP";
    static final String ACTION_LISTEN = "musabai.overlay.LISTEN";
    static final String ACTION_TOGGLE_PANEL = "musabai.overlay.TOGGLE_PANEL";
    static final String ACTION_COMMAND = "musabai.overlay.COMMAND";
    private static final String CHANNEL = "musabai-overlay";
    private static final int NOTIFICATION = 607;
    private static final int MAX_COMMAND = 4000;
    private static final String PREFS = "musabai-floating";

    private static final class ChatEntry {
        final String role; // "user", "assistant", "step"
        final String text;
        ChatEntry(String role, String text) {
            this.role = role;
            this.text = text;
        }
    }

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService worker = Executors.newCachedThreadPool();
    private final List<ChatEntry> history = new ArrayList<>();

    private WindowManager wm;
    private View bubble;
    private TextView bubbleText;
    private TextView statusPill;
    private LinearLayout panel;
    private WindowManager.LayoutParams bubbleParams;
    private WindowManager.LayoutParams pillParams;
    private WindowManager.LayoutParams panelParams;

    private TextView status;
    private TextView stopButton;
    private TextView micButton;
    private TextView voiceToggleBtn;
    private ScrollView chatScroll;
    private LinearLayout chatContainer;
    private EditText input;

    private SpeechRecognizer speech;
    private boolean listening;
    private volatile boolean busy;
    private volatile boolean cancelRequested;
    private volatile boolean speaking;
    private boolean destroyed;
    private boolean voiceReplyEnabled = true;
    private int speechFailures;
    private int localeIndex;
    private static final String[] ARABIC_LOCALES = new String[] {"ar-SA", "ar-SY", "ar"};

    private String sessionId = "";
    private String lastSpeech = "";
    private String lastAnswer = "";
    private volatile String currentStepSummary = "جاهز لتنفيذ المهام";
    private volatile String pendingCommand = "";
    private String lastStatus = "جاهز — احكي \"أم\" أو اطلب أي مهمة مباشرة.";
    private long lastSpeechAt;
    private volatile long lastTtsEndedAt;
    private BroadcastReceiver controlReceiver;

    private final Runnable restartSpeech = this::startRecognition;
    private final Runnable hidePillRunnable = () -> {
        if (statusPill != null && !busy && !speaking) {
            statusPill.setVisibility(View.GONE);
        }
    };
    private final Runnable ttsSafetyReset = this::resumeListeningAfterSpeech;

    private TextToSpeech tts;
    private boolean ttsReady;

    @Override
    public void onCreate() {
        super.onCreate();
        wm = (WindowManager) getSystemService(WINDOW_SERVICE);
        SharedPreferences prefs = getSharedPreferences(PREFS, MODE_PRIVATE);
        sessionId = prefs.getString("session", "");
        voiceReplyEnabled = prefs.getBoolean("voice_reply", true);

        history.add(new ChatEntry("assistant",
                "أهلاً بك! أنا مساعدك الصوتي العائم. قل لي «أم» أو اطلب أي مهمة على الهاتف أو البرمجة وسأنفذها وأحكي معك خطوة بخطوة."));

        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm != null) {
            nm.createNotificationChannel(new NotificationChannel(CHANNEL, "MusabAI voice assistant",
                    NotificationManager.IMPORTANCE_LOW));
        }
        initTts();
        registerControlReceiver();
        worker.execute(this::restoreUserBackupIfPresent);
    }

    private void registerControlReceiver() {
        controlReceiver = new BroadcastReceiver() {
            @Override
            public void onReceive(Context context, Intent intent) {
                if (intent == null) return;
                String act = intent.getStringExtra("op");
                String cmd = intent.getStringExtra("cmd");
                if ("toggle_panel".equals(act)) {
                    ui.post(() -> togglePanel());
                } else if ("open_panel".equals(act)) {
                    ui.post(() -> { if (panel == null) togglePanel(); });
                } else if ("close_panel".equals(act)) {
                    ui.post(() -> collapsePanel());
                } else if ("listen_on".equals(act)) {
                    ui.post(() -> setListening(true));
                } else if ("listen_off".equals(act)) {
                    ui.post(() -> setListening(false));
                }
                if (cmd != null && !cmd.trim().isEmpty()) {
                    ui.post(() -> handleUserUtterance(cmd.trim(), true));
                }
            }
        };
        IntentFilter filter = new IntentFilter("musabai.overlay.CONTROL");
        if (Build.VERSION.SDK_INT >= 33) {
            registerReceiver(controlReceiver, filter, Context.RECEIVER_EXPORTED);
        } else {
            registerReceiver(controlReceiver, filter);
        }
    }

    private void restoreUserBackupIfPresent() {
        try {
            File backupRoot = new File("/sdcard/Download/musab_user_backup");
            if (!backupRoot.isDirectory()) return;
            File homeDir = new Setup(this).home;
            File targetNewal = new File(homeDir, ".newal-code");
            File marker = new File(targetNewal, ".backup_restored_v2");
            File srcNewal = new File(backupRoot, ".newal-code");
            if (srcNewal.isDirectory() && !marker.exists()) {
                copyRecursive(srcNewal, targetNewal);
                marker.getParentFile().mkdirs();
                try (FileOutputStream fos = new FileOutputStream(marker)) {
                    fos.write("ok".getBytes(StandardCharsets.UTF_8));
                }
            }
            File nvKeyFile = new File(backupRoot, "nvidia_key.txt");
            if (nvKeyFile.isFile()) {
                String key = Setup.read(nvKeyFile).trim();
                if (key.length() >= 10) {
                    ProviderSecrets.handle(this, new JSONObject()
                            .put("action", "provider_secret_set")
                            .put("provider", "nvidia")
                            .put("value", key));
                }
                nvKeyFile.delete();
            }
        } catch (Exception ignored) {}
    }

    private static void copyRecursive(File src, File dst) throws IOException {
        if (src.isDirectory()) {
            dst.mkdirs();
            File[] children = src.listFiles();
            if (children != null) {
                for (File child : children) {
                    copyRecursive(child, new File(dst, child.getName()));
                }
            }
        } else if (src.isFile()) {
            dst.getParentFile().mkdirs();
            try (InputStream in = new FileInputStream(src);
                 OutputStream out = new FileOutputStream(dst)) {
                byte[] buf = new byte[16384];
                int n;
                while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            }
        }
    }

    private void initTts() {
        try {
            tts = new TextToSpeech(this, state -> {
                ttsReady = state == TextToSpeech.SUCCESS;
                if (ttsReady && tts != null) {
                    int res = tts.setLanguage(new Locale("ar", "SA"));
                    if (res < 0) res = tts.setLanguage(new Locale("ar"));
                    ttsReady = res >= 0;
                    try {
                        tts.setSpeechRate(1.03f);
                        tts.setPitch(1.0f);
                    } catch (RuntimeException ignored) {}
                }
            });
        } catch (RuntimeException ignored) {
            tts = null;
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int id) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putBoolean("enabled_by_user", false).apply();
            stopSelf();
            return START_NOT_STICKY;
        }
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().putBoolean("enabled_by_user", true).apply();
        foreground();
        if (Build.VERSION.SDK_INT >= 23 && !Settings.canDrawOverlays(this)) {
            postNotice("اسمح لـ MusabAI بالظهور فوق التطبيقات من الإعدادات.");
            return START_STICKY;
        }
        if (bubble == null) showBubble();
        if (intent != null && ACTION_LISTEN.equals(intent.getAction())) {
            setListening(true);
        }
        if (intent != null && ACTION_TOGGLE_PANEL.equals(intent.getAction())) {
            ui.post(this::togglePanel);
        }
        if (intent != null && ACTION_COMMAND.equals(intent.getAction())) {
            String cmd = intent.getStringExtra("command");
            if (cmd != null && !cmd.trim().isEmpty()) {
                ui.post(() -> handleUserUtterance(cmd.trim(), true));
            }
        }
        return START_STICKY;
    }

    private void foreground() {
        Intent open = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pending = PendingIntent.getActivity(this, 607, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent stop = new Intent(this, FloatingAssistantService.class).setAction(ACTION_STOP);
        PendingIntent stopping = PendingIntent.getService(this, 608, stop,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Intent listenIntent = new Intent(this, FloatingAssistantService.class).setAction(ACTION_LISTEN);
        PendingIntent listenPending = PendingIntent.getService(this, 609, listenIntent,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

        Notification n = new Notification.Builder(this, CHANNEL)
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle("MusabAI • المساعد الصوتي العائم")
                .setContentText(listening ? "🎙 الاستماع النشط مفعّل — قل «أم» أو اطلب مهمتك" : "المساعد العائم جاهز على الشاشة")
                .setOngoing(true)
                .setContentIntent(pending)
                .addAction(new Notification.Action.Builder(null, "تفعيل الصوت", listenPending).build())
                .addAction(new Notification.Action.Builder(null, "إيقاف المساعد", stopping).build())
                .build();
        int type = Build.VERSION.SDK_INT >= 34 ? ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE : 0;
        if (Build.VERSION.SDK_INT >= 34 && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            type |= ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE;
        }
        try {
            if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIFICATION, n, type);
            else startForeground(NOTIFICATION, n);
        } catch (RuntimeException failure) {
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(NOTIFICATION, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
            } else {
                startForeground(NOTIFICATION, n);
            }
        }
    }

    private GradientDrawable bg(int color, float round) {
        return bgStroke(color, round, 0, 0);
    }

    private GradientDrawable bgStroke(int color, float round, int strokeDp, int strokeColor) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(dp(round));
        if (strokeDp > 0) d.setStroke(dp(strokeDp), strokeColor);
        return d;
    }

    private int dp(float value) {
        return (int) (getResources().getDisplayMetrics().density * value + .5f);
    }

    private TextView label(String text, int size, int color) {
        TextView t = new TextView(this);
        t.setText(text);
        t.setTextSize(size);
        t.setTextColor(color);
        t.setGravity(Gravity.CENTER_VERTICAL);
        t.setPadding(dp(10), dp(8), dp(10), dp(8));
        return t;
    }

    private void showBubble() {
        bubbleText = label("✦", 22, Color.WHITE);
        bubbleText.setGravity(Gravity.CENTER);
        bubbleText.setPadding(0, 0, 0, 0);
        bubbleText.setBackground(bgStroke(0xff087e70, 80, 2, 0x66ffffff));
        bubbleText.setElevation(dp(10));
        bubble = bubbleText;

        int screenW = getResources().getDisplayMetrics().widthPixels;
        int screenH = getResources().getDisplayMetrics().heightPixels;
        bubbleParams = new WindowManager.LayoutParams(dp(56), dp(56),
                Build.VERSION.SDK_INT >= 26 ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                        : WindowManager.LayoutParams.TYPE_PHONE,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                PixelFormat.TRANSLUCENT);
        bubbleParams.gravity = Gravity.TOP | Gravity.LEFT;
        bubbleParams.x = getSharedPreferences(PREFS, MODE_PRIVATE).getInt("bubble_x", screenW - dp(64));
        bubbleParams.y = getSharedPreferences(PREFS, MODE_PRIVATE).getInt("bubble_y", dp(160));
        bubbleParams.x = Math.max(0, Math.min(screenW - dp(56), bubbleParams.x));
        bubbleParams.y = Math.max(dp(28), Math.min(screenH - dp(120), bubbleParams.y));

        try {
            wm.addView(bubble, bubbleParams);
        } catch (RuntimeException e) {
            postNotice("تعذّر إظهار فقاعة MusabAI: " + e.getClass().getSimpleName());
            return;
        }

        createStatusPill();
        updateBubbleVisuals();

        bubble.setOnTouchListener(new View.OnTouchListener() {
            int x0, y0;
            float tx, ty;
            boolean dragged;
            boolean longPressTriggered;
            final Runnable longPressRunnable = () -> {
                if (!dragged) {
                    longPressTriggered = true;
                    if (speaking) {
                        stopSpeakingOnly();
                        postNotice("🔇 تم إيقاف القراءة الصوتية.");
                    } else {
                        setListening(!listening);
                    }
                }
            };

            @Override
            public boolean onTouch(View view, MotionEvent event) {
                if (event.getActionMasked() == MotionEvent.ACTION_DOWN) {
                    x0 = bubbleParams.x;
                    y0 = bubbleParams.y;
                    tx = event.getRawX();
                    ty = event.getRawY();
                    dragged = false;
                    longPressTriggered = false;
                    ui.postDelayed(longPressRunnable, 480);
                    return true;
                }
                if (event.getActionMasked() == MotionEvent.ACTION_MOVE) {
                    int dx = Math.round(event.getRawX() - tx);
                    int dy = Math.round(event.getRawY() - ty);
                    if (Math.abs(dx) + Math.abs(dy) > dp(10)) {
                        dragged = true;
                        ui.removeCallbacks(longPressRunnable);
                    }
                    int sw = getResources().getDisplayMetrics().widthPixels;
                    int sh = getResources().getDisplayMetrics().heightPixels;
                    bubbleParams.x = Math.max(0, Math.min(sw - dp(56), x0 + dx));
                    bubbleParams.y = Math.max(dp(28), Math.min(sh - dp(100), y0 + dy));
                    try {
                        wm.updateViewLayout(bubble, bubbleParams);
                        syncPillPosition();
                    } catch (RuntimeException ignored) {}
                    return true;
                }
                if (event.getActionMasked() == MotionEvent.ACTION_UP
                        || event.getActionMasked() == MotionEvent.ACTION_CANCEL) {
                    ui.removeCallbacks(longPressRunnable);
                    if (longPressTriggered) return true;
                    if (!dragged) {
                        togglePanel();
                    } else {
                        getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                                .putInt("bubble_x", bubbleParams.x)
                                .putInt("bubble_y", bubbleParams.y).apply();
                        final int start = bubbleParams.x;
                        final int width = getResources().getDisplayMetrics().widthPixels - dp(56);
                        final int end = start < width / 2 ? dp(6) : width - dp(6);
                        android.animation.ValueAnimator snap = android.animation.ValueAnimator.ofInt(start, end);
                        snap.setDuration(160);
                        snap.addUpdateListener(v -> {
                            bubbleParams.x = (int) v.getAnimatedValue();
                            if (bubble != null) {
                                try {
                                    wm.updateViewLayout(bubble, bubbleParams);
                                    syncPillPosition();
                                } catch (RuntimeException ignored) {}
                            }
                        });
                        snap.start();
                    }
                    return true;
                }
                return true;
            }
        });
    }

    /**
     * Non-focusable, non-touchable mini status pill next to the bubble so the user sees live voice recognition
     * and step-by-step phone execution while the main panel is collapsed, without blocking Accessibility!
     */
    private void createStatusPill() {
        if (statusPill != null) return;
        statusPill = new TextView(this);
        statusPill.setTextColor(Color.WHITE);
        statusPill.setTextSize(12);
        statusPill.setMaxLines(2);
        statusPill.setEllipsize(TextUtils.TruncateAt.END);
        statusPill.setPadding(dp(10), dp(6), dp(10), dp(6));
        statusPill.setBackground(bgStroke(0xe6101c26, 16, 1, 0xff2b5468));
        statusPill.setElevation(dp(8));
        statusPill.setVisibility(View.GONE);

        pillParams = new WindowManager.LayoutParams(
                dp(215),
                WindowManager.LayoutParams.WRAP_CONTENT,
                Build.VERSION.SDK_INT >= 26 ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                        : WindowManager.LayoutParams.TYPE_PHONE,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,
                PixelFormat.TRANSLUCENT);
        pillParams.gravity = Gravity.TOP | Gravity.LEFT;
        syncPillPosition();
        try {
            wm.addView(statusPill, pillParams);
        } catch (RuntimeException ignored) {
            statusPill = null;
        }
    }

    private void syncPillPosition() {
        if (statusPill == null || pillParams == null || bubbleParams == null) return;
        int screenW = getResources().getDisplayMetrics().widthPixels;
        boolean bubbleOnRight = bubbleParams.x > screenW / 2;
        pillParams.x = bubbleOnRight
                ? Math.max(dp(8), bubbleParams.x - dp(222))
                : Math.min(screenW - dp(222), bubbleParams.x + dp(62));
        pillParams.y = Math.max(dp(28), bubbleParams.y + dp(6));
        try {
            wm.updateViewLayout(statusPill, pillParams);
        } catch (RuntimeException ignored) {}
    }

    private void showPillMessage(String text) {
        if (statusPill == null || text == null || text.isEmpty()) return;
        if (panel != null) {
            statusPill.setVisibility(View.GONE);
            return;
        }
        statusPill.setText(text);
        syncPillPosition();
        statusPill.setVisibility(View.VISIBLE);
        ui.removeCallbacks(hidePillRunnable);
        if (!busy && !speaking) {
            ui.postDelayed(hidePillRunnable, 5500);
        }
    }

    private void togglePanel() {
        if (panel != null) {
            collapsePanel();
            return;
        }
        if (statusPill != null) statusPill.setVisibility(View.GONE);

        panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setLayoutDirection(View.LAYOUT_DIRECTION_RTL);
        panel.setPadding(dp(11), dp(10), dp(11), dp(9));
        panel.setBackground(bgStroke(0xff101b24, 22, 1, 0xff2f5266));
        panel.setElevation(dp(20));

        // 1. Header bar (draggable)
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setBackground(bg(0xff182b3a, 14));
        header.setPadding(dp(10), dp(6), dp(8), dp(6));

        TextView title = new TextView(this);
        title.setText("✦ مساعد MusabAI الصوتي");
        title.setTextSize(14);
        title.setTypeface(Typeface.DEFAULT_BOLD);
        title.setTextColor(Color.WHITE);
        header.addView(title, new LinearLayout.LayoutParams(0, -2, 1f));

        voiceToggleBtn = headerBtn(voiceReplyEnabled ? "🔊" : "🔇", v -> {
            voiceReplyEnabled = !voiceReplyEnabled;
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putBoolean("voice_reply", voiceReplyEnabled).apply();
            if (voiceToggleBtn != null) voiceToggleBtn.setText(voiceReplyEnabled ? "🔊" : "🔇");
            if (!voiceReplyEnabled) stopSpeakingOnly();
            postNotice(voiceReplyEnabled ? "🔊 الرد الصوتي مفعّل." : "🔇 الرد الصوتي مكتوم.");
        });
        header.addView(voiceToggleBtn);

        TextView newChatBtn = headerBtn("🔄", v -> resetConversationSession());
        header.addView(newChatBtn);

        TextView openAppBtn = headerBtn("↗", v -> {
            collapsePanel();
            Intent open = new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(open);
        });
        header.addView(openAppBtn);

        TextView closeBtn = headerBtn("✕", v -> collapsePanel());
        header.addView(closeBtn);

        header.setOnTouchListener(new View.OnTouchListener() {
            float y0;
            int top0;
            @Override
            public boolean onTouch(View v, MotionEvent event) {
                if (event.getActionMasked() == MotionEvent.ACTION_DOWN) {
                    y0 = event.getRawY();
                    top0 = panelParams == null ? 0 : panelParams.y;
                    return true;
                }
                if (event.getActionMasked() == MotionEvent.ACTION_MOVE && panelParams != null) {
                    panelParams.y = Math.max(dp(24), Math.min(
                            getResources().getDisplayMetrics().heightPixels - dp(320),
                            top0 + Math.round(event.getRawY() - y0)));
                    try {
                        wm.updateViewLayout(panel, panelParams);
                    } catch (RuntimeException ignored) {}
                    return true;
                }
                return event.getActionMasked() == MotionEvent.ACTION_UP;
            }
        });
        panel.addView(header);

        // 2. Live Status + Stop/Interrupt Row
        LinearLayout statusRow = new LinearLayout(this);
        statusRow.setOrientation(LinearLayout.HORIZONTAL);
        statusRow.setGravity(Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams statusRowLp = new LinearLayout.LayoutParams(-1, -2);
        statusRowLp.setMargins(0, dp(6), 0, dp(4));

        status = new TextView(this);
        status.setText(lastStatus);
        status.setTextSize(12);
        status.setTextColor(0xff9ad8eb);
        status.setMaxLines(2);
        status.setEllipsize(TextUtils.TruncateAt.END);
        status.setPadding(dp(6), dp(4), dp(6), dp(4));
        statusRow.addView(status, new LinearLayout.LayoutParams(0, -2, 1f));

        stopButton = new TextView(this);
        stopButton.setText("⏹ إيقاف");
        stopButton.setTextSize(12);
        stopButton.setTextColor(Color.WHITE);
        stopButton.setGravity(Gravity.CENTER);
        stopButton.setPadding(dp(10), dp(5), dp(10), dp(5));
        stopButton.setBackground(bg(0xffc83e3b, 10));
        stopButton.setVisibility((busy || speaking) ? View.VISIBLE : View.GONE);
        stopButton.setOnClickListener(v -> interruptCurrentTask());
        statusRow.addView(stopButton);

        panel.addView(statusRow, statusRowLp);

        // 3. Scrollable Mini Conversation History
        chatScroll = new ScrollView(this);
        chatScroll.setBackground(bg(0xff0b131a, 14));
        chatScroll.setPadding(dp(8), dp(6), dp(8), dp(6));
        chatContainer = new LinearLayout(this);
        chatContainer.setOrientation(LinearLayout.VERTICAL);
        chatScroll.addView(chatContainer, new ScrollView.LayoutParams(-1, -2));
        LinearLayout.LayoutParams scrollLp = new LinearLayout.LayoutParams(-1, dp(150));
        scrollLp.setMargins(0, dp(2), 0, dp(6));
        panel.addView(chatScroll, scrollLp);
        renderChatHistory();

        // 4. Quick Action Chips
        HorizontalScrollView chipsScroll = new HorizontalScrollView(this);
        chipsScroll.setHorizontalScrollBarEnabled(false);
        LinearLayout chipsRow = new LinearLayout(this);
        chipsRow.setOrientation(LinearLayout.HORIZONTAL);
        addQuickChip(chipsRow, "👋 أم", () -> handleUserUtterance("أم", false));
        addQuickChip(chipsRow, "👀 اقرأ الشاشة", () -> handleUserUtterance("اقرأ محتوى الشاشة الحالية واشرح لي أهم ما فيها باختصار", false));
        addQuickChip(chipsRow, "🔦 الفلاش", () -> handleUserUtterance("شغل الفلاش", false));
        addQuickChip(chipsRow, "🌑 إطفاء الفلاش", () -> handleUserUtterance("طفي الفلاش", false));
        addQuickChip(chipsRow, "🔋 البطارية", () -> handleUserUtterance("كم البطارية", false));
        addQuickChip(chipsRow, "🕒 الساعة", () -> handleUserUtterance("كم الساعة", false));
        addQuickChip(chipsRow, "🏠 الرئيسية", () -> handleUserUtterance("الشاشة الرئيسية", false));
        addQuickChip(chipsRow, "🔙 رجوع", () -> handleUserUtterance("رجوع", false));
        chipsScroll.addView(chipsRow);
        LinearLayout.LayoutParams chipsLp = new LinearLayout.LayoutParams(-1, -2);
        chipsLp.setMargins(0, 0, 0, dp(6));
        panel.addView(chipsScroll, chipsLp);

        // 5. Input Field
        input = new EditText(this);
        input.setTextColor(Color.WHITE);
        input.setHintTextColor(0xff849aa6);
        input.setTextSize(14);
        input.setSingleLine(false);
        input.setMaxLines(3);
        input.setHint("احكي «أم» أو اكتب أي مهمة هون...");
        input.setBackground(bgStroke(0xff1c2b36, 12, 1, 0xff314b5c));
        input.setPadding(dp(11), dp(8), dp(11), dp(8));
        LinearLayout.LayoutParams editParams = new LinearLayout.LayoutParams(-1, dp(54));
        editParams.setMargins(0, 0, 0, dp(7));
        panel.addView(input, editParams);

        // 6. Action Buttons (Send + Continuous Voice Toggle)
        LinearLayout actions = new LinearLayout(this);
        actions.setOrientation(LinearLayout.HORIZONTAL);

        TextView send = label("إرسال المهمة ↗", 13, Color.WHITE);
        send.setTypeface(Typeface.DEFAULT_BOLD);
        send.setGravity(Gravity.CENTER);
        send.setBackground(bg(0xff087e70, 12));
        actions.addView(send, new LinearLayout.LayoutParams(0, dp(42), 1f));
        send.setOnClickListener(v -> {
            String text = input.getText().toString().trim();
            if (!text.isEmpty()) {
                input.setText("");
                handleUserUtterance(text, false);
            }
        });

        micButton = label("", 13, Color.WHITE);
        micButton.setTypeface(Typeface.DEFAULT_BOLD);
        micButton.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams mp = new LinearLayout.LayoutParams(0, dp(42), 1.1f);
        mp.setMargins(dp(7), 0, 0, 0);
        actions.addView(micButton, mp);
        micButton.setOnClickListener(v -> setListening(!listening));
        panel.addView(actions);

        updateBubbleVisuals();

        int width = Math.min(getResources().getDisplayMetrics().widthPixels - dp(24), dp(340));
        panelParams = new WindowManager.LayoutParams(width, -2,
                Build.VERSION.SDK_INT >= 26 ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                        : WindowManager.LayoutParams.TYPE_PHONE,
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        panelParams.gravity = Gravity.TOP | Gravity.CENTER_HORIZONTAL;
        panelParams.y = Math.max(dp(36), Math.min(
                getResources().getDisplayMetrics().heightPixels - dp(410),
                bubbleParams.y + dp(58)));
        panelParams.softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE;
        try {
            wm.addView(panel, panelParams);
        } catch (RuntimeException e) {
            panel = null;
            postNotice("تعذّر فتح نافذة المحادثة");
        }
    }

    private TextView headerBtn(String icon, View.OnClickListener click) {
        TextView b = new TextView(this);
        b.setText(icon);
        b.setTextSize(14);
        b.setTextColor(Color.WHITE);
        b.setGravity(Gravity.CENTER);
        b.setPadding(dp(8), dp(4), dp(8), dp(4));
        b.setOnClickListener(click);
        return b;
    }

    private void addQuickChip(LinearLayout parent, String text, Runnable action) {
        TextView chip = new TextView(this);
        chip.setText(text);
        chip.setTextSize(12);
        chip.setTextColor(0xffd7eef7);
        chip.setBackground(bgStroke(0xff1a2d3b, 14, 1, 0xff2e4f63));
        chip.setPadding(dp(10), dp(5), dp(10), dp(5));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-2, -2);
        lp.setMargins(0, 0, dp(6), 0);
        chip.setOnClickListener(v -> action.run());
        parent.addView(chip, lp);
    }

    private void addHistoryEntry(String role, String text) {
        if (text == null || text.trim().isEmpty()) return;
        ui.post(() -> {
            // Avoid duplicate consecutive step entries
            if ("step".equals(role) && !history.isEmpty()
                    && "step".equals(history.get(history.size() - 1).role)) {
                history.set(history.size() - 1, new ChatEntry(role, text.trim()));
            } else {
                history.add(new ChatEntry(role, text.trim()));
                while (history.size() > 24) history.remove(0);
            }
            renderChatHistory();
        });
    }

    private void renderChatHistory() {
        if (chatContainer == null) return;
        chatContainer.removeAllViews();
        for (ChatEntry entry : history) {
            TextView tv = new TextView(this);
            tv.setTextSize("step".equals(entry.role) ? 11.5f : 13f);
            tv.setPadding(dp(10), dp(6), dp(10), dp(6));
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-2, -2);
            lp.setMargins(0, dp(3), 0, dp(3));

            if ("user".equals(entry.role)) {
                tv.setText("🗣 أنت: " + entry.text);
                tv.setTextColor(Color.WHITE);
                tv.setBackground(bg(0xff0b6a5e, 12));
                lp.gravity = Gravity.START;
            } else if ("step".equals(entry.role)) {
                tv.setText(entry.text);
                tv.setTextColor(0xffffd580);
                tv.setBackground(bg(0xff262318, 10));
                lp.gravity = Gravity.CENTER_HORIZONTAL;
            } else {
                tv.setText("🤖 MusabAI: " + entry.text);
                tv.setTextColor(0xffe6f4f8);
                tv.setBackground(bg(0xff1a2b38, 12));
                lp.gravity = Gravity.END;
                tv.setOnClickListener(v -> speakAnswer(entry.text, true));
            }
            chatContainer.addView(tv, lp);
        }
        if (chatScroll != null) {
            chatScroll.post(() -> chatScroll.fullScroll(View.FOCUS_DOWN));
        }
    }

    private void postNotice(String text) {
        ui.post(() -> {
            lastStatus = text;
            if (status != null) status.setText(text);
            updateBubbleVisuals();
            showPillMessage(text);
        });
    }

    private void updateBubbleVisuals() {
        ui.post(() -> {
            if (micButton != null) {
                micButton.setText(listening ? "● استماع نشط (اضغط للإيقاف)" : "🎙 محادثة صوتية مستمرة");
                micButton.setBackground(bg(listening ? 0xff0d9488 : 0xff364958, 12));
            }
            if (stopButton != null) {
                stopButton.setVisibility((busy || speaking) ? View.VISIBLE : View.GONE);
            }
            if (bubbleText != null) {
                if (busy) {
                    bubbleText.setText("⏳");
                    bubbleText.setBackground(bgStroke(0xffd97706, 80, 2, 0xffffffff));
                } else if (speaking) {
                    bubbleText.setText("🔊");
                    bubbleText.setBackground(bgStroke(0xff2563eb, 80, 2, 0xffffffff));
                } else if (listening) {
                    bubbleText.setText("🎙");
                    bubbleText.setBackground(bgStroke(0xff0d9488, 80, 2, 0x99ffffff));
                } else {
                    bubbleText.setText("✦");
                    bubbleText.setBackground(bgStroke(0xff087e70, 80, 2, 0x55ffffff));
                }
            }
        });
    }

    private void setListening(boolean enable) {
        if (!enable) {
            listening = false;
            ui.removeCallbacks(restartSpeech);
            if (speech != null) {
                try { speech.cancel(); speech.destroy(); } catch (RuntimeException ignored) {}
                speech = null;
            }
            foreground();
            updateBubbleVisuals();
            postNotice("الاستماع الصوتي متوقف. اضغط 🎙 أو مطولاً على الفقاعة للتحدث.");
            return;
        }
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            postNotice("يلزم السماح بالميكروفون من داخل تطبيق MusabAI.");
            return;
        }
        if (!SpeechRecognizer.isRecognitionAvailable(this)) {
            postNotice("خدمة التعرف الصوتي غير متوفرة على هذا الجهاز.");
            return;
        }
        try {
            listening = true;
            speechFailures = 0;
            foreground();
            updateBubbleVisuals();
            startRecognition();
        } catch (RuntimeException problem) {
            listening = false;
            updateBubbleVisuals();
            postNotice("أندرويد منع تشغيل الميكروفون بالخلفية؛ افتح MusabAI لتفعيله.");
        }
    }

    private void startRecognition() {
        if (!listening || destroyed || speaking) return;
        long sinceTts = SystemClock.elapsedRealtime() - lastTtsEndedAt;
        if (sinceTts >= 0 && sinceTts < 700) {
            ui.removeCallbacks(restartSpeech);
            ui.postDelayed(restartSpeech, 700 - sinceTts);
            return;
        }
        try {
            if (speech == null) {
                speech = SpeechRecognizer.createSpeechRecognizer(this);
                speech.setRecognitionListener(this);
            }
            String lang = (localeIndex % 2 == 0) ? "ar-SA" : "ar";
            Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, lang);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_PREFERENCE, lang);
            intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
            intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3);
            speech.startListening(intent);
            if (!busy) {
                if (status != null) status.setText("🎙 عم اسمعك — قل «أم» أو احكي مهمتك مباشرة...");
                updateBubbleVisuals();
            }
        } catch (RuntimeException e) {
            speechFailures++;
            if (speech != null) {
                try { speech.destroy(); } catch (RuntimeException ignored) {}
                speech = null;
            }
            retrySpeech();
        }
    }

    private void retrySpeech() {
        if (!listening || destroyed || speaking) return;
        ui.removeCallbacks(restartSpeech);
        long delay = speechFailures == 0 ? 550L : Math.min(3000L, 700L + speechFailures * 350L);
        ui.postDelayed(restartSpeech, delay);
    }

    @Override public void onReadyForSpeech(Bundle p) {}
    @Override public void onBeginningOfSpeech() {}

    @Override
    public void onRmsChanged(float rms) {
        if (!listening || busy || speaking || bubbleText == null) return;
        if (rms > 4.5f) {
            bubbleText.setBackground(bgStroke(0xff10b981, 80, 3, 0xffffffff));
        } else {
            bubbleText.setBackground(bgStroke(0xff0d9488, 80, 2, 0x99ffffff));
        }
    }

    @Override public void onBufferReceived(byte[] b) {}
    @Override public void onEndOfSpeech() {}

    @Override
    public void onPartialResults(Bundle b) {
        if (!listening || speaking || b == null) return;
        ArrayList<String> partial = b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        if (partial != null && !partial.isEmpty()) {
            String text = partial.get(0).trim();
            if (!text.isEmpty()) {
                String preview = "🎙 " + text + " …";
                if (status != null) status.setText(preview);
                showPillMessage(preview);
            }
        }
    }

    @Override public void onEvent(int eventType, Bundle params) {}

    @Override
    public void onError(int error) {
        if (!listening || speaking) return;
        if (error == SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS
                && checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            setListening(false);
            postNotice("يلزم منح صلاحية الميكروفون للتحدث مع الفقاعة.");
            return;
        }
        if (error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT) {
            speechFailures = 0;
        } else {
            speechFailures = Math.min(6, speechFailures + 1);
            if (error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY
                    || error == SpeechRecognizer.ERROR_CLIENT
                    || error == SpeechRecognizer.ERROR_SERVER_DISCONNECTED) {
                if (speech != null) {
                    try { speech.cancel(); speech.destroy(); } catch (RuntimeException ignored) {}
                    speech = null;
                }
            }
            if (error == SpeechRecognizer.ERROR_LANGUAGE_NOT_SUPPORTED) {
                localeIndex++;
            }
        }
        retrySpeech();
    }

    @Override
    public void onResults(Bundle data) {
        if (!listening || speaking) return;
        speechFailures = 0;
        if (SystemClock.elapsedRealtime() - lastTtsEndedAt < 900) {
            retrySpeech();
            return;
        }
        ArrayList<String> words = data == null ? null : data.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        String transcript = "";
        if (words != null) {
            // Check if any candidate is a valid wake word or command
            for (String candidate : words) {
                if (candidate != null && !QuickPhoneActions.isLikelyEchoOrNoise(candidate)) {
                    transcript = candidate.trim();
                    break;
                }
            }
        }
        if (!transcript.isEmpty()) {
            long now = SystemClock.elapsedRealtime();
            if (!transcript.equals(lastSpeech) || now - lastSpeechAt > 3000) {
                lastSpeech = transcript;
                lastSpeechAt = now;
                handleUserUtterance(transcript, true);
            }
        }
        retrySpeech();
    }

    /**
     * Unified entrypoint for both spoken and typed user input.
     * Handles wake words ("ام", "يا مصعب"), stop/interrupt, status queries, repeat requests,
     * instant native phone actions, and full agent tasks.
     */
    private void handleUserUtterance(String rawInput, boolean fromVoice) {
        String cleaned = QuickPhoneActions.cleanPunctuation(rawInput);
        if (cleaned.isEmpty()) return;

        // 1. Check stop / cancel command first (works even while busy or speaking)
        if (QuickPhoneActions.isStopCommand(cleaned)) {
            addHistoryEntry("user", cleaned);
            interruptCurrentTask();
            return;
        }

        // 2. Check status query while busy ("شو عم تعمل", "وين وصلت")
        if (QuickPhoneActions.isStatusQuery(cleaned)) {
            addHistoryEntry("user", cleaned);
            String msg = busy
                    ? ("حالياً أعمل على: " + currentStepSummary)
                    : "لا توجد مهمة قيد التنفيذ حالياً، أنا جاهز لأي طلب جديد.";
            addHistoryEntry("assistant", msg);
            postNotice("ℹ️ " + msg);
            speakAnswer(msg, true);
            return;
        }

        // 3. Check repeat request ("عيد", "شو قلت")
        if (QuickPhoneActions.isRepeatQuery(cleaned)) {
            addHistoryEntry("user", cleaned);
            String msg = lastAnswer.isEmpty() ? "لم أقدم إجابة سابقة بعد، تفضل بطلبك." : lastAnswer;
            postNotice("🔁 " + (msg.length() > 180 ? msg.substring(0, 180) + "…" : msg));
            speakAnswer(msg, true);
            return;
        }

        // 4. Check new session request ("محادثة جديدة", "جلسة جديدة")
        if (QuickPhoneActions.isNewSessionCommand(cleaned)) {
            addHistoryEntry("user", cleaned);
            resetConversationSession();
            return;
        }

        // 5. Check standalone conversational / wake phrase ("ام", "أم", "يا مصعب", "مرحبا", "كيفك")
        String convReply = QuickPhoneActions.conversationalReply(cleaned);
        if (convReply != null) {
            if (!listening && checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                listening = true;
                speechFailures = 0;
                foreground();
            }
            addHistoryEntry("user", cleaned);
            addHistoryEntry("assistant", convReply);
            lastAnswer = convReply;
            postNotice("💬 " + convReply);
            speakAnswer(convReply, true);
            return;
        }

        // 6. Strip leading wake/filler words from task commands (e.g. "ام افتح واتساب" -> "افتح واتساب")
        String command = QuickPhoneActions.normalizeCommand(cleaned);
        if (command.isEmpty()) command = cleaned;

        addHistoryEntry("user", cleaned);

        // 7. Try fast native phone actions (open any installed app, flashlight, volume, battery, time, navigation)
        try {
            QuickPhoneActions.ActionResult quick = QuickPhoneActions.executeQuickAction(this, command);
            if (quick.handled) {
                if (quick.collapsePanel) collapsePanel();
                addHistoryEntry("assistant", quick.displayMessage);
                lastAnswer = quick.spokenMessage;
                postNotice(quick.displayMessage);
                speakAnswer(quick.spokenMessage, true);
                return;
            }
        } catch (Exception e) {
            String err = "تعذّر تنفيذ الإجراء السريع: " + e.getMessage();
            postNotice(err);
            speakAnswer("تعذر تنفيذ الإجراء السريع.", true);
            return;
        }

        // 8. Full AI Agent Task Execution
        sendCommandToAgent(command);
    }

    private void resetConversationSession() {
        sessionId = "";
        pendingCommand = "";
        getSharedPreferences(PREFS, MODE_PRIVATE).edit().remove("session").apply();
        history.clear();
        String msg = "بدأت محادثة جديدة. قل لي «أم» أو اطلب المهمة التي تريدها.";
        history.add(new ChatEntry("assistant", msg));
        renderChatHistory();
        postNotice("🔄 " + msg);
        speakAnswer("بدأت محادثة جديدة، تفضل بطلبك.", true);
    }

    private void interruptCurrentTask() {
        cancelRequested = true;
        pendingCommand = "";
        stopSpeakingOnly();
        final String activeSession = sessionId;
        if (busy && !activeSession.isEmpty()) {
            worker.execute(() -> {
                try {
                    request("POST", "/api/sessions/" + activeSession + "/interrupt", new JSONObject());
                } catch (Exception ignored) {}
            });
        }
        busy = false;
        currentStepSummary = "تم إيقاف المهمة";
        String msg = "تم إيقاف المهمة الحالية بناءً على طلبك.";
        addHistoryEntry("assistant", msg);
        postNotice("⏹ " + msg);
        speakAnswer("تم إيقاف المهمة.", true);
    }

    private void stopSpeakingOnly() {
        ui.removeCallbacks(ttsSafetyReset);
        if (tts != null && ttsReady) {
            try { tts.stop(); } catch (RuntimeException ignored) {}
        }
        speaking = false;
        updateBubbleVisuals();
        if (listening && !destroyed) retrySpeech();
    }

    private JSONObject request(String method, String url, JSONObject payload) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL("http://127.0.0.1:" + Setup.PORT + url).openConnection();
        c.setConnectTimeout(4500);
        c.setReadTimeout(15000);
        c.setRequestMethod(method);
        c.setRequestProperty("X-NewAl-Key", new Setup(this).key());
        c.setRequestProperty("Accept", "application/json");
        if (payload != null) {
            c.setDoOutput(true);
            c.setRequestProperty("Content-Type", "application/json");
            byte[] body = payload.toString().getBytes(StandardCharsets.UTF_8);
            try (OutputStream stream = c.getOutputStream()) {
                stream.write(body);
            }
        }
        try {
            int code = c.getResponseCode();
            InputStream in = code < 400 ? c.getInputStream() : c.getErrorStream();
            if (in == null) throw new IOException("Local engine: HTTP " + code);
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            byte[] chunk = new byte[2048];
            int n;
            while ((n = in.read(chunk)) != -1 && bytes.size() < 500000) bytes.write(chunk, 0, n);
            if (code >= 400) throw new IOException("Local engine: HTTP " + code);
            return new JSONObject(bytes.toString("UTF-8"));
        } finally {
            c.disconnect();
        }
    }

    /**
     * Cleans markdown, code blocks, URLs, and symbols so Arabic TTS reads smoothly and naturally.
     */
    private static String cleanForSpeech(String raw) {
        if (raw == null) return "";
        String s = raw
                .replaceAll("```[\\s\\S]*?```", " ، تم إرفاق الكود في المحادثة ، ")
                .replaceAll("`([^`]+)`", "$1")
                .replaceAll("https?://\\S+", " رابط ")
                .replaceAll("\\[([^\\]]+)\\]\\([^)]+\\)", "$1")
                .replaceAll("[*_~#>`|•✓⏳🎙🔊🔇⚠️ℹ️🔁⏹✦◉⋯]+", " ")
                .replaceAll("\\s+", " ")
                .trim();
        if (s.length() > 650) {
            s = s.substring(0, 650) + " ... باقي التفاصيل موجودة في المحادثة.";
        }
        return s;
    }

    private void speakAnswer(String rawText, boolean forceSpeak) {
        if ((!voiceReplyEnabled && !forceSpeak) || tts == null || !ttsReady || rawText == null) return;
        final String clean = cleanForSpeech(rawText);
        if (clean.isEmpty()) return;

        ui.post(() -> {
            if (destroyed) return;
            speaking = true;
            updateBubbleVisuals();
            ui.removeCallbacks(restartSpeech);
            ui.removeCallbacks(ttsSafetyReset);
            if (speech != null) {
                try { speech.cancel(); speech.destroy(); } catch (RuntimeException ignored) {}
                speech = null;
            }

            List<String> chunks = splitIntoSentences(clean, 180);
            if (chunks.isEmpty()) {
                resumeListeningAfterSpeech();
                return;
            }

            tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                @Override public void onStart(String id) {}
                @Override public void onDone(String id) {
                    if ("musabai-reply-last".equals(id)) {
                        resumeListeningAfterSpeech();
                    }
                }
                @Override public void onError(String id) {
                    resumeListeningAfterSpeech();
                }
            });

            int lastIdx = chunks.size() - 1;
            int status = TextToSpeech.SUCCESS;
            for (int i = 0; i <= lastIdx; i++) {
                String uid = (i == lastIdx) ? "musabai-reply-last" : ("musabai-reply-" + i);
                int queueMode = (i == 0) ? TextToSpeech.QUEUE_FLUSH : TextToSpeech.QUEUE_ADD;
                int r = tts.speak(chunks.get(i), queueMode, null, uid);
                if (r == TextToSpeech.ERROR) status = TextToSpeech.ERROR;
            }
            // Safety fallback in case Android TTS engine does not fire onDone
            long estimatedMs = Math.max(4000L, Math.min(35000L, clean.length() * 95L));
            ui.postDelayed(ttsSafetyReset, estimatedMs);
            if (status == TextToSpeech.ERROR) {
                resumeListeningAfterSpeech();
            }
        });
    }

    private static List<String> splitIntoSentences(String text, int maxChunkLen) {
        List<String> out = new ArrayList<>();
        String[] parts = text.split("(?<=[.!؟،\\n])\\s+");
        StringBuilder current = new StringBuilder();
        for (String p : parts) {
            String trimmed = p.trim();
            if (trimmed.isEmpty()) continue;
            if (current.length() + trimmed.length() + 1 <= maxChunkLen) {
                if (current.length() > 0) current.append(' ');
                current.append(trimmed);
            } else {
                if (current.length() > 0) {
                    out.add(current.toString());
                    current.setLength(0);
                }
                while (trimmed.length() > maxChunkLen) {
                    out.add(trimmed.substring(0, maxChunkLen));
                    trimmed = trimmed.substring(maxChunkLen).trim();
                }
                if (!trimmed.isEmpty()) current.append(trimmed);
            }
        }
        if (current.length() > 0) out.add(current.toString());
        return out;
    }

    private void resumeListeningAfterSpeech() {
        ui.post(() -> {
            ui.removeCallbacks(ttsSafetyReset);
            speaking = false;
            lastTtsEndedAt = SystemClock.elapsedRealtime();
            updateBubbleVisuals();
            ui.removeCallbacks(hidePillRunnable);
            if (!busy) {
                ui.postDelayed(hidePillRunnable, 4000);
            }
            if (listening && !destroyed) {
                ui.removeCallbacks(restartSpeech);
                ui.postDelayed(restartSpeech, 750);
            }
        });
    }

    /** Remove the focusable panel so Accessibility sees the target app (WhatsApp/Chrome/etc.), not our overlay. */
    private void collapsePanel() {
        if (panel != null) {
            try { wm.removeView(panel); } catch (RuntimeException ignored) {}
            panel = null;
            panelParams = null;
            input = null;
            micButton = null;
            status = null;
            stopButton = null;
            voiceToggleBtn = null;
            chatScroll = null;
            chatContainer = null;
        }
    }

    private boolean isPhoneInteractionTask(String words) {
        String f = TextFold.fold(words);
        return f.contains("واتساب") || f.contains("whatsapp") || f.contains("يوتيوب") || f.contains("youtube")
                || f.contains("تيليجرام") || f.contains("كروم") || f.contains("chrome") || f.contains("انستجرام")
                || f.contains("فيسبوك") || f.contains("تيك توك") || f.contains("شاشه") || f.contains("اضغط")
                || f.contains("انقر") || f.contains("اكتب") || f.contains("ابحث") || f.contains("ارسل")
                || f.contains("ابعث") || f.contains("افتح") || f.contains("شغل") || f.contains("تطبيق");
    }

    private static String describeToolInArabic(String toolName) {
        if (toolName == null || toolName.isEmpty()) return "تنفيذ خطوة";
        String t = toolName.toLowerCase(Locale.ROOT);
        if (t.contains("phone") || t.contains("screen") || t.contains("tap") || t.contains("accessibility")) {
            return "التحكم بشاشة الهاتف";
        }
        if (t.contains("bash") || t.contains("shell") || t.contains("command") || t.contains("exec")) {
            return "تشغيل أمر في النظام";
        }
        if (t.contains("web") || t.contains("search") || t.contains("fetch") || t.contains("http")) {
            return "البحث وجلب المعلومات";
        }
        if (t.contains("read") || t.contains("grep") || t.contains("glob") || t.contains("list")) {
            return "قراءة وفحص الملفات";
        }
        if (t.contains("write") || t.contains("edit") || t.contains("patch")) {
            return "كتابة وتعديل الملفات";
        }
        return "تشغيل أداة " + toolName;
    }

    private void sendCommandToAgent(String message) {
        if (message.length() > MAX_COMMAND) {
            postNotice("الطلب طويل جداً؛ الحد الأقصى 4000 حرف.");
            return;
        }
        if (busy) {
            pendingCommand = message;
            String queuedMsg = "حاضر! حفظت طلبك («" + message + "») وسأنفذه فور انتهاء المهمة الحالية، أو قل «وقف» لإلغاء المهمة الحالية.";
            addHistoryEntry("assistant", queuedMsg);
            postNotice("📥 " + queuedMsg);
            speakAnswer("حاضر، رح نفذ طلبك مباشرة بعد ما تخلص المهمة الحالية.", false);
            return;
        }

        boolean phoneTask = isPhoneInteractionTask(message);
        if (phoneTask && PhoneControlService.on == null) {
            String warn = "فعّل خدمة إمكانية الوصول (NewAl Code) من الإعدادات لأتمكن من التحكم بالشاشة.";
            addHistoryEntry("assistant", warn);
            postNotice("⚠️ " + warn);
            speakAnswer("يلزم تفعيل إمكانية الوصول للتحكم بالشاشة.", true);
            return;
        }

        // Collapse the focusable panel when controlling phone apps so Accessibility sees the underlying app window.
        if (phoneTask) {
            collapsePanel();
        }

        busy = true;
        cancelRequested = false;
        currentStepSummary = "تحليل الطلب: " + message;
        updateBubbleVisuals();
        postNotice("⏳ جاري التنفيذ: " + message);
        speakAnswer(phoneTask ? "حاضر، عم نفذ المهمة على الهاتف." : "حاضر، عم اشتغل عليها.", false);

        worker.execute(() -> {
            try {
                JSONObject state = request("GET", "/api/state", null);
                String model = state.optJSONObject("settings") == null ? "auto" :
                        state.optJSONObject("settings").optString("model", "auto");
                if (!sessionId.isEmpty()) {
                    try {
                        JSONObject existing = request("GET", "/api/sessions/" + sessionId, null);
                        JSONObject meta = existing.optJSONObject("meta");
                        if (meta != null && !model.equals(meta.optString("model"))) {
                            request("POST", "/api/sessions/" + sessionId + "/settings",
                                    new JSONObject().put("model", model));
                        }
                    } catch (Exception missing) {
                        sessionId = "";
                    }
                }
                if (sessionId.isEmpty()) {
                    JSONObject spec = new JSONObject()
                            .put("root", new Setup(this).home.getAbsolutePath())
                            .put("model", model)
                            .put("mode", "full-auto")
                            .put("warm", false);
                    sessionId = request("POST", "/api/sessions", spec).getString("id");
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString("session", sessionId).apply();
                }

                JSONObject existingBeforeSend = request("GET", "/api/sessions/" + sessionId, null);
                JSONArray eventsBefore = existingBeforeSend.optJSONArray("events");
                int seenEvents = eventsBefore == null ? 0 : eventsBefore.length();

                JSONObject r = request("POST", "/api/sessions/" + sessionId + "/send",
                        new JSONObject().put("text", message).put("lang", "ar"));
                if (!r.optBoolean("started", false)) {
                    String reply = r.optString("reply", "تم تنفيذ الطلب.");
                    busy = false;
                    lastAnswer = reply;
                    addHistoryEntry("assistant", reply);
                    postNotice(reply);
                    speakAnswer(reply, false);
                    runPendingIfAny();
                    return;
                }

                int stepCounter = 0;
                long lastSpokenProgressAt = SystemClock.elapsedRealtime();

                for (int t = 0; t < 1800 && !destroyed && !cancelRequested; t++) {
                    Thread.sleep(750);
                    if (cancelRequested) break;
                    JSONObject result = request("GET", "/api/sessions/" + sessionId, null);
                    JSONArray events = result.optJSONArray("events");

                    if (events != null && events.length() > seenEvents) {
                        for (int i = seenEvents; i < events.length(); i++) {
                            JSONObject ev = events.optJSONObject(i);
                            if (ev == null) continue;
                            String evType = ev.optString("type");
                            if ("tool_start".equals(evType)) {
                                stepCounter++;
                                String toolName = ev.optString("name", ev.optString("tool", ""));
                                String desc = describeToolInArabic(toolName);
                                currentStepSummary = "الخطوة " + stepCounter + ": " + desc;
                                String stepMsg = "⚡ " + currentStepSummary;
                                addHistoryEntry("step", stepMsg);
                                postNotice(stepMsg);
                                long now = SystemClock.elapsedRealtime();
                                if (now - lastSpokenProgressAt > 14000L && !speaking) {
                                    lastSpokenProgressAt = now;
                                    speakAnswer("أنا بالخطوة " + stepCounter + "، " + desc, false);
                                }
                            } else if ("status".equals(evType) || "task_heartbeat".equals(evType)) {
                                String st = ev.optString("message", ev.optString("status", "")).trim();
                                if (!st.isEmpty()) {
                                    currentStepSummary = st;
                                    postNotice("⏳ " + st);
                                }
                            }
                        }
                        seenEvents = events.length();
                    }

                    if (result.optBoolean("busy", false)) continue;

                    String answer = "";
                    if (events != null) {
                        for (int i = events.length() - 1; i >= 0; i--) {
                            JSONObject ev = events.optJSONObject(i);
                            if (ev == null) continue;
                            String eventType = ev.optString("type");
                            if ("turn_end".equals(eventType) || "reply".equals(eventType) || "error".equals(eventType)) {
                                answer = ev.optString("answer", ev.optString("text", ev.optString("message", ""))).trim();
                                if (!answer.isEmpty()) break;
                            }
                        }
                    }
                    final String finalAnswer = answer.isEmpty()
                            ? "أنهيت تنفيذ المهمة بنجاح! هل تريد مني شيئاً آخر؟"
                            : answer;
                    busy = false;
                    currentStepSummary = "اكتملت المهمة";
                    lastAnswer = finalAnswer;
                    addHistoryEntry("assistant", finalAnswer);
                    postNotice("✓ " + (finalAnswer.length() > 260 ? finalAnswer.substring(0, 260) + "…" : finalAnswer));
                    speakAnswer(finalAnswer, false);
                    runPendingIfAny();
                    return;
                }

                if (busy && !cancelRequested) {
                    busy = false;
                    String timeoutMsg = "المهمة أخذت وقتاً طويلاً؛ افحص تطبيق MusabAI للتفاصيل.";
                    addHistoryEntry("assistant", timeoutMsg);
                    postNotice("⚠️ " + timeoutMsg);
                    speakAnswer(timeoutMsg, false);
                }
            } catch (Exception ex) {
                if (!cancelRequested) {
                    busy = false;
                    sessionId = "";
                    getSharedPreferences(PREFS, MODE_PRIVATE).edit().remove("session").apply();
                    String errMsg = "تعذّر الاتصال بمحرك MusabAI (" + ex.getClass().getSimpleName() + "). افتح التطبيق للتأكد من تشغيله.";
                    addHistoryEntry("assistant", errMsg);
                    postNotice("⚠️ " + errMsg);
                    speakAnswer("تعذر الاتصال بالمحرك، افتح التطبيق للتأكد من تشغيله.", false);
                }
            }
        });
    }

    private void runPendingIfAny() {
        final String next = pendingCommand;
        if (next != null && !next.isEmpty() && !cancelRequested) {
            pendingCommand = "";
            ui.postDelayed(() -> sendCommandToAgent(next), 1200);
        }
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }

    @Override
    public void onDestroy() {
        destroyed = true;
        listening = false;
        ui.removeCallbacksAndMessages(null);
        if (controlReceiver != null) {
            try { unregisterReceiver(controlReceiver); } catch (RuntimeException ignored) {}
            controlReceiver = null;
        }
        if (speech != null) {
            try { speech.cancel(); speech.destroy(); } catch (RuntimeException ignored) {}
        }
        if (panel != null) {
            try { wm.removeView(panel); } catch (RuntimeException ignored) {}
            panel = null;
        }
        if (statusPill != null) {
            try { wm.removeView(statusPill); } catch (RuntimeException ignored) {}
            statusPill = null;
        }
        if (bubble != null) {
            try { wm.removeView(bubble); } catch (RuntimeException ignored) {}
            bubble = null;
        }
        if (tts != null) {
            try { tts.shutdown(); } catch (RuntimeException ignored) {}
        }
        worker.shutdownNow();
        super.onDestroy();
    }
}
