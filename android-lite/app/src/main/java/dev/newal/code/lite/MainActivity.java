package dev.newal.code.lite;

import android.app.Activity;
import android.content.Intent;
import android.content.res.Configuration;
import android.graphics.Color;
import android.graphics.Insets;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.speech.RecognizerIntent;
import android.text.Html;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.FrameLayout;

import java.net.HttpURLConnection;
import java.net.URL;

/** NewAl Code's interface (the same web app as on a computer) in a WebView, once the service has started it. */
public class MainActivity extends Activity {
    static final int PICK_MODEL = 7;
    static final int VOICE = 8;
    static final int FILES = 9;
    static final int SAVE_DOCUMENT = 10;
    private java.io.File saveTemp;
    private android.webkit.ValueCallback<Uri[]> files;
    private static final String HOME = "http://127.0.0.1:" + Setup.PORT + "/";
    private FrameLayout root;
    private WebView web;
    private WebBridge bridge;
    private String key;
    private final EngineReadiness readiness = new EngineReadiness();

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        root = new FrameLayout(this);
        web = new WebView(this);
        root.addView(web, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        setContentView(root);
        boolean night = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
                == Configuration.UI_MODE_NIGHT_YES;
        betweenBars();
        bars(night ? 0xff1e1e20 : Color.WHITE, night);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        web.setWebChromeClient(new WebChromeClient() {           // confirm() and alert() dialogs, and:
            /** The page's file inputs ("Attach an image"): Android's picker (a WebView has none of its own). */
            @Override
            public boolean onShowFileChooser(WebView view, android.webkit.ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (files != null) {
                    files.onReceiveValue(null);
                }
                files = callback;
                Intent pick = params.createIntent();
                pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                try {
                    startActivityForResult(Intent.createChooser(pick, "NewAl Code"), FILES);
                    return true;
                } catch (Exception e) {
                    files = null;
                    return false;
                }
            }
        });
        key = new Setup(this).key();
        bridge = new WebBridge(this, web, key);
        web.addJavascriptInterface(bridge, "NewAlPhone");
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                Uri u = request.getUrl();
                if ("newal".equals(u.getScheme()) && "original".equals(u.getHost())) {
                    new java.io.File(new Setup(MainActivity.this).home, ".newal-code/evolution/active.json").delete();
                    startAgent(true);
                    return true;
                }
                if ("newal".equals(u.getScheme()) && "retry".equals(u.getHost())) {
                    startAgent(true);
                    return true;
                }
                if ("127.0.0.1".equals(u.getHost())) {
                    return false;
                }
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));   // GitHub, Hugging Face: the browser
                } catch (Exception ignored) {
                }
                return true;
            }
        });
        page("جارٍ تشغيل NewAl Code…", "يتم تجهيز Python وبدء التطبيق. قد يستغرق التشغيل الأول حتى دقيقتين.", night);
        if (Build.VERSION.SDK_INT >= 33) {
            requestPermissions(new String[] {"android.permission.POST_NOTIFICATIONS"}, 1);
        }
        startAgent(false);
        Shared.from(this, getIntent(), this::tellShared);
        Shared.shortcut(getIntent(), this::tellShared);
    }

    /** Shared from another app while NewAl Code runs (the activity is single-task). */
    @Override
    protected void onNewIntent(Intent i) {
        super.onNewIntent(i);
        setIntent(i);
        if (i.getData() != null && "musabai".equals(i.getData().getScheme()) && "connectors".equals(i.getData().getHost())) {
            web.evaluateJavascript("window.openMusabConnectors && window.openMusabConnectors()", null);
            return;
        }
        Shared.from(this, i, this::tellShared);
        Shared.shortcut(i, this::tellShared);
    }

    private void tellShared() {
        web.evaluateJavascript("window.onPhoneShared && window.onPhoneShared()", null);
    }

    /**
     * The page between the status bar (and a camera cutout) and the navigation bar or the keyboard. Android 15 draws
     * apps under those bars, so from Android 11 on the app lays itself out that way and pads the page; Android 8 to
     * 10 stop the window at the bars, and adjustResize at the keyboard.
     */
    private void betweenBars() {
        if (Build.VERSION.SDK_INT < 30) {
            return;
        }
        getWindow().setDecorFitsSystemWindows(false);
        root.setOnApplyWindowInsetsListener((v, insets) -> {
            Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
            Insets ime = insets.getInsets(WindowInsets.Type.ime());
            v.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, ime.bottom));
            return WindowInsets.CONSUMED;
        });
        root.requestApplyInsets();
    }

    /** The status and navigation bars in the page's colour, with icons that show on it (the page tells its colour). */
    void bars(int color, boolean dark) {
        root.setBackgroundColor(color);
        web.setBackgroundColor(color);
        Window w = getWindow();
        if (Build.VERSION.SDK_INT >= 30) {
            w.setStatusBarColor(Color.TRANSPARENT);
            w.setNavigationBarColor(Color.TRANSPARENT);
            WindowInsetsController c = w.getInsetsController();
            int light = WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS
                    | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS;
            if (c != null) {
                c.setSystemBarsAppearance(dark ? 0 : light, light);
            }
        } else {
            w.setStatusBarColor(color);
            w.setNavigationBarColor(color);
            View d = w.getDecorView();
            int light = View.SYSTEM_UI_FLAG_LIGHT_STATUS_BAR | View.SYSTEM_UI_FLAG_LIGHT_NAVIGATION_BAR;
            int flags = d.getSystemUiVisibility();
            d.setSystemUiVisibility(dark ? flags & ~light : flags | light);
        }
    }

    private void startAgent(boolean retry) {
        final int request = readiness.requestStart(retry);
        boolean night = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
                == Configuration.UI_MODE_NIGHT_YES;
        if (!readiness.hasPage())
            page("جارٍ تشغيل NewAl Code…", "يتم تجهيز Python وبدء التطبيق. قد يستغرق التشغيل الأول حتى دقيقتين.", night);
        Intent service = new Intent(this, AgentService.class);
        if (retry) service.setAction(AgentService.RETRY);
        service.putExtra(AgentService.START_RESULT, new android.os.ResultReceiver(
                new android.os.Handler(android.os.Looper.getMainLooper())) {
            @Override protected void onReceiveResult(int result, Bundle data) {
                // The previous process can still answer /health until RETRY finishes stopping it.
                // Its response must not load a stale page before the service acknowledges this request.
                String failure = result == 0 ? "" : data == null ? "Engine could not start"
                        : data.getString("error", "Engine could not start");
                readiness.serviceStarted(request, failure);
            }
        });
        // Do not show an error left by a previous failed service invocation.
        AgentService.error = "";
        try {
            startForegroundService(service);
        } catch (RuntimeException e) {
            AgentService.error = String.valueOf(e);
            readiness.serviceStarted(request, AgentService.error);
        }
        waitForServer(false);
    }

    /** An explicit restart after candidate activation or rollback may replace the current page. */
    void restartEngine() { startAgent(true); }

    private void waitForServer(boolean allowRecovery) {
        final int generation = readiness.beginCheck();
        if (generation < 0) return;
        final boolean recover = allowRecovery && readiness.hasPage() && !readiness.servicePending();
        new Thread(() -> {
            long deadline = android.os.SystemClock.elapsedRealtime() + 120_000;
            while (readiness.current(generation) && android.os.SystemClock.elapsedRealtime() < deadline) {
                if (!readiness.startupError().isEmpty()) {
                    if (recover) {
                        runOnUiThread(() -> {
                            if (readiness.current(generation) && !isFinishing() && !isDestroyed())
                                startAgent(false);
                        });
                        return;
                    }
                    break;
                }
                if (readiness.canProbe(generation)) {
                    if (!recover && !AgentService.error.isEmpty()) break;
                    if (up(key)) {
                        Setup setup = new Setup(this);
                        CandidateSelection.markHealthy(setup.home, String.valueOf(BuildConfig.VERSION_CODE),
                                Setup.UPDATE_COMPAT);
                        runOnUiThread(() -> {
                            if (isFinishing() || isDestroyed()) return;
                            int action = readiness.complete(generation);
                            if (action != EngineReadiness.LOAD_PAGE) return;
                            Uri data = getIntent().getData();
                            boolean connections = data != null && "musabai".equals(data.getScheme())
                                    && "connectors".equals(data.getHost());
                            web.loadUrl(HOME + "?key=" + Uri.encode(key) + (connections ? "&connections=1" : ""));
                        });
                        return;
                    }
                    if (recover) {
                        runOnUiThread(() -> {
                            if (readiness.current(generation) && !isFinishing() && !isDestroyed())
                                startAgent(false); // ensureRunning only starts a missing process; the draft stays.
                        });
                        return;
                    }
                    if (!AgentService.error.isEmpty()) break;
                }
                try { Thread.sleep(250); }
                catch (InterruptedException e) { return; }
            }
            if (!readiness.current(generation)) return;
            String failure = readiness.startupError().isEmpty() ? AgentService.error : readiness.startupError();
            Setup setup = new Setup(this);
            if (CandidateSelection.rollbackPending(setup.home, String.valueOf(BuildConfig.VERSION_CODE),
                    Setup.UPDATE_COMPAT, failure.isEmpty() ? "Startup health timeout" : failure)) {
                runOnUiThread(() -> {
                    if (!isFinishing() && !isDestroyed()) startAgent(true);
                });
                return;
            }
            String why = failure.isEmpty()
                    ? "لم يستجب التطبيق خلال دقيقتين. اضغط إعادة المحاولة لتشغيله مجددًا."
                    : failure;
            String log = new Setup(this).logTail();
            if (!log.isEmpty()) why += "\n\n" + log;
            final String detail = why;
            boolean night = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK)
                    == Configuration.UI_MODE_NIGHT_YES;
            runOnUiThread(() -> {
                if (isFinishing() || isDestroyed() || !readiness.failed(generation)) return;
                if (readiness.hasPage()) {
                    android.widget.Toast.makeText(this, "تعذّر الاتصال بالمحرّك. ستتم إعادة المحاولة عند الرجوع للتطبيق.",
                            android.widget.Toast.LENGTH_LONG).show();
                } else {
                    page("تعذّر تشغيل NewAl Code", detail, night, true);
                }
            });
        }, "newal-wait").start();
    }

    private static boolean up(String key) {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL(HOME + "api/health").openConnection();
            c.setRequestProperty("X-NewAl-Key", key);
            c.setInstanceFollowRedirects(false);
            c.setConnectTimeout(500);
            c.setReadTimeout(500);
            return c.getResponseCode() == 200;
        } catch (Exception e) { return false; }
        finally { if (c != null) c.disconnect(); }
    }

    @Override
    protected void onDestroy() {
        readiness.pause();
        super.onDestroy();
    }

    private void page(String title, String text, boolean night) {
        page(title, text, night, false);
    }

    private void page(String title, String text, boolean night, boolean retry) {
        String html = "<html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>"
                + "<body style='font-family:sans-serif;padding:24px;margin:0;background:" + (night ? "#1e1e20" : "#fff")
                + ";color:" + (night ? "#ececf0" : "#222") + "'><h2>" + Html.escapeHtml(title)
                + "</h2><pre style='white-space:pre-wrap;color:" + (night ? "#a9a9b2" : "#555") + "'>"
                + Html.escapeHtml(text) + "</pre>"
                + (retry ? "<a href='newal://retry' style='display:inline-block;padding:14px 24px;background:#90d8b0;color:#10141d;border-radius:12px;text-decoration:none'>إعادة المحاولة</a><p><a href='newal://original'>الرجوع للمحرّك الأصلي</a></p>" : "")
                + "</body></html>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    @Override
    protected void onPause() {
        readiness.pause();
        super.onPause();
        Access.paused();
    }

    @Override
    protected void onResume() {
        super.onResume();
        Access.resumed(this);
        readiness.resume();
        waitForServer(true);
    }

    @Override
    public void onRequestPermissionsResult(int request, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(request, permissions, results);
        Access.answered(this, request);
    }

    /** The full-access walk is over: the page hears what is on now. */
    void accessDone() {
        String js = "window.onPhoneAccess && window.onPhoneAccess(" + bridge.status() + ")";
        runOnUiThread(() -> web.evaluateJavascript(js, null));
    }

    void saveDocument(String url, String name, String mime) {
        Uri uri = Uri.parse(url);
        if (!"http".equals(uri.getScheme()) || !"127.0.0.1".equals(uri.getHost()) || uri.getPort() != Setup.PORT
                || !"/api/documents/download".equals(uri.getPath())) return;
        if (saveTemp != null) return;
        java.io.File temp;
        try { temp = java.io.File.createTempFile("document-", ".tmp", getCacheDir()); saveTemp = temp; }
        catch (Exception e) { return; }
        new Thread(() -> {
            HttpURLConnection conn = null;
            try {
                conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setInstanceFollowRedirects(false); conn.setConnectTimeout(15000); conn.setReadTimeout(30000);
                conn.setRequestProperty("X-NewAl-Key", key);
                if (conn.getResponseCode() != 200) throw new java.io.IOException("Download failed");
                try (java.io.InputStream in = conn.getInputStream(); java.io.OutputStream out = new java.io.FileOutputStream(temp)) {
                    byte[] buf = new byte[8192]; int total = 0;
                    for (int n; (n = in.read(buf)) != -1;) {
                        total += n; if (total > 32 * 1024 * 1024) throw new java.io.IOException("File exceeds 32 MB");
                        out.write(buf, 0, n);
                    }
                }
                runOnUiThread(() -> {
                    if (isDestroyed()) { temp.delete(); saveTemp = null; return; }
                    Intent pick = new Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE)
                            .setType(mime).putExtra(Intent.EXTRA_TITLE, name.replaceAll("[/\\\\]", "_"));
                    try { startActivityForResult(pick, SAVE_DOCUMENT); }
                    catch (Exception e) { temp.delete(); saveTemp = null; }
                });
            } catch (Exception e) {
                temp.delete(); saveTemp = null;
                runOnUiThread(() -> android.widget.Toast.makeText(this, "تعذّر حفظ الملف: " + e.getMessage(), android.widget.Toast.LENGTH_LONG).show());
            } finally { if (conn != null) conn.disconnect(); }
        }, "document-save").start();
    }

    /** A GGUF file the user picked (the Models page's "Copy a GGUF into the app"): copied into the models folder. */
    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request == SAVE_DOCUMENT && saveTemp != null) {
            java.io.File source = saveTemp;
            if (result != RESULT_OK || data == null || data.getData() == null) { source.delete(); saveTemp = null; return; }
            Uri destination = data.getData();
            new Thread(() -> {
                try (java.io.InputStream in = new java.io.FileInputStream(source);
                     java.io.OutputStream out = getContentResolver().openOutputStream(destination)) {
                    if (out == null) throw new java.io.IOException("Cannot open destination");
                    byte[] buf = new byte[8192]; for (int n; (n = in.read(buf)) != -1;) out.write(buf, 0, n);
                    runOnUiThread(() -> android.widget.Toast.makeText(this, "تم حفظ الملف", android.widget.Toast.LENGTH_LONG).show());
                } catch (Exception e) {runOnUiThread(() -> android.widget.Toast.makeText(this, "تعذّر حفظ الملف", android.widget.Toast.LENGTH_LONG).show());}
                finally { source.delete(); saveTemp = null; }
            }, "document-copy").start();
            return;
        }
        if (request == PICK_MODEL && result == RESULT_OK && data != null && data.getData() != null) {
            ModelImport.start(this, web, data.getData());
        }
        if (request == FILES && files != null) {
            Uri[] picked = null;
            if (result == RESULT_OK && data != null) {
                if (data.getClipData() != null) {
                    picked = new Uri[data.getClipData().getItemCount()];
                    for (int k = 0; k < picked.length; k++) {
                        picked[k] = data.getClipData().getItemAt(k).getUri();
                    }
                } else if (data.getData() != null) {
                    picked = new Uri[] {data.getData()};
                }
            }
            files.onReceiveValue(picked);
            files = null;
        }
        if (request == VOICE && result == RESULT_OK && data != null) {
            java.util.ArrayList<String> said = data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);
            if (said != null && !said.isEmpty()) {
                web.evaluateJavascript("window.onPhoneVoice && window.onPhoneVoice("
                        + org.json.JSONObject.quote(said.get(0)) + ")", null);
            }
        }
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) {
            web.goBack();
        } else {
            moveTaskToBack(true);        // keep working in the background (the service stays)
        }
    }
}
