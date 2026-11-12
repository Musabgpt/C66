package dev.newal.code.lite;

import android.app.Activity;
import android.content.Context;
import android.content.MutableContextWrapper;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.GeolocationPermissions;
import android.webkit.JsPromptResult;
import android.webkit.JsResult;
import android.webkit.PermissionRequest;
import android.webkit.RenderProcessGoneDetail;
import android.webkit.SslErrorHandler;
import android.webkit.ValueCallback;
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

import org.json.JSONObject;
import org.json.JSONTokener;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.net.InetAddress;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/**
 * The assistant's own browser: one WebView that belongs to the app. It opens no window: it sits behind the
 * conversation (or lives detached when the app is not on the screen) and the assistant drives it through the
 * phone action "web": open, read, click, type, key, back, scroll, shot. The conversation shows what it is doing
 * as a small live picture; the user can bring it to the front to sign in or pass a check themselves.
 *
 * It is a browser for the open web only. It never loads this phone's own addresses or a private network (the
 * engine and the phone bridge listen on 127.0.0.1), it has no bridge into the app, and it asks for no camera,
 * microphone, location, file or download.
 */
final class InAppBrowser {
    private static final int WIDTH_DP = 412, HEIGHT_DP = 760;       // its screen while it has no window
    private static final int SHOT_WIDTH = 360;                      // the picture the conversation shows
    private static final int TEXT_MAX = 6000, ITEMS_MAX = 60;
    private static final long LOAD_MS = 25000;
    private static InAppBrowser instance;

    private final Context app;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final Map<String, Boolean> hosts = new ConcurrentHashMap<>();
    private MutableContextWrapper context;      // main thread
    private WebView web;                        // main thread
    private FrameLayout host;                   // the activity's root while there is one
    private Activity activity;
    private LinearLayout panel;                 // the browser brought to the front for the user
    private volatile String url = "", title = "", problem = "";
    private volatile boolean loading;
    private volatile int loads;
    private volatile CountDownLatch finished = new CountDownLatch(0);

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

    /** The activity's root: the browser sits behind the page in it, so it is a real, drawn view without a window. */
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
        b.hide();
        if (b.web != null && b.web.getParent() instanceof ViewGroup) {
            ((ViewGroup) b.web.getParent()).removeView(b.web);
        }
        if (b.context != null) {
            b.context.setBaseContext(b.app);
        }
        b.activity = null;
        b.host = null;
        if (b.web != null) {
            b.lay();
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

    private void place() {
        if (panel != null) {
            return;                                    // the user has it in front
        }
        if (web.getParent() instanceof ViewGroup) {
            ((ViewGroup) web.getParent()).removeView(web);
        }
        if (host != null) {
            context.setBaseContext(activity);
            host.addView(web, 0, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT));       // index 0: under the conversation
        } else {
            lay();
        }
    }

    private void ensure() {
        if (web != null) {
            return;
        }
        context = new MutableContextWrapper(activity != null ? activity : app);
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
            problem = "a link to " + request.getUrl().getScheme() + ": was not followed (only web pages open here)";
            return true;
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
        public void onPageStarted(WebView view, String u, Bitmap icon) {
            loading = true;
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
            callback.onReceiveValue(null);
            problem = "the page asked for a file: the user has to choose it (bring the browser to the front)";
            return true;
        }
    }

    // ------------------------------------------------------------------------------------- the page's side

    /** Finds an element by the number read() gave it, else by its name: exact first, then a part of it. */
    private static final String FIND = "function find(spec){"
            + "if(spec.index){var e=document.querySelector('[data-musab=\"'+spec.index+'\"]');if(e)return e;}"
            + "var want=String(spec.text||'').replace(/\\s+/g,' ').trim().toLowerCase();if(!want)return null;"
            + "var all=document.querySelectorAll(SEL),part=null;"
            + "for(var i=0;i<all.length;i++){var e=all[i];if(!vis(e))continue;var n=name(e).toLowerCase();"
            + "if(n===want)return e;if(!part&&n&&n.indexOf(want)>=0)part=e;}return part;}";
    private static final String COMMON = "var SEL='a[href],button,input,textarea,select,[role=button],[role=link],"
            + "[role=tab],[role=menuitem],[role=checkbox],[role=option],[onclick],[contenteditable=true]';"
            + "function vis(e){var r=e.getBoundingClientRect();if(r.width<2||r.height<2)return false;"
            + "var s=getComputedStyle(e);return s.visibility!=='hidden'&&s.display!=='none';}"
            + "function label(e){var t=e.getAttribute('aria-label')||'';"
            + "if(!t&&e.id){try{var l=document.querySelector('label[for=\"'+CSS.escape(e.id)+'\"]');if(l)t=l.innerText;}catch(_){}}"
            + "if(!t&&e.closest){var p=e.closest('label');if(p)t=p.innerText;}"
            + "return (t||e.getAttribute('placeholder')||e.getAttribute('name')||e.getAttribute('title')||'');}"
            + "function kind(e){var tag=e.tagName.toLowerCase(),type=(e.getAttribute('type')||'').toLowerCase();"
            + "if(tag==='a')return 'link';if(tag==='select')return 'select';"
            + "if(tag==='input'&&(type==='submit'||type==='button'||type==='reset'||type==='image'))return 'button';"
            + "if(tag==='input'&&(type==='checkbox'||type==='radio'))return type;"
            + "if(tag==='input'||tag==='textarea'||e.isContentEditable)return 'field';return 'button';}"
            + "function name(e){var k=kind(e),t=(k==='field'||k==='select'||k==='checkbox'||k==='radio')?label(e)"
            + ":((e.innerText||e.value||'').trim()||label(e));return String(t).replace(/\\s+/g,' ').trim().slice(0,80);}"
            + FIND;
    private static final String READ = "(function(){" + COMMON
            + "var old=document.querySelectorAll('[data-musab]');for(var i=0;i<old.length;i++)old[i].removeAttribute('data-musab');"
            + "var items=[],n=0,all=document.querySelectorAll(SEL);"
            + "for(var i=0;i<all.length&&items.length<" + ITEMS_MAX + ";i++){var e=all[i];"
            + "if((e.getAttribute('type')||'').toLowerCase()==='hidden'||!vis(e))continue;"
            + "var k=kind(e),t=name(e);if(!t&&k!=='field')continue;n++;e.setAttribute('data-musab',String(n));"
            + "var it={i:n,kind:k,name:t};if((k==='field'||k==='select')&&e.value)it.value=String(e.value).slice(0,60);"
            + "if((k==='checkbox'||k==='radio')&&e.checked)it.value='checked';"
            + "if(k==='link'&&e.href)it.href=String(e.href).slice(0,160);items.push(it);}"
            + "var text=(document.body?document.body.innerText:'').replace(/\\n{3,}/g,'\\n\\n').slice(0," + TEXT_MAX + ");"
            + "return JSON.stringify({url:location.href,title:document.title,text:text,items:items,"
            + "y:Math.round(window.scrollY),height:Math.round(document.documentElement.scrollHeight),view:window.innerHeight});})()";
    private static final String CLICK = "(function(spec){" + COMMON
            + "var e=find(spec);if(!e)return JSON.stringify({ok:false,error:'nothing on the page matches'});"
            + "e.scrollIntoView({block:'center'});try{e.focus();}catch(_){}"
            + "var r=e.getBoundingClientRect(),o={bubbles:true,cancelable:true,view:window,"
            + "clientX:r.left+r.width/2,clientY:r.top+r.height/2};"
            + "e.dispatchEvent(new MouseEvent('mousedown',o));e.dispatchEvent(new MouseEvent('mouseup',o));e.click();"
            + "return JSON.stringify({ok:true,name:name(e)});})";
    private static final String TYPE = "(function(spec,value,submit){" + COMMON
            + "var e=find(spec);if(!e)return JSON.stringify({ok:false,error:'nothing on the page matches'});"
            + "e.scrollIntoView({block:'center'});try{e.focus();}catch(_){}"
            + "if(e.tagName==='SELECT'){var hit=-1,w=value.trim().toLowerCase();"
            + "for(var i=0;i<e.options.length;i++){var o=e.options[i];"
            + "if(o.text.trim().toLowerCase()===w||o.value===value){hit=i;break;}if(hit<0&&o.text.toLowerCase().indexOf(w)>=0)hit=i;}"
            + "if(hit<0)return JSON.stringify({ok:false,error:'the list has no such choice'});e.selectedIndex=hit;}"
            + "else if(e.isContentEditable){e.textContent=value;}"
            + "else{var proto=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,"
            + "d=Object.getOwnPropertyDescriptor(proto,'value');if(d&&d.set)d.set.call(e,value);else e.value=value;}"
            + "e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));"
            + "if(submit)enter(e);"
            + "return JSON.stringify({ok:true,name:name(e),value:String(e.value||e.textContent||'').slice(0,80)});})";
    private static final String ENTER = "function enter(e){var k={bubbles:true,cancelable:true,key:'Enter',code:'Enter',"
            + "keyCode:13,which:13};var go=e.dispatchEvent(new KeyboardEvent('keydown',k));"
            + "e.dispatchEvent(new KeyboardEvent('keypress',k));e.dispatchEvent(new KeyboardEvent('keyup',k));"
            + "var f=e.form;if(go&&f){var b=f.querySelector('[type=submit],button:not([type=button]):not([type=reset])');"
            + "if(f.requestSubmit)f.requestSubmit();else if(b)b.click();else f.submit();}}";
    private static final String KEY = "(function(){" + ENTER
            + "var e=document.activeElement;if(!e||e===document.body)return JSON.stringify({ok:false,"
            + "error:'no field has the cursor: type into one first'});enter(e);return JSON.stringify({ok:true});})()";

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
                return act(CLICK + "(" + spec(a) + ")", true);
            case "type":
                return act(TYPE.replace("if(submit)enter(e);", ENTER + "if(submit)enter(e);") + "(" + spec(a) + ","
                        + JSONObject.quote(a.optString("value")) + "," + a.optBoolean("submit") + ")", a.optBoolean("submit"));
            case "key":
                return act(KEY, true);
            case "back":
                return back();
            case "scroll":
                js("window.scrollBy(0,Math.round(window.innerHeight*" + a.optDouble("pages", 1) + "));'ok'");
                settle(350);
                return state();
            case "shot":
                return shot(a.optInt("width", SHOT_WIDTH));
            case "close":
                Phone.onMain(() -> {
                    if (web != null) {
                        web.loadUrl("about:blank");
                        web.setVisibility(View.INVISIBLE);
                    }
                    return null;
                });
                url = "";
                title = "";
                return state();
            default:
                throw new IllegalArgumentException("unknown web op " + op
                        + ": open, read, click, type, key, back, scroll, shot or close");
        }
    }

    private static String spec(JSONObject a) throws Exception {
        JSONObject s = new JSONObject();
        s.put("index", a.optInt("index", 0));
        s.put("text", a.optString("text"));
        return s.toString();
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
            ensure();
            web.setVisibility(View.VISIBLE);
            web.loadUrl(go);
            return null;
        });
        boolean loaded = done.await(LOAD_MS, TimeUnit.MILLISECONDS);
        settle(600);
        JSONObject o = state();
        if (!loaded) {
            o.put("note", "the page is still loading after " + LOAD_MS / 1000 + " s: read it, or wait and read again");
        }
        return o;
    }

    private JSONObject read() throws Exception {
        need();
        String raw = js(READ);
        JSONObject page = raw == null || raw.isEmpty() ? new JSONObject() : new JSONObject(raw);
        JSONObject o = state();
        o.put("page", page);
        return o;
    }

    /** A click or a typed text: done in the page, then the page is given time to move on if it does. */
    private JSONObject act(String script, boolean mayNavigate) throws Exception {
        need();
        int before = loads;
        CountDownLatch done = new CountDownLatch(1);
        finished = done;
        String raw = js(script);
        JSONObject result = raw == null || raw.isEmpty() ? new JSONObject() : new JSONObject(raw);
        if (!result.optBoolean("ok")) {
            throw new IllegalStateException(result.optString("error", "the page did not answer"));
        }
        boolean moved = false;
        if (mayNavigate) {
            for (int i = 0; i < 15 && loads == before; i++) {
                Thread.sleep(100);                     // a navigation starts within a moment, or not at all
            }
            moved = loads != before;
            if (moved) {
                done.await(LOAD_MS, TimeUnit.MILLISECONDS);
            }
        }
        settle(moved ? 600 : 400);
        JSONObject o = state();
        o.put("moved", moved);
        if (result.has("name")) {
            o.put("element", result.optString("name"));
        }
        if (result.has("value")) {
            o.put("value", result.optString("value"));
        }
        return o;
    }

    private JSONObject back() throws Exception {
        need();
        CountDownLatch done = new CountDownLatch(1);
        finished = done;
        boolean went = Phone.onMain(() -> {
            if (!web.canGoBack()) {
                return false;
            }
            web.goBack();
            return true;
        });
        if (!went) {
            throw new IllegalStateException("there is no page to go back to");
        }
        done.await(LOAD_MS, TimeUnit.MILLISECONDS);
        settle(500);
        return state();
    }

    private JSONObject shot(int width) throws Exception {
        JSONObject o = state();
        byte[] jpeg = Phone.onMain(() -> {
            if (web == null || url.isEmpty()) {
                return null;
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

    /** Runs a script in the page and returns its string result (the scripts answer with JSON text). */
    private String js(String script) throws Exception {
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
        if (!done.await(15, TimeUnit.SECONDS)) {
            throw new IllegalStateException("the page did not answer in time");
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
        b.ensure();
        if (b.web.getParent() instanceof ViewGroup) {
            ((ViewGroup) b.web.getParent()).removeView(b.web);
        }
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

    /** True when the browser was in front and went back behind the conversation. */
    static boolean back(Activity a) {
        InAppBrowser b = get(a);
        if (b.panel == null) {
            return false;
        }
        b.hide();
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
        }
    }
}
