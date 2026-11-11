package dev.newal.code.lite;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.InputType;
import android.text.TextUtils;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.inputmethod.EditorInfo;
import android.widget.AbsListView;
import android.widget.BaseAdapter;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ListView;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Task Memory, the app: everything the assistant knows how to do on the phone and the web, sorted into categories.
 * Search it, browse a category or a source, try a request and see the procedure the assistant would use (with the
 * values it takes from the request), and run a procedure on the phone. It has its own icon in the launcher and its own
 * look; the data is the library MusabAI ships (TaskMemoryDb) and the routines it learned here (TaskMemoryLearned).
 */
public class TaskMemoryActivity extends Activity {
    // the look: deep navy, violet and cyan, white text
    static final int BG = 0xFF0B1020;
    static final int SURFACE = 0xFF141B2E;
    static final int SURFACE2 = 0xFF1C2540;
    static final int LINE = 0xFF27304D;
    static final int TEXT = 0xFFF1F4FF;
    static final int MUTED = 0xFF98A2C3;
    static final int VIOLET = 0xFF7C3AED;
    static final int INDIGO = 0xFF4338CA;
    static final int CYAN = 0xFF06B6D4;
    static final int GREEN = 0xFF22C55E;
    static final int AMBER = 0xFFF59E0B;
    static final int RED = 0xFFEF4444;

    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService work = Executors.newSingleThreadExecutor();
    private final Deque<Runnable> back = new ArrayDeque<>();
    private FrameLayout root;
    private TaskMemory memory;
    private final AtomicBoolean running = new AtomicBoolean(false);
    private final AtomicBoolean cancel = new AtomicBoolean(false);
    private String lastResult = "";

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        Window w = getWindow();
        w.setStatusBarColor(BG);
        w.setNavigationBarColor(BG);
        root = new FrameLayout(this);
        root.setBackgroundColor(BG);
        root.setLayoutDirection(View.LAYOUT_DIRECTION_RTL);
        setContentView(root);
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            v.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom());
            return insets.consumeSystemWindowInsets();
        });
        showLoading();
        work.execute(() -> {
            try {
                memory = TaskMemoryDb.memory(this);
                memory.store().categories();
                ui.post(this::showHome);
            } catch (Exception e) {
                ui.post(() -> showError("تعذّر تجهيز الذاكرة: " + e.getMessage()));
            }
        });
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!lastResult.isEmpty() && !running.get()) {
            String r = lastResult;
            lastResult = "";
            if (memory != null && back.isEmpty()) {
                showHome();                     // the numbers again (a run was counted)
            }
            dialog("نتيجة التنفيذ", r);
        }
    }

    /** What a run did, for the person (the runner's own text is for the agent, in English). */
    static String arabic(TaskRunner.Result r, TaskMemory.Proc p, List<String> values) {
        int steps = p.steps().length();
        String check = TaskRunner.fill(p.check(), values == null ? new ArrayList<>() : values, false).trim();
        if (r.done) {
            return "✓ تمّ: نُفّذت " + steps + " خطوات"
                    + (check.isEmpty() ? "." : "، وتحقّقتُ أن «" + check + "» ظاهر على الشاشة.");
        }
        if (r.ok) {
            return "⚠️ نُفّذت كل الخطوات لكن «" + check + "» غير ظاهر على الشاشة الآن؛ تأكّد بنفسك.";
        }
        if (r.stoppedAt >= 0) {
            return "⚠️ توقّف التنفيذ عند الخطوة " + (r.stoppedAt + 1) + " من " + steps
                    + ": الشاشة مختلفة عمّا يتوقعه الإجراء، فأكمل من هنا أو اطلب من المساعد.\n\n" + r.message;
        }
        if (r.message.startsWith("ACCESSIBILITY_OFF")) {
            return "⚠️ فعّل خدمة إمكانية الوصول «MusabAI: التحكم بالهاتف» من الإعدادات ثم جرّب مجددًا.";
        }
        return "⚠️ " + r.message;
    }

    @Override
    protected void onDestroy() {
        work.shutdownNow();
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        if (!back.isEmpty()) {
            back.pop().run();
        } else {
            super.onBackPressed();
        }
    }

    // ------------------------------------------------------------------------------------------------ building blocks

    int dp(float v) {
        return Math.round(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, v, getResources().getDisplayMetrics()));
    }

    TextView text(String s, float sp, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        t.setTextColor(color);
        t.setTextDirection(View.TEXT_DIRECTION_FIRST_STRONG);
        t.setLineSpacing(dp(2), 1f);
        if (bold) {
            t.setTypeface(Typeface.DEFAULT_BOLD);
        }
        return t;
    }

    GradientDrawable round(int color, float radius) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(color);
        g.setCornerRadius(dp(radius));
        return g;
    }

    GradientDrawable outline(int fill, int stroke, float radius) {
        GradientDrawable g = round(fill, radius);
        g.setStroke(dp(1), stroke);
        return g;
    }

    GradientDrawable gradient(int a, int b, float radius) {
        GradientDrawable g = new GradientDrawable(GradientDrawable.Orientation.TL_BR, new int[] {a, b});
        g.setCornerRadius(dp(radius));
        return g;
    }

    LinearLayout column() {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        return l;
    }

    LinearLayout row() {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.HORIZONTAL);
        l.setGravity(Gravity.CENTER_VERTICAL);
        return l;
    }

    LinearLayout card() {
        LinearLayout c = column();
        c.setBackground(outline(SURFACE, LINE, 18));
        c.setPadding(dp(16), dp(14), dp(16), dp(14));
        return c;
    }

    LinearLayout.LayoutParams wide(int top) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(top);
        return lp;
    }

    TextView chip(String s, int color) {
        TextView t = text(s, 12, color, true);
        t.setBackground(outline(SURFACE2, color, 50));
        t.setPadding(dp(10), dp(4), dp(10), dp(4));
        return t;
    }

    TextView button(String s, int color, View.OnClickListener click) {
        TextView b = text(s, 15, Color.WHITE, true);
        b.setGravity(Gravity.CENTER);
        b.setBackground(gradient(color, color == VIOLET ? INDIGO : color, 14));
        b.setPadding(dp(16), dp(12), dp(16), dp(12));
        b.setOnClickListener(click);
        return b;
    }

    TextView ghostButton(String s, View.OnClickListener click) {
        TextView b = text(s, 15, TEXT, true);
        b.setGravity(Gravity.CENTER);
        b.setBackground(outline(SURFACE2, LINE, 14));
        b.setPadding(dp(16), dp(12), dp(16), dp(12));
        b.setOnClickListener(click);
        return b;
    }

    EditText field(String hint) {
        EditText e = new EditText(this);
        e.setHint(hint);
        e.setHintTextColor(MUTED);
        e.setTextColor(TEXT);
        e.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        e.setBackground(outline(SURFACE2, LINE, 14));
        e.setPadding(dp(14), dp(12), dp(14), dp(12));
        e.setTextDirection(View.TEXT_DIRECTION_FIRST_STRONG);
        e.setSingleLine(true);
        return e;
    }

    static String num(int n) {
        return String.format(Locale.US, "%,d", n);
    }

    /** A label of another direction inside Arabic text (FSI...PDI): "What's on your mind?" keeps its "?" at its end. */
    static String iso(String s) {
        return "\u2068" + s + "\u2069";
    }

    /** A label in «», a blank alone (‹نص المنشور›) as it is. */
    static String quoted(String s) {
        return s.startsWith("‹") && s.endsWith("›") ? iso(s) : "«" + iso(s) + "»";
    }

    /** {1}, {2}... shown as the names of the blanks ("نص المنشور"). */
    static String named(String s, List<String> names) {
        String out = s;
        for (int i = 0; i < names.size(); i++) {
            out = out.replace("{" + (i + 1) + "}", "‹" + names.get(i) + "›");
        }
        return out;
    }

    View topBar(String title, boolean withBack) {
        LinearLayout bar = row();
        bar.setPadding(dp(16), dp(12), dp(16), dp(8));
        if (withBack) {
            TextView b = text("→", 22, TEXT, true);
            b.setPadding(dp(4), 0, dp(14), 0);
            b.setOnClickListener(v -> onBackPressed());
            bar.addView(b);
        }
        TextView t = text(title, 20, TEXT, true);
        t.setSingleLine(true);
        t.setEllipsize(TextUtils.TruncateAt.END);
        bar.addView(t, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        return bar;
    }

    void setPage(View page) {
        root.removeAllViews();
        root.addView(page, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
    }

    void showLoading() {
        LinearLayout c = column();
        c.setGravity(Gravity.CENTER);
        ProgressBar p = new ProgressBar(this);
        c.addView(p);
        TextView t = text("يجهّز ذاكرة المهام… (أول مرة فقط)", 15, MUTED, false);
        t.setPadding(0, dp(16), 0, 0);
        c.addView(t);
        setPage(c);
    }

    void showError(String message) {
        LinearLayout c = column();
        c.setGravity(Gravity.CENTER);
        c.setPadding(dp(24), dp(24), dp(24), dp(24));
        c.addView(text(message, 15, RED, false));
        setPage(c);
    }

    void dialog(String title, String message) {
        new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle(title).setMessage(message).setPositiveButton("حسنًا", null).show();
    }

    // ------------------------------------------------------------------------------------------------ home

    void showHome() {
        back.clear();
        ScrollView scroll = new ScrollView(this);
        LinearLayout page = column();
        page.setPadding(dp(16), dp(8), dp(16), dp(32));
        scroll.addView(page);

        // the hero card
        LinearLayout hero = column();
        hero.setBackground(gradient(VIOLET, INDIGO, 24));
        hero.setPadding(dp(20), dp(20), dp(20), dp(20));
        hero.addView(text("🧠 ذاكرة المهام", 26, Color.WHITE, true));
        TextView sub = text("مكتبة خطوات مصنّفة يستخدمها مساعدك ليعرف كيف ينفّذ مهامك على الهاتف والويب: "
                + "تقول له «افتح فيسبوك وانشر منشور» فيجد الخطوات هنا وينفّذها.", 14, 0xFFE9E5FF, false);
        sub.setPadding(0, dp(8), 0, dp(14));
        hero.addView(sub);
        LinearLayout stats = row();
        hero.addView(stats);
        page.addView(hero, wide(4));

        // try a request
        LinearLayout tryCard = card();
        tryCard.addView(text("جرّب أمرًا", 17, TEXT, true));
        TextView tryHint = text("اكتب طلبًا كما تقوله للمساعد، وشاهد أي إجراء سيستخدم وما القيم التي أخذها منه.", 13,
                MUTED, false);
        tryHint.setPadding(0, dp(4), 0, dp(10));
        tryCard.addView(tryHint);
        EditText ask = field("مثال: افتح فيسبوك وانشر منشور يقول صباح الخير");
        ask.setImeOptions(EditorInfo.IME_ACTION_GO);
        tryCard.addView(ask, wide(0));
        LinearLayout answer = column();
        Runnable doTry = () -> tryRequest(ask.getText().toString(), answer);
        ask.setOnEditorActionListener((v, id, ev) -> {
            if (id == EditorInfo.IME_ACTION_GO || (ev != null && ev.getKeyCode() == KeyEvent.KEYCODE_ENTER)) {
                doTry.run();
                return true;
            }
            return false;
        });
        tryCard.addView(button("اعثر على الخطوات", VIOLET, v -> doTry.run()), wide(10));
        tryCard.addView(answer, wide(0));
        page.addView(tryCard, wide(14));

        // search
        EditText search = field("🔎  ابحث في " + "كل الإجراءات (عربي أو إنجليزي)");
        search.setImeOptions(EditorInfo.IME_ACTION_SEARCH);
        search.setOnEditorActionListener((v, id, ev) -> {
            String q = search.getText().toString().trim();
            if (!q.isEmpty()) {
                showResults(q);
            }
            return true;
        });
        page.addView(search, wide(14));

        // categories
        TextView ch = text("الفئات", 18, TEXT, true);
        ch.setPadding(dp(4), dp(20), 0, dp(8));
        page.addView(ch);
        LinearLayout grid = column();
        page.addView(grid, wide(0));

        // sources
        TextView sh = text("مصادر البيانات", 18, TEXT, true);
        sh.setPadding(dp(4), dp(22), 0, dp(8));
        page.addView(sh);
        LinearLayout sources = column();
        page.addView(sources, wide(0));

        setPage(scroll);

        work.execute(() -> {
            try {
                List<TaskMemory.Category> cats = memory.store().categories();
                List<TaskMemory.Source> srcs = memory.store().sources();
                List<TaskMemory.Proc> learned = TaskMemoryLearned.all(this);
                int total = 0;
                int exec = 0;
                for (TaskMemory.Source s : srcs) {
                    total += s.n;
                    exec += s.nExec;
                }
                total += learned.size();
                exec += learned.size();
                int fTotal = total;
                int fExec = exec;
                ui.post(() -> {
                    addStat(stats, num(fTotal), "إجراء");
                    addStat(stats, num(fExec), "قابل للتنفيذ");
                    addStat(stats, String.valueOf(cats.size()), "فئة");
                    addStat(stats, num(TaskMemoryUsage.totalRuns(this)), "تشغيل");
                    fillCategories(grid, cats, learned.size());
                    fillSources(sources, srcs, learned.size());
                });
            } catch (Exception e) {
                ui.post(() -> showError(e.toString()));
            }
        });
    }

    void addStat(LinearLayout stats, String value, String label) {
        LinearLayout s = column();
        s.setGravity(Gravity.CENTER);
        s.setBackground(round(0x33FFFFFF, 14));
        s.setPadding(dp(4), dp(8), dp(4), dp(8));
        TextView v = text(value, 16, Color.WHITE, true);
        v.setGravity(Gravity.CENTER);
        s.addView(v);
        TextView l = text(label, 11, 0xFFE9E5FF, false);
        l.setGravity(Gravity.CENTER);
        s.addView(l);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        lp.setMarginStart(stats.getChildCount() == 0 ? 0 : dp(6));
        stats.addView(s, lp);
    }

    void fillCategories(LinearLayout grid, List<TaskMemory.Category> cats, int learnedCount) {
        grid.removeAllViews();
        List<Object> items = new ArrayList<>();
        if (learnedCount > 0) {
            items.add("learned");
        }
        for (TaskMemory.Category c : cats) {
            if (c.n > 0) {
                items.add(c);
            }
        }
        LinearLayout line = null;
        for (int i = 0; i < items.size(); i++) {
            if (i % 2 == 0) {
                line = row();
                grid.addView(line, wide(i == 0 ? 0 : 10));
            }
            Object it = items.get(i);
            LinearLayout cell = column();
            cell.setBackground(outline(SURFACE, LINE, 18));
            cell.setPadding(dp(14), dp(12), dp(14), dp(12));
            if (it instanceof TaskMemory.Category) {
                TaskMemory.Category c = (TaskMemory.Category) it;
                cell.addView(text(c.icon, 26, TEXT, false));
                cell.addView(text(c.nameAr, 15, TEXT, true));
                cell.addView(text(num(c.n) + " إجراء" + (c.nExec > 0 ? " · " + num(c.nExec) + " قابل للتنفيذ" : ""),
                        12, MUTED, false));
                cell.setOnClickListener(v -> showCategory(c, ""));
            } else {
                cell.addView(text("⭐", 26, TEXT, false));
                cell.addView(text("تعلّمها من استخدامك", 15, TEXT, true));
                cell.addView(text(num(learnedCount) + " مهمة", 12, MUTED, false));
                cell.setOnClickListener(v -> showLearned());
            }
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            lp.setMarginStart(i % 2 == 0 ? 0 : dp(10));
            line.addView(cell, lp);
        }
        if (items.size() % 2 == 1 && line != null) {
            View spacer = new View(this);
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, 1, 1f);
            lp.setMarginStart(dp(10));
            line.addView(spacer, lp);
        }
    }

    void fillSources(LinearLayout list, List<TaskMemory.Source> srcs, int learnedCount) {
        list.removeAllViews();
        for (TaskMemory.Source s : srcs) {
            int n = "learned".equals(s.id) ? learnedCount : s.n;
            if (n == 0 && !"learned".equals(s.id)) {
                continue;
            }
            LinearLayout c = card();
            LinearLayout head = row();
            TextView name = text(s.nameAr + "  ·  " + s.name, 15, TEXT, true);
            head.addView(name, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
            head.addView(chip(num(n), CYAN));
            c.addView(head);
            TextView about = text(s.aboutAr, 13, MUTED, false);
            about.setPadding(0, dp(6), 0, 0);
            c.addView(about);
            boolean learnedSource = "learned".equals(s.id);
            String extra = learnedSource ? "تزيد تلقائيًا بعد كل مهمة هاتف تنجح"
                    : (s.nExec > 0 ? num(s.nExec) + " قابل للتنفيذ مباشرة" : "إرشادات يخطط منها المساعد")
                    + (s.license.isEmpty() ? "" : " · رخصة " + s.license);
            TextView e = text(extra, 12, learnedSource || s.nExec > 0 ? GREEN : AMBER, false);
            e.setPadding(0, dp(6), 0, 0);
            c.addView(e);
            c.setOnClickListener(v -> {
                if ("learned".equals(s.id)) {
                    showLearned();
                } else {
                    showSource(s);
                }
            });
            list.addView(c, wide(list.getChildCount() == 0 ? 0 : 10));
        }
    }

    // ------------------------------------------------------------------------------------------------ try a request

    void tryRequest(String request, LinearLayout answer) {
        String q = QuickPhoneActions.normalizeCommand(request.trim());
        if (q.isEmpty()) {
            return;
        }
        answer.removeAllViews();
        ProgressBar p = new ProgressBar(this);
        answer.addView(p, wide(12));
        work.execute(() -> {
            try {
                long t0 = System.currentTimeMillis();
                List<TaskMemory.Match> ranked = memory.rank(q, 4);
                TaskMemory.Match best = ranked.isEmpty() ? null : ranked.get(0);
                if (best != null) {
                    best.values = memory.slotValues(best.proc, q, best.app);
                }
                long ms = System.currentTimeMillis() - t0;
                ui.post(() -> showAnswer(answer, best, ranked, ms));
            } catch (Exception e) {
                ui.post(() -> {
                    answer.removeAllViews();
                    answer.addView(text("خطأ: " + e.getMessage(), 13, RED, false), wide(10));
                });
            }
        });
    }

    void showAnswer(LinearLayout answer, TaskMemory.Match best, List<TaskMemory.Match> ranked, long ms) {
        answer.removeAllViews();
        if (best == null) {
            answer.addView(text("لا يوجد إجراء مناسب في الذاكرة؛ سيخطط المساعد الخطوات بنفسه.", 13, AMBER, false),
                    wide(10));
            return;
        }
        LinearLayout box = column();
        box.setBackground(outline(SURFACE2, best.confident ? GREEN : AMBER, 16));
        box.setPadding(dp(14), dp(12), dp(14), dp(12));
        TextView verdict = text((best.confident ? "✓ وجدتُ الإجراء المناسب" : "≈ أقرب إجراء (غير مؤكد)")
                + String.format(Locale.US, "  ·  %.0f%%  ·  %d ms", Math.min(1.0, best.score / 1.3) * 100, ms), 13,
                best.confident ? GREEN : AMBER, true);
        box.addView(verdict);
        TextView title = text(best.proc.title(), 16, TEXT, true);
        title.setPadding(0, dp(6), 0, 0);
        box.addView(title);
        if (!best.proc.title().equals(best.proc.goal)) {
            box.addView(text(best.proc.goal, 12, MUTED, false));
        }
        List<String> names = best.proc.slotNames();
        for (int i = 0; i < names.size(); i++) {
            String v = i < best.values.size() ? best.values.get(i) : "";
            TextView sv = text("• " + names.get(i) + ": " + (v.isEmpty() ? "(يسأل عنه المساعد)" : "«" + iso(v) + "»"), 13,
                    v.isEmpty() ? MUTED : CYAN, false);
            sv.setPadding(0, dp(4), 0, 0);
            box.addView(sv);
        }
        TextView steps = text(named(TaskMemory.describe(best.proc), best.proc.slotNames()), 12, MUTED, false);
        steps.setPadding(0, dp(8), 0, 0);
        steps.setTextDirection(View.TEXT_DIRECTION_LTR);
        box.addView(steps);
        LinearLayout actions = row();
        TextView open = ghostButton("التفاصيل", v -> showProcedure(best.proc, best.values));
        actions.addView(open, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        if (best.proc.exec) {
            TextView run = button("نفّذ الآن", GREEN, v -> confirmRun(best.proc, best.values));
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
            lp.setMarginStart(dp(8));
            actions.addView(run, lp);
        }
        box.addView(actions, wide(12));
        answer.addView(box, wide(12));
        if (ranked.size() > 1) {
            TextView more = text("بدائل:", 13, MUTED, true);
            more.setPadding(dp(4), dp(10), 0, dp(4));
            answer.addView(more);
            for (int i = 1; i < ranked.size(); i++) {
                TaskMemory.Match m = ranked.get(i);
                answer.addView(procRow(m.proc), wide(6));
            }
        }
    }

    // ------------------------------------------------------------------------------------------------ lists

    View procRow(TaskMemory.Proc p) {
        LinearLayout r = column();
        r.setBackground(outline(SURFACE, LINE, 14));
        r.setPadding(dp(14), dp(10), dp(14), dp(10));
        TextView t = text(p.title(), 14, TEXT, true);
        t.setMaxLines(2);
        t.setEllipsize(TextUtils.TruncateAt.END);
        r.addView(t);
        LinearLayout meta = row();
        meta.setPadding(0, dp(6), 0, 0);
        meta.addView(chip(p.exec ? "▶ قابل للتنفيذ" : "💡 إرشاد", p.exec ? GREEN : AMBER));
        TextView src = text("  " + TaskMemory.sourceName(p.src) + (p.app == null || p.app.isEmpty() ? "" : " · " + p.app)
                + (p.nsteps > 0 ? " · " + p.nsteps + " خطوات" : ""), 12, MUTED, false);
        src.setSingleLine(true);
        src.setEllipsize(TextUtils.TruncateAt.END);
        meta.addView(src, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        r.addView(meta);
        r.setOnClickListener(v -> showProcedure(p, null));
        return r;
    }

    /** A long list that loads more as it is scrolled. */
    interface Pager {
        List<TaskMemory.Proc> page(int offset, int limit) throws Exception;
    }

    final class ProcAdapter extends BaseAdapter {
        final List<TaskMemory.Proc> items = new ArrayList<>();
        final Pager pager;
        boolean loading;
        boolean end;

        ProcAdapter(Pager pager) {
            this.pager = pager;
        }

        void more() {
            if (loading || end) {
                return;
            }
            loading = true;
            int offset = items.size();
            work.execute(() -> {
                try {
                    List<TaskMemory.Proc> got = pager.page(offset, 60);
                    ui.post(() -> {
                        items.addAll(got);
                        end = got.size() < 60;
                        loading = false;
                        notifyDataSetChanged();
                    });
                } catch (Exception e) {
                    ui.post(() -> {
                        loading = false;
                        end = true;
                    });
                }
            });
        }

        @Override
        public int getCount() {
            return items.size();
        }

        @Override
        public Object getItem(int i) {
            return items.get(i);
        }

        @Override
        public long getItemId(int i) {
            return items.get(i).id;
        }

        @Override
        public View getView(int i, View convert, ViewGroup parent) {
            FrameLayout f = new FrameLayout(TaskMemoryActivity.this);
            f.setPadding(dp(16), dp(5), dp(16), dp(5));
            f.addView(procRow(items.get(i)));
            if (i > items.size() - 10) {
                more();
            }
            return f;
        }
    }

    void listPage(String title, View header, Pager pager, Runnable backTo) {
        LinearLayout page = column();
        page.addView(topBar(title, true));
        if (header != null) {
            page.addView(header);
        }
        ListView list = new ListView(this);
        list.setDivider(null);
        list.setSelector(android.R.color.transparent);
        list.setBackgroundColor(BG);
        ProcAdapter adapter = new ProcAdapter(pager);
        list.setAdapter(adapter);
        list.setOnScrollListener(new AbsListView.OnScrollListener() {
            @Override
            public void onScrollStateChanged(AbsListView v, int s) {
            }

            @Override
            public void onScroll(AbsListView v, int first, int visible, int total) {
                if (first + visible >= total - 5) {
                    adapter.more();
                }
            }
        });
        page.addView(list, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        back.push(backTo);
        setPage(page);
        adapter.more();
    }

    void showCategory(TaskMemory.Category c, String app) {
        HorizontalScrollView chips = new HorizontalScrollView(this);
        chips.setHorizontalScrollBarEnabled(false);
        LinearLayout line = row();
        line.setPadding(dp(16), 0, dp(16), dp(8));
        chips.addView(line);
        TextView all = chip("الكل · " + num(c.n), app.isEmpty() ? CYAN : MUTED);
        all.setOnClickListener(v -> {
            back.pop();
            showCategory(c, "");
        });
        line.addView(all);
        work.execute(() -> {
            try {
                List<TaskMemory.App> apps = memory.store().appsIn(c.id, 30);
                ui.post(() -> {
                    for (TaskMemory.App a : apps) {
                        TextView t = chip(a.name + " · " + num(a.n), a.name.equals(app) ? CYAN : MUTED);
                        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
                                ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
                        lp.setMarginStart(dp(6));
                        t.setOnClickListener(v -> {
                            back.pop();
                            showCategory(c, a.name);
                        });
                        line.addView(t, lp);
                    }
                });
            } catch (Exception ignored) {
                // the list still shows without the app chips
            }
        });
        listPage(c.icon + "  " + c.nameAr + (app.isEmpty() ? "" : " · " + app), chips,
                (offset, limit) -> memory.store().byCategory(c.id, app, offset, limit), this::showHome);
    }

    void showSource(TaskMemory.Source s) {
        TextView about = text(s.aboutAr, 13, MUTED, false);
        about.setPadding(dp(16), 0, dp(16), dp(8));
        listPage(s.nameAr, about, (offset, limit) -> memory.store().bySource(s.id, offset, limit), this::showHome);
    }

    void showLearned() {
        TextView about = text("مهام نفّذها المساعد بنجاح على هذا الهاتف فحفظ خطواتها ليعيدها في نداء واحد.", 13, MUTED,
                false);
        about.setPadding(dp(16), 0, dp(16), dp(8));
        listPage("⭐ تعلّمها من استخدامك", about, (offset, limit) -> {
            List<TaskMemory.Proc> all = TaskMemoryLearned.all(this);
            return offset >= all.size() ? new ArrayList<>() : all.subList(offset, Math.min(all.size(), offset + limit));
        }, this::showHome);
    }

    void showResults(String q) {
        listPage("نتائج: " + q, null, (offset, limit) -> {
            if (offset > 0) {
                return new ArrayList<>();
            }
            List<TaskMemory.Proc> out = new ArrayList<>();
            for (TaskMemory.Match m : memory.rank(q, 60)) {
                out.add(m.proc);
            }
            return out;
        }, this::showHome);
    }

    // ------------------------------------------------------------------------------------------------ a procedure

    static String icon(String a) {
        switch (a) {
            case "open_app":
                return "📱";
            case "open_url":
                return "🌐";
            case "tap":
                return "👆";
            case "long_press":
                return "✋";
            case "type":
                return "⌨️";
            case "key":
                return "⏎";
            case "scroll":
            case "swipe":
                return "↕️";
            case "wait":
                return "⏳";
            case "phone":
                return "⚙️";
            default:
                return "💡";
        }
    }

    static String verb(String a) {
        switch (a) {
            case "open_app":
                return "افتح التطبيق";
            case "open_url":
                return "افتح الرابط";
            case "tap":
                return "اضغط";
            case "long_press":
                return "اضغط مطوّلًا";
            case "type":
                return "اكتب";
            case "key":
                return "زر";
            case "scroll":
            case "swipe":
                return "مرّر";
            case "wait":
                return "انتظر";
            case "phone":
                return "إجراء الهاتف";
            default:
                return "خطوة";
        }
    }

    void showProcedure(TaskMemory.Proc p, List<String> given) {
        View current = root.getChildCount() > 0 ? root.getChildAt(0) : null;
        ScrollView scroll = new ScrollView(this);
        LinearLayout page = column();
        page.setPadding(0, 0, 0, dp(32));
        scroll.addView(page);
        page.addView(topBar("تفاصيل الإجراء", true));
        LinearLayout body = column();
        body.setPadding(dp(16), 0, dp(16), 0);
        page.addView(body);

        LinearLayout head = card();
        head.addView(text(p.title(), 19, TEXT, true));
        if (!p.title().equals(p.goal)) {
            TextView g = text(p.goal, 13, MUTED, false);
            g.setPadding(0, dp(4), 0, 0);
            head.addView(g);
        }
        LinearLayout chips = row();
        chips.setPadding(0, dp(10), 0, 0);
        chips.addView(chip(p.exec ? "▶ قابل للتنفيذ" : "💡 إرشاد للمساعد", p.exec ? GREEN : AMBER));
        TextView src = chip(TaskMemory.sourceName(p.src), CYAN);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.setMarginStart(dp(6));
        chips.addView(src, lp);
        if (p.app != null && !p.app.isEmpty()) {
            TextView app = chip(p.app, VIOLET);
            LinearLayout.LayoutParams lp2 = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT);
            lp2.setMarginStart(dp(6));
            chips.addView(app, lp2);
        }
        head.addView(chips);
        int runs = TaskMemoryUsage.runs(this, p.ref());
        TextView ref = text("المعرّف: tm:" + p.ref() + (runs > 0 ? " · شُغّل " + runs + " مرة، اكتمل "
                + TaskMemoryUsage.done(this, p.ref()) : ""), 11, MUTED, false);
        ref.setPadding(0, dp(8), 0, 0);
        ref.setTextIsSelectable(true);
        head.addView(ref);
        body.addView(head, wide(4));

        // the blanks
        List<String> names = p.slotNames();
        List<EditText> inputs = new ArrayList<>();
        if (!names.isEmpty()) {
            LinearLayout blanks = card();
            blanks.addView(text("القيم", 16, TEXT, true));
            for (int i = 0; i < names.size(); i++) {
                TextView n = text("{" + (i + 1) + "} " + names.get(i), 13, MUTED, false);
                n.setPadding(0, dp(10), 0, dp(4));
                blanks.addView(n);
                EditText e = field(names.get(i));
                e.setSingleLine(false);
                e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
                if (given != null && i < given.size()) {
                    e.setText(given.get(i));
                }
                inputs.add(e);
                blanks.addView(e, wide(0));
            }
            body.addView(blanks, wide(12));
        }

        // the steps
        LinearLayout steps = card();
        steps.addView(text("الخطوات", 16, TEXT, true));
        JSONArray arr = p.steps();
        for (int i = 0; i < arr.length(); i++) {
            JSONObject s = arr.optJSONObject(i);
            if (s == null) {
                continue;
            }
            steps.addView(stepRow(i + 1, s, names), wide(10));
        }
        body.addView(steps, wide(12));

        // what to do with it
        LinearLayout actions = column();
        if (p.exec) {
            actions.addView(button("▶  نفّذ الآن على الهاتف", GREEN, v -> {
                List<String> values = new ArrayList<>();
                for (EditText e : inputs) {
                    values.add(e.getText().toString().trim());
                }
                confirmRun(p, values);
            }), wide(0));
        }
        actions.addView(ghostButton("💬  أرسل للمساعد لينفّذها", v -> sendToAssistant(p, inputs)), wide(10));
        body.addView(actions, wide(16));

        if (current != null) {
            back.push(() -> {
                root.removeAllViews();
                root.addView(current);
            });
        } else {
            back.push(this::showHome);
        }
        setPage(scroll);
    }

    View stepRow(int n, JSONObject s, List<String> names) {
        LinearLayout r = row();
        r.setGravity(Gravity.TOP);
        TextView num = text(String.valueOf(n), 13, Color.WHITE, true);
        num.setGravity(Gravity.CENTER);
        num.setBackground(round(s.optBoolean("final") ? RED : INDIGO, 50));
        r.addView(num, new LinearLayout.LayoutParams(dp(26), dp(26)));
        LinearLayout col = column();
        col.setPadding(dp(10), 0, 0, 0);
        String a = s.optString("a");
        StringBuilder main = new StringBuilder(icon(a) + "  " + verb(a));
        JSONArray t = s.optJSONArray("t");
        if (t != null && t.length() > 0) {
            main.append(' ').append(quoted(named(t.optString(0), names)));
        } else if (s.has("name")) {
            main.append(" ").append(iso(s.optString("name")));
        } else if (s.has("text")) {
            main.append(' ').append(quoted(named(s.optString("text"), names)));
        } else if (s.has("url")) {
            main.append(" ").append(iso(named(s.optString("url"), names)));
        } else if (s.has("direction")) {
            main.append(" ").append(s.optString("direction"));
        } else if ("phone".equals(a)) {
            main.append(" ").append(s.optString("action")).append(s.has("page") ? " " + s.optString("page") : "");
        }
        if (s.optBoolean("optional")) {
            main.append("  (إن ظهر)");
        }
        if (s.optBoolean("final")) {
            main.append("  (ينشر/يرسل)");
        }
        col.addView(text(main.toString(), 14, TEXT, false));
        if (t != null && t.length() > 1) {
            StringBuilder alt = new StringBuilder("أو: ");
            for (int i = 1; i < t.length() && i < 6; i++) {
                alt.append(i > 1 ? " · " : "").append(iso(named(t.optString(i), names)));
            }
            col.addView(text(alt.toString(), 12, MUTED, false));
        }
        if (s.has("hint")) {
            col.addView(text(iso(s.optString("hint")), 12, MUTED, false));
        }
        r.addView(col, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        return r;
    }

    void sendToAssistant(TaskMemory.Proc p, List<EditText> inputs) {
        StringBuilder msg = new StringBuilder(p.title());
        if (!p.title().equals(p.goal)) {
            msg.append(" (").append(p.goal).append(")");
        }
        List<String> names = p.slotNames();
        for (int i = 0; i < inputs.size(); i++) {
            String v = inputs.get(i).getText().toString().trim();
            if (!v.isEmpty()) {
                msg.append("، ").append(i < names.size() ? names.get(i) : "{" + (i + 1) + "}").append(": ").append(v);
            }
        }
        msg.append(" — استخدم إجراء ذاكرة المهام tm:").append(p.ref());
        Intent i = new Intent(this, MainActivity.class).setAction(Intent.ACTION_SEND).setType("text/plain")
                .putExtra(Intent.EXTRA_TEXT, msg.toString()).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(i);
    }

    // ------------------------------------------------------------------------------------------------ running

    void confirmRun(TaskMemory.Proc p, List<String> values) {
        if (running.get()) {
            dialog("قيد التنفيذ", "هناك إجراء يعمل الآن.");
            return;
        }
        List<Integer> gaps = TaskRunner.missing(p, values == null ? new ArrayList<>() : values);
        if (!gaps.isEmpty()) {
            showProcedure(p, values);
            dialog("أكمل القيم", "اكتب القيم الناقصة " + gaps + " ثم اضغط نفّذ.");
            return;
        }
        if (PhoneControlService.on == null && !A11y.enabled(this)) {
            new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                    .setTitle("فعّل التحكم بالهاتف")
                    .setMessage("لتنفيذ الخطوات (الضغط والكتابة) يلزم تشغيل خدمة إمكانية الوصول «NewAl Code: التحكم "
                            + "بالهاتف» من الإعدادات.")
                    .setPositiveButton("افتح الإعدادات", (d, w) -> startActivity(
                            new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)))
                    .setNegativeButton("لاحقًا", null).show();
            return;
        }
        boolean publishes = false;
        JSONArray steps = p.steps();
        for (int i = 0; i < steps.length(); i++) {
            publishes |= steps.optJSONObject(i) != null && steps.optJSONObject(i).optBoolean("final");
        }
        String summary = p.title() + "\n\n" + TaskMemory.describe(p);
        new AlertDialog.Builder(this, android.R.style.Theme_DeviceDefault_Dialog_Alert)
                .setTitle(publishes ? "تأكيد: سينشر/يرسل" : "تنفيذ الإجراء")
                .setMessage(summary)
                .setPositiveButton("نفّذ", (d, w) -> run(p, values))
                .setNegativeButton("إلغاء", null).show();
    }

    void run(TaskMemory.Proc p, List<String> values) {
        running.set(true);
        cancel.set(false);
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("task_memory", "ذاكرة المهام",
                NotificationManager.IMPORTANCE_LOW));
        notifyStep(nm, "يبدأ: " + p.title(), true);
        new Thread(() -> {
            TaskRunner.Result r = TaskRunner.run(getApplicationContext(), p, values,
                    (i, n, what) -> notifyStep(nm, "الخطوة " + (i + 1) + " من " + n + ": " + what, true), cancel);
            TaskMemoryUsage.record(getApplicationContext(), p.ref(), r.done);
            running.set(false);
            String text = arabic(r, p, values);
            notifyStep(nm, text, false);
            lastResult = text;
            ui.post(() -> {
                if (!isFinishing() && hasWindowFocus()) {
                    lastResult = "";
                    dialog("نتيجة التنفيذ", text);
                }
            });
        }, "task-memory-run").start();
    }

    void notifyStep(NotificationManager nm, String text, boolean ongoing) {
        Notification.Builder b = new Notification.Builder(this, "task_memory")
                .setSmallIcon(R.drawable.ic_stat)
                .setContentTitle("ذاكرة المهام")
                .setContentText(text)
                .setStyle(new Notification.BigTextStyle().bigText(text))
                .setOngoing(ongoing)
                .setOnlyAlertOnce(true)
                .setAutoCancel(!ongoing);
        Intent open = new Intent(this, TaskMemoryActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        b.setContentIntent(android.app.PendingIntent.getActivity(this, 11, open,
                android.app.PendingIntent.FLAG_IMMUTABLE | android.app.PendingIntent.FLAG_UPDATE_CURRENT));
        try {
            nm.notify(4711, b.build());
        } catch (SecurityException ignored) {
            // notifications not allowed: the dialog shows the result when the person comes back
        }
    }
}
