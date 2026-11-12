package dev.newal.code.lite;

import android.app.Activity;
import android.app.Presentation;
import android.content.Context;
import android.content.MutableContextWrapper;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.os.SystemClock;
import android.util.Base64;
import android.view.Gravity;
import android.view.InputDevice;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.GeolocationPermissions;
import android.webkit.JsPromptResult;
import android.webkit.JsResult;
import android.webkit.PermissionRequest;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.SslErrorHandler;
import android.webkit.ValueCallback;
import android.webkit.WebBackForwardList;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;
import org.json.JSONTokener;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.InputStream;
import java.net.InetAddress;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * The assistant's own browser: one WebView that belongs to the app. It opens no window on the phone's screen. It
 * lives on a screen of its own that nobody sees (a private virtual display), so it is a real, lit, drawn browser
 * whether the app is open, in the background or closed, and the assistant drives it through the phone action
 * "web": open, read, click, type, key, back, scroll, shot. The conversation shows what it is doing as a small live
 * picture; the user can bring it to the front to sign in or pass a check themselves.
 *
 * It is a browser for the open web only. It never loads this phone's own addresses or a private network (the
 * engine and the phone bridge listen on 127.0.0.1), it has no bridge into the app, and it asks for no camera,
 * microphone, location, file or download.
 */
final class InAppBrowser {
    private static final int WIDTH_DP = 412, HEIGHT_DP = 760;       // its own screen: a phone held upright
    private static final long REST_MS = 3 * 60 * 1000;              // unused this long, the page is paused
    private static final int SHOT_WIDTH = 360;                      // the picture the conversation shows
    private static final long LOAD_MS = 25000;
    private static final long MOVE_MS = 1200, SENT_MS = 4000;       // how long a page gets to start moving on
    private static final long USABLE_MS = 7000;                     // a page shown this long is used though it still loads
    private static InAppBrowser instance;

    private final Context app;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Map<String, Boolean> hosts = new ConcurrentHashMap<>();
    private MutableContextWrapper context;      // main thread
    private WebView web;                        // main thread
    private FrameLayout host;                   // the activity's root while there is one
    private Activity activity;
    private LinearLayout panel;                 // the browser brought to the front for the user
    private VirtualDisplay screen;              // its own screen: needs no activity and shows nothing on the phone's
    private ImageReader frames;                 // where that screen is drawn (drained: nobody watches it)
    private HandlerThread drain;
    private Presentation stage;                 // the browser's window on that screen
    private FrameLayout stageRoot;
    private boolean noStage;                    // this phone refused the screen: the browser sits behind the page
    private boolean resting;                    // paused after REST_MS without work
    private final Runnable rest = this::rest;
    private volatile String url = "", title = "", problem = "";
    private volatile boolean loading;
    private volatile int loads;
    private volatile CountDownLatch finished = new CountDownLatch(0);
    private volatile long shownAt;              // when the page now loading first showed something
    private volatile File upload;               // the file the page is about to be given
    private volatile String uploaded = "";
    private volatile String pageScript;         // assets/browser_page.js

    private InAppBrowser(Context c) {
        app = c.getApplicationContext();
    }

    static synchronized InAppBrowser get(Context c) {
        if (instance == null) {
            instance = new InAppBrowser(c);
        }
        return instance;
    }

    // ------------------------------------------------------------------------------------- where it lives

    /** The activity on the screen: the browser comes in front of its page when the user asks for it (show). */
    static void host(Activity a, FrameLayout root) {
        InAppBrowser b = get(a);
        b.activity = a;
        b.host = root;
        if (b.web != null) {
            b.place();
        }
    }

    static void unhost(Activity a) {
        InAppBrowser b = get(a);
        if (b.activity != a) {
            return;
        }
        b.activity = null;                             // before hide(): the browser must not go back into this activity
        b.host = null;
        b.hide();
        if (b.web != null) {
            b.place();
        }
    }

    private int px(int dp) {
        return Math.round(dp * app.getResources().getDisplayMetrics().density);
    }

    private void lay() {
        int w = px(WIDTH_DP), h = px(HEIGHT_DP);
        web.measure(View.MeasureSpec.makeMeasureSpec(w, View.MeasureSpec.EXACTLY),
                View.MeasureSpec.makeMeasureSpec(h, View.MeasureSpec.EXACTLY));
        web.layout(0, 0, w, h);
    }

    /**
     * The browser's own screen and its window on it. A WebView draws and runs a page fully only in a window that
     * is shown; an activity's window stops being shown when the user leaves the app. This one never does, and it
     * is on no screen the user can see. False when the phone refuses it.
     */
    private boolean stage() {
        if (noStage) {
            return false;
        }
        if (stage != null && stage.isShowing() && screen != null) {
            return true;
        }
        dropStage();
        try {
            int w = px(WIDTH_DP), h = px(HEIGHT_DP);
            drain = new HandlerThread("musabai-browser-screen");
            drain.start();
            frames = ImageReader.newInstance(w, h, PixelFormat.RGBA_8888, 2);
            frames.setOnImageAvailableListener(reader -> {
                try {
                    Image frame = reader.acquireLatestImage();
                    if (frame != null) {
                        frame.close();
                    }
                } catch (RuntimeException ignored) {
                    // the screen was just closed
                }
            }, new Handler(drain.getLooper()));
            DisplayManager displays = (DisplayManager) app.getSystemService(Context.DISPLAY_SERVICE);
            screen = displays.createVirtualDisplay("musabai-browser", w, h,
                    app.getResources().getDisplayMetrics().densityDpi, frames.getSurface(),
                    DisplayManager.VIRTUAL_DISPLAY_FLAG_OWN_CONTENT_ONLY);
            Presentation window = new Presentation(app, screen.getDisplay());
            Window inner = window.getWindow();
            if (inner != null) {                       // it never takes the keyboard or a touch from the app
                inner.addFlags(WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE);
            }
            FrameLayout root = new FrameLayout(window.getContext());
            root.setBackgroundColor(Color.WHITE);
            window.setContentView(root);
            window.setCancelable(false);
            window.show();
            stage = window;
            stageRoot = root;
            return true;
        } catch (RuntimeException | LinkageError e) {
            dropStage();
            noStage = true;
            return false;
        }
    }

    private void dropStage() {
        if (web != null && stageRoot != null && web.getParent() == stageRoot) {
            stageRoot.removeView(web);
        }
        try {
            if (stage != null) {
                stage.dismiss();
            }
        } catch (RuntimeException ignored) {
            // its screen is already gone
        }
        if (screen != null) {
            screen.release();
        }
        if (frames != null) {
            frames.close();
        }
        if (drain != null) {
            drain.quitSafely();
        }
        stage = null;
        stageRoot = null;
        screen = null;
        frames = null;
        drain = null;
    }

    /** Puts the browser where it lives: on its own screen; where the phone refuses one, behind the conversation. */
    private void place() {
        if (panel != null) {
            return;                                    // the user has it in front
        }
        FrameLayout.LayoutParams fill = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT);
        if (stage()) {
            if (web.getParent() == stageRoot) {
                return;
            }
            leave();
            context.setBaseContext(stage.getContext());
            stageRoot.addView(web, fill);
        } else if (host != null) {
            if (web.getParent() == host) {
                return;
            }
            leave();
            context.setBaseContext(activity);
            host.addView(web, 0, fill);                // index 0: under the conversation
        } else {
            leave();
            context.setBaseContext(app);
            lay();
        }
    }

    private void leave() {
        if (web.getParent() instanceof ViewGroup) {
            ((ViewGroup) web.getParent()).removeView(web);
        }
    }

    /** Before each use (main thread): the browser exists, is awake and is where it lives. */
    private void ready() {
        ensure();
        main.removeCallbacks(rest);
        main.postDelayed(rest, REST_MS);
        if (resting) {
            resting = false;
            web.onResume();
        }
        if (panel == null && (web.getParent() == null || (stage != null && !stage.isShowing()))) {
            place();
        }
        if (panel == null) {
            // Its window never takes the keyboard, and a page that is not "focused" gives its fields no focus
            // events: composers and menus wait for them. The view is told it has the focus; the phone's keyboard
            // does not follow (it serves only the window the user is in).
            try {
                if (!web.isFocused()) {
                    web.requestFocus();
                }
                web.onWindowFocusChanged(true);
            } catch (RuntimeException ignored) {
                // a WebView that will not be told: the page script gives the focus events itself
            }
        }
    }

    /** Unused for a while: the page is paused, so a site left open does not keep the phone busy. */
    private void rest() {
        if (web == null || panel != null) {
            return;
        }
        resting = true;
        web.onPause();
    }

    private void ensure() {
        if (web != null) {
            return;
        }
        context = new MutableContextWrapper(app);
        web = new WebView(context);
        web.setBackgroundColor(Color.WHITE);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        s.setSupportMultipleWindows(false);
        s.setJavaScriptCanOpenWindowsAutomatically(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setGeolocationEnabled(false);
        s.setUseWideViewPort(true);
        s.setLoadWithOverviewMode(true);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        if (Build.VERSION.SDK_INT >= 26) {
            s.setSafeBrowsingEnabled(true);
        }
        // Sites treat an embedded view as a lesser browser (some refuse it). This is a browser: it says so.
        String ua = s.getUserAgentString();
        if (ua != null) {
            s.setUserAgentString(ua.replace("; wv)", ")").replaceAll(" Version/\\d+\\.\\d+", ""));
        }
        CookieManager cookies = CookieManager.getInstance();
        cookies.setAcceptCookie(true);
        cookies.setAcceptThirdPartyCookies(web, true); // sign-ins and checks that live in a frame need them
        web.setFocusable(true);
        web.setFocusableInTouchMode(true);
        web.setWebViewClient(new Client());
        web.setWebChromeClient(new Chrome());
        web.setDownloadListener((u, agent, disposition, mime, length) -> problem = "a download was refused");
        place();
    }

    // ------------------------------------------------------------------------------------- what it may load

    /** http and https on the open web. Never this phone, never a private network, never a file or another app. */
    static boolean literalAllowed(Uri u) {
        String scheme = u.getScheme() == null ? "" : u.getScheme().toLowerCase(Locale.ROOT);
        if (scheme.equals("about") || scheme.equals("data") || scheme.equals("blob")) {
            return true;                               // made by the page itself: nothing is fetched
        }
        if (!scheme.equals("http") && !scheme.equals("https")) {
            return false;
        }
        String h = u.getHost();
        if (h == null || h.isEmpty()) {
            return false;
        }
        h = h.toLowerCase(Locale.ROOT);
        if (h.startsWith("[") && h.endsWith("]")) {
            h = h.substring(1, h.length() - 1);
        }
        if (h.equals("localhost") || h.endsWith(".localhost") || h.endsWith(".local") || h.endsWith(".internal")
                || h.equals("0.0.0.0") || h.equals("::") || h.equals("::1")) {
            return false;
        }
        if (h.contains(":")) {                         // an IPv6 literal: unique-local and link-local are private
            return !(h.startsWith("fc") || h.startsWith("fd") || h.startsWith("fe8") || h.startsWith("fe9")
                    || h.startsWith("fea") || h.startsWith("feb") || h.startsWith("::ffff:"));
        }
        String[] p = h.split("\\.");
        if (p.length == 4) {
            try {
                int a = Integer.parseInt(p[0]), b = Integer.parseInt(p[1]);
                Integer.parseInt(p[2]);
                Integer.parseInt(p[3]);
                return !(a == 127 || a == 10 || a == 0 || (a == 172 && b >= 16 && b <= 31) || (a == 192 && b == 168)
                        || (a == 169 && b == 254) || (a == 100 && b >= 64 && b <= 127));
            } catch (NumberFormatException ignored) {
                // a name that only looks like an address
            }
        }
        return true;
    }

    /** As literalAllowed, and the name must not resolve to this phone or a private network (not the main thread). */
    private boolean allowed(Uri u) {
        if (!literalAllowed(u)) {
            return false;
        }
        String scheme = u.getScheme() == null ? "" : u.getScheme().toLowerCase(Locale.ROOT);
        if (!scheme.startsWith("http")) {
            return true;
        }
        String h = u.getHost().toLowerCase(Locale.ROOT);
        Boolean known = hosts.get(h);
        if (known != null) {
            return known;
        }
        boolean ok = true;
        try {
            for (InetAddress a : InetAddress.getAllByName(h)) {
                if (a.isLoopbackAddress() || a.isSiteLocalAddress() || a.isLinkLocalAddress() || a.isAnyLocalAddress()) {
                    ok = false;
                }
            }
        } catch (Exception ignored) {
            // it does not resolve: the load fails on its own
        }
        if (hosts.size() > 500) {
            hosts.clear();
        }
        hosts.put(h, ok);
        return ok;
    }

    private final class Client extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            if (literalAllowed(request.getUrl())) {
                return false;
            }
            String scheme = String.valueOf(request.getUrl().getScheme()).toLowerCase(Locale.ROOT);
            problem = scheme.equals("http") || scheme.equals("https")
                    ? "a link to this phone or a private network was not followed: only the open web opens here"
                    : "a link that opens another app (" + scheme + ":) was not followed";
            return true;
        }

        @Override
        public void doUpdateVisitedHistory(WebView view, String u, boolean isReload) {
            if (u != null && !u.isEmpty()) {
                url = u;                               // also when a page changes its address without loading
            }
        }

        @Override
        public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
            if (allowed(request.getUrl())) {
                return null;
            }
            return new WebResourceResponse("text/plain", "utf-8", 403, "Blocked", new HashMap<String, String>(),
                    new ByteArrayInputStream(new byte[0]));
        }

        @Override
        public void onPageCommitVisible(WebView view, String u) {
            shownAt = SystemClock.uptimeMillis();
        }

        @Override
        public void onPageStarted(WebView view, String u, Bitmap icon) {
            loading = true;
            shownAt = 0;
            loads++;
            url = u == null ? "" : u;
        }

        @Override
        public void onPageFinished(WebView view, String u) {
            loading = false;
            url = view.getUrl() == null ? "" : view.getUrl();
            title = view.getTitle() == null ? "" : view.getTitle();
            finished.countDown();
        }

        @Override
        public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame()) {
                problem = "the page did not load: " + error.getDescription();
            }
        }

        @Override
        public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
            problem = "the site's certificate is not valid: the page was not loaded";
            handler.cancel();
        }

        @Override
        public boolean onRenderProcessGone(WebView view, RenderProcessGoneDetail detail) {
            if (view.getParent() instanceof ViewGroup) {
                ((ViewGroup) view.getParent()).removeView(view);
            }
            view.destroy();
            web = null;                                // made again at the next action
            loading = false;
            problem = "the page crashed the browser: it was closed";
            finished.countDown();
            return true;
        }
    }

    private final class Chrome extends WebChromeClient {
        @Override
        public void onReceivedTitle(WebView view, String t) {
            title = t == null ? "" : t;
        }

        @Override
        public void onPermissionRequest(PermissionRequest request) {
            request.deny();
        }

        @Override
        public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) {
            callback.invoke(origin, false, false);
        }

        @Override
        public boolean onJsAlert(WebView view, String u, String message, JsResult result) {
            result.confirm();
            return true;
        }

        @Override
        public boolean onJsConfirm(WebView view, String u, String message, JsResult result) {
            result.cancel();
            return true;
        }

        @Override
        public boolean onJsPrompt(WebView view, String u, String message, String value, JsPromptResult result) {
            result.cancel();
            return true;
        }

        @Override
        public boolean onJsBeforeUnload(WebView view, String u, String message, JsResult result) {
            result.confirm();
            return true;
        }

        @Override
        public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
            File chosen = upload;
            upload = null;
            if (chosen != null && chosen.isFile()) {
                callback.onReceiveValue(new Uri[]{Uri.fromFile(chosen)});
                uploaded = chosen.getName();
                return true;
            }
            callback.onReceiveValue(null);
            problem = "the page asked for a file: give one with op=upload, or the user chooses it (browser in front)";
            return true;
        }
    }

    // ------------------------------------------------------------------------------------- the page's side

    /** One op of the page script (assets/browser_page.js) as a script to run in the page. */
    private String page(String op, JSONObject args) throws Exception {
        String lib = pageScript;
        if (lib == null) {
            try (InputStream in = app.getAssets().open("browser_page.js")) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buf = new byte[8192];
                for (int n; (n = in.read(buf)) > 0; ) {
                    out.write(buf, 0, n);
                }
                lib = new String(out.toByteArray(), StandardCharsets.UTF_8);
            }
            pageScript = lib;
        }
        String json = (args == null ? "{}" : args.toString()).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029");
        return "(function(){" + lib + "\nreturn JSON.stringify(run(" + JSONObject.quote(op) + "," + json + "));})()";
    }

    /** Runs one op of the page script and gives its answer; {} from a page that is leaving. */
    private JSONObject ask(String op, JSONObject args) throws Exception {
        String raw = js(page(op, args), 15000);
        return raw == null || raw.isEmpty() ? new JSONObject() : new JSONObject(raw);
    }

    /** The same, for an answer that must be ok. */
    private JSONObject must(String op, JSONObject args) throws Exception {
        JSONObject r = ask(op, args);
        if (!r.optBoolean("ok")) {
            throw new IllegalStateException(r.optString("error", "the page did not answer"));
        }
        return r;
    }

    // ------------------------------------------------------------------------------------- actions

    /** One action from the assistant ("web"). Never on the main thread: it waits for pages. */
    JSONObject run(JSONObject a) throws Exception {
        String op = a.optString("op", "state");
        problem = "";
        switch (op) {
            case "state":
                return state();
            case "open":
                return open(a.optString("url"));
            case "read":
                return read();
            case "click":
                return click(a);
            case "type":
                return type(a);
            case "fill":
                return fill(a);
            case "key":
                return key(a.optString("key", "Enter"));
            case "back":
                return back();
            case "scroll":
                return scroll(a);
            case "find":
                return find(a.optString("text"));
            case "wait":
                return waitFor(a.optString("text"), a.optInt("seconds", 8));
            case "dismiss":
                return dismiss();
            case "upload":
                return upload(a);
            case "shot":
                return shot(a.optInt("width", SHOT_WIDTH));
            case "close":
                Phone.onMain(() -> {
                    hide();
                    main.removeCallbacks(rest);
                    if (web != null) {
                        web.loadUrl("about:blank");
                    }
                    dropStage();                       // its screen comes back with the next page
                    return null;
                });
                url = "";
                title = "";
                return state();
            default:
                throw new IllegalArgumentException("unknown web op " + op + ": open, read, click, type, fill, key, "
                        + "back, scroll, find, wait, dismiss, upload, shot or close");
        }
    }

    /** What an action names: the number read gave an element, or its name. */
    private static JSONObject spec(JSONObject a) throws Exception {
        JSONObject s = new JSONObject();
        s.put("index", a.optInt("index", 0));
        s.put("text", a.optString("text"));
        return s;
    }

    private static JSONObject named(String target) throws Exception {
        String t = target == null ? "" : target.trim();
        String digits = t.replace("[", "").replace("]", "").replace("#", "").trim();
        JSONObject s = new JSONObject();
        boolean number = !digits.isEmpty() && digits.length() <= 3 && digits.chars().allMatch(Character::isDigit);
        s.put("index", number ? Integer.parseInt(digits) : 0);
        s.put("text", number ? "" : t);
        return s;
    }

    private JSONObject state() throws Exception {
        JSONObject o = Phone.ok(url.isEmpty() || url.equals("about:blank") ? "no page is open" : title + " — " + url);
        o.put("url", url.equals("about:blank") ? "" : url);
        o.put("title", title);
        o.put("loading", loading);
        o.put("front", panel != null);
        if (!problem.isEmpty()) {
            o.put("note", problem);
        }
        return o;
    }

    private JSONObject open(String address) throws Exception {
        String target = address == null ? "" : address.trim();
        if (target.isEmpty()) {
            throw new IllegalArgumentException("open needs a url");
        }
        if (!target.contains("://")) {
            target = "https://" + target;
        }
        if (!allowed(Uri.parse(target))) {
            throw new IllegalArgumentException("this address does not open in the assistant's browser: only pages on "
                    + "the open web do (not this phone, a private network, a file or another app)");
        }
        final String go = target;
        CountDownLatch done = new CountDownLatch(1);
        finished = done;
        Phone.onMain(() -> {
            ready();
            web.loadUrl(go);
            return null;
        });
        boolean loaded = arrive(done);
        settle(500);
        String accepted = clearWay();
        JSONObject o = state();
        if (!loaded) {
            o.put("note", "the page is still loading: it can be read and used; wait and read again for what is missing");
        }
        if (!accepted.isEmpty()) {
            o.put("accepted", accepted);
        }
        return o;
    }

    /**
     * Waits for the page that is loading: until it has finished, or has been showing for a while. Heavy sites never
     * quite finish (ads, trackers), and what the user sees is there long before.
     */
    private boolean arrive(CountDownLatch done) throws InterruptedException {
        long start = SystemClock.uptimeMillis();
        while (SystemClock.uptimeMillis() - start < LOAD_MS) {
            if (done.await(250, TimeUnit.MILLISECONDS)) {
                return true;
            }
            long shown = shownAt;
            if (shown > 0 && SystemClock.uptimeMillis() - shown > USABLE_MS) {
                return false;
            }
        }
        return false;
    }

    /** A page that has just opened: its cookie notice is accepted, so nothing covers it. "" when it had none. */
    private String clearWay() {
        try {
            String accepted = ask("clear", null).optString("accepted");
            if (!accepted.isEmpty()) {
                settle(450);
            }
            return accepted;
        } catch (Exception e) {
            return "";
        }
    }

    private JSONObject read() throws Exception {
        need();
        wake();
        JSONObject page = ask("read", null);
        JSONObject o = state();
        o.put("page", page);
        return o;
    }

    /**
     * A click is a real touch on the browser where the element is, so the page gets what a finger gives it (mobile
     * sites answer touches, not clicks made by a script, and only a real touch may open a file chooser or a new
     * page). Where the touch cannot be given or does not arrive, the page clicks the element itself.
     */
    private JSONObject click(JSONObject a) throws Exception {
        need();
        wake();
        JSONObject target = spec(a);
        must("point", target);
        settle(120);                                   // the page settles where it scrolled to, then it is measured
        JSONObject where = must("point", target);
        String was = url;
        int before = loads;
        CountDownLatch done = new CountDownLatch(1);
        finished = done;
        boolean touched = where.has("fx") && touch(where.optDouble("fx"), where.optDouble("fy"), before);
        if (!touched) {
            JSONObject pressed = ask("press", target);
            if (pressed.has("ok") && !pressed.optBoolean("ok")) {
                throw new IllegalStateException(pressed.optString("error", "the page did not answer"));
            }
        }
        JSONObject o = after(where, was, before, done, where.optBoolean("nav") ? SENT_MS : MOVE_MS);
        o.put("touch", touched);
        return o;
    }

    /** A real touch at this share of the browser's width and height. True when the page felt it. */
    private boolean touch(double fx, double fy, int before) throws Exception {
        boolean sent = Phone.onMain(() -> {
            if (web == null || web.getWidth() <= 0 || web.getHeight() <= 0) {
                return false;
            }
            final float x = (float) (fx * web.getWidth()), y = (float) (fy * web.getHeight());
            final long down = SystemClock.uptimeMillis();
            finger(MotionEvent.ACTION_DOWN, down, down, x, y);
            main.postDelayed(() -> finger(MotionEvent.ACTION_UP, down, SystemClock.uptimeMillis(), x, y), 60);
            return true;
        });
        if (!sent) {
            return false;
        }
        settle(280);
        if (loads != before) {
            return true;                               // the page is already moving on
        }
        String raw = js(page("touched", null), 2500);
        if (raw == null || raw.isEmpty()) {
            return true;                               // no answer: the page left
        }
        return new JSONObject(raw).optBoolean("tapped", true);
    }

    private void finger(int action, long down, long now, float x, float y) {
        if (web == null) {
            return;
        }
        MotionEvent.PointerProperties p = new MotionEvent.PointerProperties();
        p.id = 0;
        p.toolType = MotionEvent.TOOL_TYPE_FINGER;
        MotionEvent.PointerCoords c = new MotionEvent.PointerCoords();
        c.x = x;
        c.y = y;
        c.pressure = 1f;
        c.size = 1f;
        MotionEvent e = MotionEvent.obtain(down, now, action, 1, new MotionEvent.PointerProperties[]{p},
                new MotionEvent.PointerCoords[]{c}, 0, 0, 1f, 1f, 0, 0, InputDevice.SOURCE_TOUCHSCREEN, 0);
        try {
            web.dispatchTouchEvent(e);
        } catch (RuntimeException ignored) {
            // the page clicks instead (touch() sees that nothing arrived)
        } finally {
            e.recycle();
        }
    }

    /** After an action: the page is given time to move on if it does, and where it is now is said. */
    private JSONObject after(JSONObject result, String was, int before, CountDownLatch done, long waitMs) throws Exception {
        for (long waited = 0; waited < waitMs && loads == before && url.equals(was); waited += 100) {
            Thread.sleep(100);                         // a page that moves on starts within a moment, or not at all
        }
        boolean loadsAnew = loads != before, moved = loadsAnew || !url.equals(was);
        String accepted = "";
        if (loadsAnew) {
            arrive(done);
        }
        if (moved) {
            settle(500);
            accepted = clearWay();
        } else {
            settle(waitMs > 0 ? 300 : 100);
        }
        JSONObject o = state();
        o.put("moved", moved);
        for (String k : new String[]{"name", "value", "covered", "by"}) {
            if (result.has(k)) {
                o.put(k.equals("name") ? "element" : k, result.optString(k));
            }
        }
        if (!accepted.isEmpty()) {
            o.put("accepted", accepted);
        }
        return o;
    }

    private JSONObject type(JSONObject a) throws Exception {
        need();
        wake();
        boolean submit = a.optBoolean("submit");
        String was = url;
        int before = loads;
        CountDownLatch done = new CountDownLatch(1);
        finished = done;
        JSONObject r = typeInto(spec(a), a.optString("value"), submit);
        return after(r, was, before, done, submit ? SENT_MS : 0);
    }

    /** Text into one field. A rich editor takes it in three moments (browser_page.js: compose, typed, check). */
    private JSONObject typeInto(JSONObject target, String value, boolean submit) throws Exception {
        JSONObject args = new JSONObject(target.toString());
        args.put("value", value);
        args.put("submit", submit);
        JSONObject r = must("type", args);
        if ("selected".equals(r.optString("wait"))) {
            settle(90);                                // the editor follows the selection a beat later
            must("typed", new JSONObject());
            settle(180);                               // and shows what it took a beat after that
            r = must("typed", new JSONObject().put("check", true));
            if (submit) {
                ask("key", new JSONObject().put("key", "Enter"));
            }
        }
        return r;
    }

    /** Several fields in one action: [{target, value}]. Each answers for itself; one that fails does not stop the rest. */
    private JSONObject fill(JSONObject a) throws Exception {
        need();
        wake();
        JSONArray fields = a.optJSONArray("fields");
        if (fields == null || fields.length() == 0) {
            throw new IllegalArgumentException("fill needs fields: [{\"target\": a name or number, \"value\": the text}]");
        }
        JSONArray lines = new JSONArray();
        int failed = 0;
        for (int i = 0; i < fields.length() && i < 40; i++) {
            JSONObject f = fields.optJSONObject(i);
            if (f == null) {
                continue;
            }
            JSONObject line = new JSONObject().put("target", f.optString("target"));
            try {
                JSONObject r = typeInto(named(f.optString("target")), f.optString("value"), false);
                line.put("ok", true).put("name", r.optString("name")).put("value", r.optString("value"));
            } catch (IllegalStateException e) {
                failed++;
                line.put("ok", false).put("error", String.valueOf(e.getMessage()));
            }
            lines.put(line);
        }
        JSONObject o = state();
        o.put("fields", lines);
        o.put("failed", failed);
        return o;
    }

    private JSONObject key(String which) throws Exception {
        need();
        wake();
        String was = url;
        int before = loads;
        CountDownLatch done = new CountDownLatch(1);
        finished = done;
        JSONObject r = must("key", new JSONObject().put("key", which));
        return after(r, was, before, done, r.optBoolean("nav") ? SENT_MS : MOVE_MS);
    }

    private JSONObject scroll(JSONObject a) throws Exception {
        need();
        wake();
        Object to = a.has("to") ? a.opt("to") : (Object) a.optDouble("pages", 1);
        JSONObject r = ask("scroll", new JSONObject().put("to", String.valueOf(to)));
        settle(350);                                   // pages load more as they are scrolled
        JSONObject o = state();
        for (String k : new String[]{"y", "height", "view"}) {
            if (r.has(k)) {
                o.put(k, r.optInt(k));
            }
        }
        return o;
    }

    private JSONObject find(String text) throws Exception {
        need();
        wake();
        JSONObject r = must("find", new JSONObject().put("text", text));
        JSONObject o = state();
        o.put("count", r.optInt("count"));
        o.put("places", r.optJSONArray("places"));
        return o;
    }

    /** Waits until the page says the text (or, with no text, until it has stopped loading), up to the seconds given. */
    private JSONObject waitFor(String text, int seconds) throws Exception {
        need();
        wake();
        long end = SystemClock.uptimeMillis() + Math.max(1, Math.min(seconds, 20)) * 1000L;
        boolean there = false;
        while (SystemClock.uptimeMillis() < end) {
            if (text == null || text.trim().isEmpty()) {
                there = !loading;
            } else {
                there = ask("has", new JSONObject().put("text", text)).optBoolean("there");
            }
            if (there) {
                break;
            }
            Thread.sleep(400);
        }
        JSONObject o = state();
        o.put("there", there);
        return o;
    }

    private JSONObject dismiss() throws Exception {
        need();
        wake();
        JSONObject r = must("dismiss", null);
        settle(450);
        JSONObject o = state();
        o.put("did", r.optString("did"));
        return o;
    }

    /** Gives a file to a file field: the field is touched, and the chooser the page then opens is answered with it. */
    private JSONObject upload(JSONObject a) throws Exception {
        need();
        wake();
        File file = new File(a.optString("path"));
        if (!file.isFile() || !file.canRead()) {
            throw new IllegalArgumentException("upload needs the path of a file that exists");
        }
        JSONObject target = spec(a);
        must("point", target);
        settle(120);
        JSONObject where = must("point", target);
        if (!where.has("fx")) {
            throw new IllegalStateException("the file field cannot be touched where it is: scroll to it first");
        }
        uploaded = "";
        upload = file;
        try {
            touch(where.optDouble("fx"), where.optDouble("fy"), loads);
            for (int i = 0; i < 20 && uploaded.isEmpty(); i++) {
                Thread.sleep(100);
            }
        } finally {
            upload = null;
        }
        if (uploaded.isEmpty()) {
            throw new IllegalStateException("the page did not ask for a file there: it is not a file field, or it opens "
                    + "one only after another click");
        }
        settle(400);
        JSONObject o = state();
        o.put("element", where.optString("name"));
        o.put("value", uploaded);
        return o;
    }

    private JSONObject back() throws Exception {
        need();
        CountDownLatch done = new CountDownLatch(1);
        finished = done;
        boolean went = Phone.onMain(() -> {
            ready();
            return stepBack();
        });
        if (!went) {
            throw new IllegalStateException("there is no page to go back to");
        }
        arrive(done);
        settle(500);
        return state();
    }

    /**
     * One page back. canGoBack() skips every page that was left without a touch, and the assistant's touches are
     * not always counted: the history list itself says whether there is a page behind this one.
     */
    private boolean stepBack() {
        WebBackForwardList history = web.copyBackForwardList();
        if (history.getCurrentIndex() <= 0) {
            return false;
        }
        web.goBackOrForward(-1);
        return true;
    }

    private void wake() throws Exception {
        Phone.onMain(() -> {
            ready();
            return null;
        });
    }

    private JSONObject shot(int width) throws Exception {
        JSONObject o = state();
        byte[] jpeg = Phone.onMain(() -> {
            if (web == null || url.isEmpty()) {
                return null;
            }
            if (!resting) {
                ready();                               // a picture of a page at work keeps it awake
            }
            int w = web.getWidth(), h = web.getHeight();
            if (w <= 0 || h <= 0) {
                lay();
                w = web.getWidth();
                h = web.getHeight();
            }
            Bitmap full = Bitmap.createBitmap(w, h, Bitmap.Config.RGB_565);
            Canvas canvas = new Canvas(full);
            canvas.drawColor(Color.WHITE);
            canvas.translate(-web.getScrollX(), -web.getScrollY());
            web.draw(canvas);
            int tw = Math.max(120, Math.min(width, w)), th = Math.max(1, Math.round(h * (tw / (float) w)));
            Bitmap small = tw == w ? full : Bitmap.createScaledBitmap(full, tw, th, true);
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            small.compress(Bitmap.CompressFormat.JPEG, 60, out);
            if (small != full) {
                small.recycle();
            }
            full.recycle();
            return out.toByteArray();
        });
        if (jpeg != null) {
            o.put("jpeg", Base64.encodeToString(jpeg, Base64.NO_WRAP));
        }
        return o;
    }

    private void need() {
        if (web == null || url.isEmpty() || url.equals("about:blank")) {
            throw new IllegalStateException("no page is open: open one first");
        }
    }

    private static void settle(long ms) throws InterruptedException {
        Thread.sleep(ms);
    }

    /**
     * Runs a script in the page and returns its string result (the scripts answer with JSON text); "" when the
     * page gave none. A page that does not answer within the time: an error for a long wait, "" for a short one
     * (a page that is leaving never answers).
     */
    private String js(String script, long timeoutMs) throws Exception {
        CountDownLatch done = new CountDownLatch(1);
        AtomicReference<String> out = new AtomicReference<>("");
        main.post(() -> {
            if (web == null) {
                done.countDown();
                return;
            }
            web.evaluateJavascript(script, value -> {
                try {
                    Object parsed = value == null ? null : new JSONTokener(value).nextValue();
                    out.set(parsed instanceof String ? (String) parsed : "");
                } catch (Exception e) {
                    out.set("");
                }
                done.countDown();
            });
        });
        if (!done.await(timeoutMs, TimeUnit.MILLISECONDS)) {
            if (timeoutMs >= 10000) {
                throw new IllegalStateException("the page did not answer in time");
            }
            return "";
        }
        return out.get();
    }

    // ------------------------------------------------------------------------------------- in front, for the user

    /** The user takes over (a sign-in, a check): the same browser, in front of the conversation, until Done. */
    static void show(Activity a) {
        InAppBrowser b = get(a);
        if (b.host == null || b.panel != null) {
            return;
        }
        b.ready();
        b.leave();
        b.context.setBaseContext(a);
        LinearLayout box = new LinearLayout(a);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setBackgroundColor(Color.WHITE);
        box.setFitsSystemWindows(true);
        LinearLayout bar = new LinearLayout(a);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setBackgroundColor(0xff0e8a6f);
        int pad = b.px(10);
        bar.setPadding(pad, pad / 2, pad, pad / 2);
        TextView where = new TextView(a);
        where.setTextColor(Color.WHITE);
        where.setSingleLine(true);
        where.setText(b.title.isEmpty() ? b.url : b.title);
        bar.addView(where, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f));
        Button done = new Button(a);
        done.setText(Locale.getDefault().getLanguage().equals("ar") ? "تم" : "Done");
        done.setOnClickListener(v -> b.hide());
        bar.addView(done, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        box.addView(bar, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        b.web.setVisibility(View.VISIBLE);
        box.addView(b.web, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        b.host.addView(box, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        b.panel = box;
    }

    /** The phone's back key while the browser is in front: one page back, and from its first page, out of the way. */
    static boolean back(Activity a) {
        InAppBrowser b = get(a);
        if (b.panel == null) {
            return false;
        }
        if (b.web == null || !b.stepBack()) {
            b.hide();
        }
        return true;
    }

    private void hide() {
        if (panel == null) {
            return;
        }
        LinearLayout box = panel;
        panel = null;
        if (web != null && web.getParent() == box) {
            box.removeView(web);
        }
        if (box.getParent() instanceof ViewGroup) {
            ((ViewGroup) box.getParent()).removeView(box);
        }
        if (web != null) {
            place();
            main.removeCallbacks(rest);                // given back: it rests again when nobody uses it
            main.postDelayed(rest, REST_MS);
        }
        if (activity instanceof MainActivity) {
            ((MainActivity) activity).browserReturned();
        }
    }
}
