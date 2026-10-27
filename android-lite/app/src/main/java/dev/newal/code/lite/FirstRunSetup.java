package dev.newal.code.lite;

import android.Manifest;
import android.app.AlertDialog;
import android.content.*;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.InputType;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.Toast;

import org.json.JSONObject;

/**
 * Guided, resumable setup. A single start button automatically performs everything
 * an ordinary Android app is legally allowed to do. Android's accessibility, overlay,
 * runtime mic and Termux authorizations still require actual user approval.
 */
final class FirstRunSetup {
    static final int MIC_REQUEST = 504;
    static final int TERMUX_REQUEST = 505;
    private static boolean active;
    private static boolean waitingSettings;
    private static int waitingFor;
    private static boolean waitingPermission;
    private static boolean dialogOpen;
    private static boolean termuxAsked;
    private static final Handler MAIN = new Handler(Looper.getMainLooper());

    private FirstRunSetup() {}

    static void offer(MainActivity a) {
        boolean canOverlay = Build.VERSION.SDK_INT < 23 || Settings.canDrawOverlays(a);
        boolean hasMic = a.checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED;
        boolean hasA11y = A11y.enabled(a);
        if (canOverlay && a.getSharedPreferences("musabai-floating", Context.MODE_PRIVATE)
                .getBoolean("enabled_by_user", true)) {
            startOverlay(a, hasMic);
        }
        if (canOverlay && hasMic && hasA11y) {
            a.getSharedPreferences("musabai-setup", Context.MODE_PRIVATE)
                    .edit().putBoolean("intro_shown", true).putBoolean("completed", true).apply();
            return;
        }
        if (a.getSharedPreferences("musabai-setup", Context.MODE_PRIVATE)
                .getBoolean("intro_shown", false)) return;
        a.getSharedPreferences("musabai-setup", Context.MODE_PRIVATE)
                .edit().putBoolean("intro_shown", true).apply();
        new AlertDialog.Builder(a)
            .setTitle("MusabAI — تهيئة ذكية")
            .setMessage("أجهّز الفقاعة العائمة والصوت وTermux والتحكم بالشاشة. أنت توافق على صلاحيات أندرويد عند طلبها؛ لا يمكن لأي تطبيق منح نفسه هذه الصلاحيات.")
            .setPositiveButton("تهيئة بضغطة واحدة", (d, w) -> start(a))
            .setNegativeButton("لاحقاً", (d, w) -> {})
            .show();
    }

    static void start(MainActivity a) {
        active = true; waitingSettings = false; waitingPermission = false;
        termuxAsked = false; dialogOpen = false;
        next(a);
    }

    private static void prompt(MainActivity a, String title, String message, int step, Runnable open) {
        dialogOpen = true;
        new AlertDialog.Builder(a).setTitle(title).setMessage(message)
          .setPositiveButton("فتح الإعداد المطلوب", (d, w) -> {
              dialogOpen = false; waitingSettings = true; waitingFor = step;
              try { open.run(); } catch (RuntimeException ex) {
                  waitingSettings = false; a.showSetupError("تعذر فتح الإعداد. تستطيع التفعيل يدوياً.");
              }
          })
          .setNegativeButton("توقف الآن", (d, w) -> { dialogOpen = false; active = false; })
          .setOnCancelListener(d -> { dialogOpen = false; active = false; })
          .show();
    }

    static void resume(MainActivity a) {
        if (!active || !waitingSettings) return;
        waitingSettings = false;
        MAIN.postDelayed(() -> {
            if (!active) return;
            if ((waitingFor == 1 && !Settings.canDrawOverlays(a)) ||
                (waitingFor == 2 && !A11y.enabled(a))) {
                // A denied permission is never treated as granted. Allow retry
                // via the explicit Setup button rather than an infinite settings loop.
                a.showSetupError("لم تكتمل إحدى الصلاحيات. اضغط تهيئة مجدداً للمحاولة.");
                active = false;
            } else next(a);
        }, 600);
    }

    static void permission(MainActivity a, int code) {
        if (!active || !waitingPermission) return;
        if (code != MIC_REQUEST && code != TERMUX_REQUEST) return;
        waitingPermission = false;
        if (code == MIC_REQUEST && a.checkSelfPermission(Manifest.permission.RECORD_AUDIO)
                != PackageManager.PERMISSION_GRANTED) {
            active = false;
            a.showSetupError("الميكروفون لم يُفعّل. يمكنك استخدام الفقاعة كتابةً وإعادة التهيئة لاحقاً.");
            startOverlay(a, false);
            return;
        }
        next(a);
    }

    private static void next(MainActivity a) {
        if (!active || dialogOpen || waitingPermission || waitingSettings) return;
        if (Build.VERSION.SDK_INT >= 23 && !Settings.canDrawOverlays(a)) {
            prompt(a, "١ — الفقاعة العائمة",
                    "اسمح بالظهور فوق التطبيقات. أندرويد يشترط اختيار هذه الصلاحية بنفسك.", 1,
                    () -> a.startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                        Uri.parse("package:" + a.getPackageName()))));
            return;
        }
        if (!A11y.enabled(a)) {
            prompt(a, "٢ — التحكم بالشاشة",
                    "فعّل خدمة NewAl Code من إمكانية الوصول. بدونها لا يستطيع MusabAI تعبئة النماذج أو الضغط في Chrome.", 2,
                    () -> a.startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
            return;
        }
        if (a.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            waitingPermission = true;
            a.requestPermissions(new String[] {Manifest.permission.RECORD_AUDIO}, MIC_REQUEST);
            return;
        }
        if (Termux.installed(a) && !Termux.allowed(a) && !termuxAsked) {
            termuxAsked = true;
            waitingPermission = true;
            a.requestPermissions(new String[] {Termux.PERMISSION}, TERMUX_REQUEST);
            return;
        }
        if (Termux.installed(a) && Termux.allowed(a)) {
            try {
                // Only start an existing installation; bootstrap and checks
                // remain with the authenticated Python Termux bridge.
                Termux.run(a, "newal-termux-preview start");
            } catch (RuntimeException ignored) {
                // Termux's allow-external-apps setting still needs user action.
            }
        }
        active = false;
        // Enable the bubble and hands-free recognition together while the app
        // is in the foreground and has while-in-use microphone permission.
        startOverlay(a, true);
        askNvidia(a, true);
    }

    static void startOverlay(MainActivity a, boolean enableSpeech) {
        if (Build.VERSION.SDK_INT >= 23 && !Settings.canDrawOverlays(a)) {
            a.showSetupError("فعّل الظهور فوق التطبيقات أولاً.");
            return;
        }
        Intent i = new Intent(a, FloatingAssistantService.class);
        i.setAction(enableSpeech ? FloatingAssistantService.ACTION_LISTEN
                : FloatingAssistantService.ACTION_START);
        try {
            if (Build.VERSION.SDK_INT >= 26) a.startForegroundService(i);
            else a.startService(i);
        } catch (RuntimeException e) {
            a.showSetupError("تعذر تشغيل الفقاعة؛ راجع صلاحيات الميكروفون والخدمات.");
        }
    }

    /** The key never traverses GitHub, clipboard, logs, WebView source or APK. */
    static void askNvidia(MainActivity a, boolean fromSetup) {
        EditText entry = new EditText(a);
        entry.setSingleLine(true);
        entry.setHint("nvapi-…");
        entry.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        entry.setLayoutParams(new ViewGroup.LayoutParams(-1, -2));
        int pad = (int) (20 * a.getResources().getDisplayMetrics().density);
        android.widget.LinearLayout box = new android.widget.LinearLayout(a);
        box.setOrientation(android.widget.LinearLayout.VERTICAL);
        box.setPadding(pad, 0, pad, 0);
        box.addView(entry);
        new AlertDialog.Builder(a)
            .setTitle("٣ — NVIDIA API")
            .setMessage("NVIDIA ستضاف إلى جانب النماذج الحالية. أدخل مفتاحاً جديداً يُحفظ مشفراً داخل الهاتف. لا تستخدم مفتاحاً مكشوفاً في المحادثات العامة.")
            .setView(box)
            .setPositiveButton("حفظ المفتاح", (d, w) -> {
                String key = entry.getText().toString().trim();
                entry.setText("");
                if (!key.isEmpty()) {
                    try {
                        ProviderSecrets.handle(a, new JSONObject().put("action", "provider_secret_set")
                                .put("provider", "nvidia").put("value", key));
                        Toast.makeText(a, "تم حفظ مفتاح NVIDIA بأمان. اختر النموذج من القائمة.", Toast.LENGTH_LONG).show();
                    } catch (Exception e) { a.showSetupError("تعذر حفظ مفتاح NVIDIA؛ جرّب من إعدادات النماذج."); }
                }
                if (fromSetup) complete(a);
            })
            .setNegativeButton("تخطي — لاحقاً", (d, w) -> { if (fromSetup) complete(a); })
            .setOnCancelListener(d -> { if (fromSetup) complete(a); })
            .show();
    }

    private static void complete(MainActivity a) {
        a.getSharedPreferences("musabai-setup", Context.MODE_PRIVATE)
            .edit().putBoolean("completed", true).apply();
        new AlertDialog.Builder(a)
            .setTitle("الفقاعة والصوت جاهزان")
            .setMessage("BusyBox وToybox يعملان تلقائياً. تأكد من اتصال Termux من صفحة الاتصالات، ووصّل GitHub هناك إذا لم يكن متصلاً. تفويض GitHub يحتاج تسجيل دخولك ولا يمكن إجراؤه سراً.")
            .setPositiveButton("افتح صفحة الاتصالات", (d, w) -> a.openConnections())
            .setNegativeButton("ابدأ استخدام MusabAI", (d, w) -> {})
            .show();
    }
}
