package dev.newal.code.lite;

import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.util.Base64;
import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.HashSet;
import java.util.Set;

/** Browser OAuth with PKCE-bound HTTPS handoff. WebView and Python never receive credentials. */
final class Connectors {
    private static Connectors instance;
    static synchronized Connectors get(Context c) throws Exception {
        if (instance == null) instance = new Connectors(c.getApplicationContext());
        return instance;
    }
    private final Context ctx;
    private final ConnectorVault vault;
    private final ActivepiecesMcp activepieces;
    private final String broker;
    private final Set<String> polling = new HashSet<>();
    private JSONObject deployed = new JSONObject();
    private long catalogChecked;
    private String catalogError = "";
    private static final String[] IDS = {"github", "gitlab", "drive", "gmail", "calendar", "docs", "sheets", "notion", "figma", "notionmcp", "netlify", "miro", "huggingface", "gitlabmcp"};
    private static final String[] NAMES = {"GitHub", "GitLab API", "Google Drive", "Gmail", "Google Calendar", "Google Docs", "Google Sheets", "Notion API", "Figma", "Notion", "Netlify", "Miro", "Hugging Face", "GitLab"};

    private Connectors(Context c) throws Exception {
        ctx = c;
        vault = new ConnectorVault(c);
        activepieces = new ActivepiecesMcp(c, vault);
        JSONObject config;
        try (InputStream in = c.getAssets().open("connectors.json")) {
            config = new JSONObject(new String(read(in, 65536), StandardCharsets.UTF_8));
        }
        broker = config.optString("broker_url", "").replaceAll("/+$", "");
        if (!broker.isEmpty()) {
            URL u = new URL(broker);
            if (!"https".equals(u.getProtocol()) || u.getUserInfo() != null || !u.getPath().isEmpty() || u.getQuery() != null || u.getRef() != null)
                throw new SecurityException("Connector broker must be an HTTPS origin");
        }
    }

    private static void valid(String provider) {
        for (String id : IDS) if (id.equals(provider)) return;
        throw new IllegalArgumentException("Unknown connector");
    }

    static JSONObject handle(Context c, JSONObject a) throws Exception {
        Connectors self = get(c);
        String op = a.optString("op"), provider = a.optString("provider");
        if (op.equals("status")) return self.status();
        if (op.equals("termux_status")) return self.termuxStatus();
        if (op.equals("termux_test")) return TermuxJobs.test(c);
        if (op.equals("termux_exec")) return TermuxJobs.run(c, a.optString("command"), false);
        if (op.equals("termux_result")) return TermuxJobs.result(c, a.getString("id"));
        if (provider.equals("termux") && op.equals("disconnect")) {
            TermuxJobs.disconnect(c);
            return new JSONObject().put("ok", true);
        }
        if (provider.equals(ActivepiecesMcp.ID)) {
            if (op.equals("connect")) return self.activepieces.start(a.optString("url"));
            if (op.equals("disconnect")) return self.activepieces.disconnect();
            if (op.equals("test")) return self.activepieces.test();
            if (op.equals("oauth_complete")) return self.activepieces.complete(
                    a.optString("code"), a.optString("state"), a.optString("iss"), a.optString("error"));
            if (op.equals("mcp_tools")) return new JSONObject().put("ok", true).put("data", self.activepieces.tools());
            if (op.equals("mcp_call")) return new JSONObject().put("ok", true).put("data",
                    self.activepieces.call(a.getString("name"), a.optJSONObject("arguments")));
            throw new IllegalArgumentException("Unknown Activepieces operation");
        }
        valid(provider);
        if (op.equals("connect")) return self.connect(provider);
        if (op.equals("disconnect")) {
            // Cancels pending handoffs as well; removing credentials is immediate.
            synchronized (self) { self.vault.remove(provider); ConnectorMcp.forget(provider); }
            return new JSONObject().put("ok", true).put("revoked_locally", true)
                    .put("text", "Disconnected on this device. Revoke the application's grant in the provider's account settings to remove provider access.");
        }
        if (op.equals("test")) return self.testChecked(provider);
        if (op.equals("mcp_tools") || op.equals("mcp_call")) return self.mcp(provider, op, a);
        if (op.equals("request")) return self.api(provider, a, true);
        throw new IllegalArgumentException("Unknown connector operation");
    }

    private JSONObject status() throws Exception {
        synchronized (this) {
            if (!broker.isEmpty() && System.currentTimeMillis() - catalogChecked > 60000) {
                try {
                    deployed = http(broker + "/v1/catalog", "GET", null, "application/json", null).getJSONObject("providers");
                    catalogError = "";
                } catch (Exception e) {
                    // Keep the last known deployment during a temporary network outage.
                    catalogError = "Connector server is unavailable; retry when online";
                }
                catalogChecked = System.currentTimeMillis();
            }
        }
        JSONArray list = new JSONArray();
        for (int i = 0; i < IDS.length; i++) {
            // Prefer Notion's registered official MCP client, retaining existing REST grants.
            if ((IDS[i].equals("notion") || IDS[i].equals("gitlab")) && deployed.optBoolean(IDS[i] + "mcp") && !vault.get(IDS[i]).has("access_token")) continue;
            JSONObject rec = vault.get(IDS[i]);
            boolean configured = !broker.isEmpty() && deployed.optBoolean(IDS[i]);
            String state = rec.optString("status", configured ? "disconnected" : "not_configured");
            if (rec.has("access_token") && rec.optLong("expires_at") > 0 && rec.optLong("expires_at") < System.currentTimeMillis() && !rec.has("refresh_token"))
                state = "reauthorize";
            if (state.equals("authorizing")) resume(IDS[i]);
            list.put(new JSONObject().put("id", IDS[i]).put("name", NAMES[i]).put("status", state)
                    .put("account", rec.optString("account")).put("scopes", rec.optString("scope"))
                    .put("error", rec.optString("error", configured ? "" : catalogError.isEmpty() ? "OAuth application deployment required" : catalogError))
                    .put("configured", configured).put("has_credentials", rec.has("access_token")).put("tested_at", rec.optLong("tested_at"))
                    .put("transport", ConnectorMcp.supports(IDS[i]) ? "mcp" : "rest")
                    .put("generation", rec.optString("generation")).put("tool_count", rec.optInt("tool_count")));
        }
        list.put(activepieces.status());
        list.put(termuxStatus());
        return new JSONObject().put("ok", true).put("connectors", list).put("remote_worker", false);
    }

    // Runtime probes must never fetch the OAuth catalog or decrypt every account.
    private JSONObject termuxStatus() throws Exception {
        JSONObject termux = vault.get("termux");
        boolean usable = Termux.installed(ctx) && Termux.allowed(ctx);
        return new JSONObject().put("ok", true).put("id", "termux").put("name", "Termux").put("configured", true)
                .put("status", usable ? termux.optString("status", "disconnected") : "permission_required")
                .put("error", usable ? termux.optString("error") : "Install Termux, grant RUN_COMMAND and enable allow-external-apps in Termux");
    }

    private synchronized JSONObject connect(String provider) throws Exception {
        if (broker.isEmpty()) throw new IllegalStateException("OAuth applications are not deployed for this build");
        JSONObject old = vault.get(provider);
        if (old.optString("status").equals("authorizing") && old.optLong("expires") > System.currentTimeMillis()) {
            resume(provider);
            return new JSONObject().put("ok", true).put("status", "authorizing");
        }
        byte[] bytes = new byte[48];
        new SecureRandom().nextBytes(bytes);
        String verifier = Base64.encodeToString(bytes, Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
        String proof = Base64.encodeToString(MessageDigest.getInstance("SHA-256").digest(verifier.getBytes(StandardCharsets.UTF_8)), Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
        JSONObject started = http(broker + "/v1/start", "POST", new JSONObject().put("provider", provider).put("challenge", proof).toString(), "application/json", null);
        JSONObject rec = new JSONObject().put("status", "authorizing").put("id", started.getString("id"))
                .put("verifier", verifier).put("expires", System.currentTimeMillis() + 600000);
        vault.put(provider, rec);
        Uri uri = Uri.parse(started.getString("authorization_url"));
        if (!"https".equals(uri.getScheme()) || !authHost(provider).equals(uri.getHost())) {
            vault.remove(provider);
            throw new SecurityException("Invalid provider authorization URL");
        }
        try {
            Phone.onMain(() -> { ctx.startActivity(new Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); return true; });
        } catch (Exception e) {
            vault.remove(provider);
            throw e;
        }
        resume(provider);
        return new JSONObject().put("ok", true).put("status", "authorizing");
    }

    private static String authHost(String provider) {
        if (provider.equals("github")) return "github.com";
        if (provider.equals("gitlab") || provider.equals("gitlabmcp")) return "gitlab.com";
        if (provider.equals("notion")) return "api.notion.com";
        if (provider.equals("figma")) return "www.figma.com";
        if (provider.equals("notionmcp")) return "mcp.notion.com";
        if (provider.equals("netlify")) return "mcp.netlify.com";
        if (provider.equals("miro")) return "mcp.miro.com";
        if (provider.equals("huggingface")) return "huggingface.co";
        return "accounts.google.com";
    }

    private synchronized void resume(String provider) {
        if (!polling.add(provider)) return;
        new Thread(() -> {
            String flow = "";
            String failure = "Authorization expired; connect again";
            try {
                JSONObject rec = vault.get(provider);
                flow = rec.optString("id");
                while (rec.optString("status").equals("authorizing") && rec.optLong("expires") > System.currentTimeMillis()) {
                    JSONObject out;
                    try {
                        out = http(broker + "/v1/poll", "POST", new JSONObject().put("id", flow).put("verifier", rec.getString("verifier")).toString(), "application/json", null);
                    } catch (Exception e) {
                        if (!(e instanceof java.io.IOException) && !(e instanceof HttpFailure &&
                                (((HttpFailure) e).status == 408 || ((HttpFailure) e).status == 429 || ((HttpFailure) e).status >= 500))) {
                            failure = "Authorization session is no longer valid; connect again";
                            throw e;
                        }
                        // Opening the browser can interrupt mobile data. Keep the same flow/proof until expiry.
                        synchronized (this) {
                            JSONObject current = vault.get(provider);
                            if (!flow.equals(current.optString("id"))) return;
                            current.put("error", "Waiting for network; authorization will resume automatically");
                            vault.put(provider, current);
                        }
                        Thread.sleep(3000);
                        rec = vault.get(provider);
                        continue;
                    }
                    synchronized (this) {
                        JSONObject current = vault.get(provider);
                        if (!current.optString("id").equals(flow)) return;
                        if (out.optString("status").equals("ready")) {
                            JSONObject tokens = out.getJSONObject("tokens");
                            tokens.put("status", "testing").put("generation", flow);
                            tokens.put("expires_at", tokens.has("expires_in") ? System.currentTimeMillis() + tokens.optLong("expires_in") * 1000 : 0);
                            vault.put(provider, tokens);
                        } else if (out.optString("status").equals("failed")) {
                            failure = "Authorization was declined or failed; connect again";
                            throw new IllegalStateException(failure);
                        } else {
                            current.remove("error");
                            vault.put(provider, current);
                        }
                    }
                    if (out.optString("status").equals("ready")) { testChecked(provider); return; }
                    Thread.sleep(2000);
                    rec = vault.get(provider);
                }
                if (rec.optString("status").equals("authorizing")) throw new IllegalStateException(failure);
            } catch (Exception e) {
                try {
                    synchronized (this) {
                        JSONObject current = vault.get(provider);
                        if (flow.equals(current.optString("id")))
                            vault.put(provider, new JSONObject().put("status", "error").put("error", failure));
                    }
                } catch (Exception ignored) { }
            } finally {
                synchronized (this) {
                    polling.remove(provider);
                    // A reconnect may have arrived while the previous poller was exiting.
                    try { if (vault.get(provider).optString("status").equals("authorizing")) resume(provider); }
                    catch (Exception ignored) { }
                }
            }
        }, "connector-oauth-" + provider).start();
    }

    private static String origin(String provider) {
        switch (provider) {
            case "github": return "https://api.github.com";
            case "gitlab": return "https://gitlab.com/api/v4";
            case "docs": return "https://docs.googleapis.com";
            case "sheets": return "https://sheets.googleapis.com";
            case "notion": return "https://api.notion.com";
            case "figma": return "https://api.figma.com";
            default: return "https://www.googleapis.com";
        }
    }

    private JSONObject testChecked(String provider) throws Exception {
        JSONObject before = vault.get(provider);
        try { return test(provider); }
        catch (Exception e) {
            synchronized (this) {
                JSONObject current = vault.get(provider);
                if (current.has("access_token") && current.optString("generation").equals(before.optString("generation"))) {
                    current.put("status", "error").put("error", "Account verification failed; test again or reconnect");
                    vault.put(provider, current);
                }
            }
            throw new IllegalStateException("Account verification failed; test again or reconnect");
        }
    }

    private JSONObject test(String provider) throws Exception {
        JSONObject rec = vault.get(provider);
        if (!rec.has("access_token")) throw new IllegalStateException("Connect this account first");
        if (ConnectorMcp.supports(provider)) {
            JSONObject catalog = mcp(provider, "mcp_tools", new JSONObject()).getJSONObject("data");
            int count = catalog.getJSONArray("tools").length();
            if (count == 0) throw new IllegalStateException("Service did not provide any tools");
            synchronized (this) {
                JSONObject current = vault.get(provider);
                if (!current.has("access_token") || !current.optString("generation").equals(rec.optString("generation")))
                    throw new IllegalStateException("Connection was removed during verification");
                current.put("status", "connected").put("tool_count", count).put("tested_at", System.currentTimeMillis());
                current.remove("error"); vault.put(provider, current);
            }
            return new JSONObject().put("ok", true).put("status", "connected").put("tool_count", count);
        }
        String path;
        switch (provider) {
            case "github": path = "/user"; break;
            case "gitlab": path = "/user"; break;
            case "notion": path = "/v1/users/me"; break;
            case "figma": path = "/v1/me"; break;
            default: path = "/oauth2/v3/userinfo";
        }
        JSONObject out;
        if (java.util.Arrays.asList("drive", "gmail", "calendar", "docs", "sheets").contains(provider)) {
            if (rec.optLong("expires_at") > 0 && rec.optLong("expires_at") < System.currentTimeMillis() + 30000) {
                refresh(provider); rec = vault.get(provider);
            }
            JSONObject data = http("https://www.googleapis.com" + path, "GET", null, "application/json", rec.getString("access_token"));
            out = new JSONObject().put("data", data);
        } else out = api(provider, new JSONObject().put("method", "GET").put("path", path), true);
        JSONObject data = out.getJSONObject("data");
        if (java.util.Arrays.asList("drive", "gmail", "calendar", "docs", "sheets").contains(provider)) {
            String scope;
            switch (provider) {
                case "drive": scope = "https://www.googleapis.com/auth/drive.file"; break;
                case "gmail": scope = "https://www.googleapis.com/auth/gmail.readonly"; break;
                case "calendar": scope = "https://www.googleapis.com/auth/calendar"; break;
                case "docs": scope = "https://www.googleapis.com/auth/documents"; break;
                default: scope = "https://www.googleapis.com/auth/spreadsheets";
            }
            if (!java.util.Arrays.asList(rec.optString("scope").split(" ")).contains(scope))
                throw new IllegalStateException("Required service permission was not granted; reconnect");
            if (provider.equals("gmail") && !rec.optString("scope").contains("https://www.googleapis.com/auth/gmail.send"))
                throw new IllegalStateException("Gmail send permission was not granted; reconnect");
            if (java.util.Arrays.asList("drive", "gmail", "calendar").contains(provider)) {
                String check = provider.equals("drive") ? "/drive/v3/files?pageSize=1&fields=files(id)" : provider.equals("gmail") ? "/gmail/v1/users/me/profile" : "/calendar/v3/users/me/calendarList?maxResults=1";
                api(provider, new JSONObject().put("method", "GET").put("path", check), true);
            }
        }
        synchronized (this) {
            JSONObject current = vault.get(provider);
            if (!current.optString("generation").equals(rec.optString("generation")) || !current.has("access_token"))
                throw new IllegalStateException("Connection was removed during verification");
            current.put("status", "connected").put("account", data.optString("login", data.optString("username", data.optString("email", data.optString("name")))))
                    .put("tested_at", System.currentTimeMillis());
            current.remove("error");
            vault.put(provider, current);
        }
        return new JSONObject().put("ok", true).put("status", "connected");
    }

    private synchronized JSONObject freshCredentials(String provider) throws Exception {
        JSONObject rec = vault.get(provider);
        if (!rec.has("access_token")) throw new IllegalStateException("Account is disconnected");
        if (rec.optLong("expires_at") > 0 && rec.optLong("expires_at") < System.currentTimeMillis() + 30000) {
            refresh(provider); rec = vault.get(provider);
        }
        return rec;
    }

    private JSONObject mcp(String provider, String op, JSONObject args) throws Exception {
        if (!ConnectorMcp.supports(provider)) throw new IllegalArgumentException("Provider does not use MCP");
        JSONObject rec = freshCredentials(provider);
        try {
            JSONObject data = op.equals("mcp_tools") ? ConnectorMcp.tools(provider, rec.getString("access_token")) :
                    ConnectorMcp.call(provider, rec.getString("access_token"), args.getString("name"), args.optJSONObject("arguments"));
            return new JSONObject().put("ok", true).put("data", data);
        } catch (ConnectorMcp.Failure failure) {
            if (failure.status == 401) synchronized (this) {
                JSONObject current = vault.get(provider);
                if (current.has("access_token") && current.optString("generation").equals(rec.optString("generation"))) {
                    current.put("status", "reauthorize").put("error", "Account verification failed; test again or reconnect");
                    vault.put(provider, current);
                }
            }
            throw failure;
        }
    }

    private JSONObject api(String provider, JSONObject args, boolean retry) throws Exception {
        JSONObject rec = vault.get(provider);
        if (!rec.has("access_token")) throw new IllegalStateException("Account is disconnected");
        String path = args.optString("path"), method = args.optString("method", "GET");
        if (!path.startsWith("/") || path.startsWith("//") || path.contains("\\") || path.contains("\r") || path.contains("\n") || path.contains("#"))
            throw new SecurityException("Invalid API path");
        String route = path.split("\\?", 2)[0];
        String decoded = java.net.URLDecoder.decode(route, "UTF-8");
        if (decoded.contains("..") || decoded.contains("\\") || decoded.startsWith("//"))
            throw new SecurityException("Unsafe API path");
        boolean allowed;
        switch (provider) {
            case "github": allowed = route.equals("/user") || route.startsWith("/user/repos") || route.startsWith("/repos/"); break;
            case "gitlab": allowed = route.equals("/user") || route.equals("/projects") || route.startsWith("/projects/"); break;
            case "drive": allowed = route.startsWith("/drive/v3/") || route.startsWith("/upload/drive/v3/"); break;
            case "gmail": allowed = route.startsWith("/gmail/v1/users/me/"); break;
            case "calendar": allowed = route.startsWith("/calendar/v3/"); break;
            case "docs": allowed = route.startsWith("/v1/documents"); break;
            case "sheets": allowed = route.startsWith("/v4/spreadsheets"); break;
            case "notion": allowed = route.equals("/v1/users/me") || route.equals("/v1/search") || route.startsWith("/v1/pages") || route.startsWith("/v1/blocks"); break;
            case "figma": allowed = route.equals("/v1/me") || route.startsWith("/v1/files/"); break;
            default: allowed = false;
        }
        if (!allowed) throw new SecurityException("API route is not exposed by this connector");
        if (!java.util.Arrays.asList("GET", "POST", "PUT", "PATCH", "DELETE").contains(method))
            throw new SecurityException("Invalid API method");
        long expiry = rec.optLong("expires_at");
        if (expiry > 0 && expiry < System.currentTimeMillis() + 30000 && rec.has("refresh_token")) {
            refresh(provider); rec = vault.get(provider);
        }
        String payload = args.has("raw") ? args.getString("raw") : args.has("body") ? args.getJSONObject("body").toString() : null;
        try {
            JSONObject data = http(origin(provider) + path, method, payload, args.optString("mime", "application/json"), rec.getString("access_token"));
            return new JSONObject().put("ok", true).put("data", data);
        } catch (HttpFailure e) {
            // Only GET is automatically retried. A write might already have happened.
            if (e.status == 401 && retry && method.equals("GET") && rec.has("refresh_token")) {
                refresh(provider); return api(provider, args, false);
            }
            synchronized (this) {
                JSONObject current = vault.get(provider);
                if (current.has("access_token") && current.optString("generation").equals(rec.optString("generation"))) {
                    if (e.status == 401) current.put("status", "reauthorize");
                    current.put("error", "Service returned HTTP " + e.status);
                    vault.put(provider, current);
                }
            }
            throw new IllegalStateException("Service returned HTTP " + e.status + "; operation was not retried");
        }
    }

    private synchronized void refresh(String provider) throws Exception {
        JSONObject rec = vault.get(provider);
        if (!rec.has("refresh_token")) throw new IllegalStateException("Reconnect account");
        try {
            JSONObject tokens = http(broker + "/v1/refresh", "POST", new JSONObject().put("provider", provider).put("refresh_token", rec.getString("refresh_token")).toString(), "application/json", null);
            for (String k : new String[]{"access_token", "refresh_token", "scope", "token_type"}) if (tokens.has(k)) rec.put(k, tokens.get(k));
            rec.put("expires_at", tokens.has("expires_in") ? System.currentTimeMillis() + tokens.optLong("expires_in") * 1000 : 0);
            vault.put(provider, rec);
        } catch (Exception e) {
            rec.put("status", "reauthorize").put("error", "Token refresh failed; reconnect account");
            vault.put(provider, rec);
            throw new IllegalStateException("Token refresh failed; reconnect account");
        }
    }

    private static final class HttpFailure extends Exception {
        final int status;
        HttpFailure(int status) { this.status = status; }
    }

    private static JSONObject http(String url, String method, String payload, String mime, String token) throws Exception {
        HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
        conn.setInstanceFollowRedirects(false); // never forward credentials across redirects
        conn.setConnectTimeout(15000);
        conn.setReadTimeout(30000);
        conn.setRequestMethod(method);
        conn.setRequestProperty("Accept", "application/json");
        conn.setRequestProperty("User-Agent", "MusabAI-Connectors/1");
        if (token != null) conn.setRequestProperty("Authorization", "Bearer " + token);
        if (url.startsWith("https://api.notion.com/")) conn.setRequestProperty("Notion-Version", "2022-06-28");
        try {
            if (payload != null) {
                conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", mime);
                byte[] data = payload.getBytes(StandardCharsets.UTF_8);
                conn.setFixedLengthStreamingMode(data.length);
                try (java.io.OutputStream out = conn.getOutputStream()) { out.write(data); }
            }
            int status = conn.getResponseCode();
            if (status < 200 || status >= 300) throw new HttpFailure(status);
            String text;
            try (InputStream in = conn.getInputStream()) { text = new String(read(in, 4 << 20), StandardCharsets.UTF_8); }
            if (text.isEmpty()) return new JSONObject();
            if (text.trim().startsWith("{")) return new JSONObject(text);
            if (text.trim().startsWith("[")) return new JSONObject().put("items", new JSONArray(text));
            return new JSONObject().put("text", text);
        } finally { conn.disconnect(); }
    }

    private static byte[] read(InputStream in, int max) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        for (int n; (n = in.read(buffer)) != -1;) {
            if (out.size() + n > max) throw new IllegalStateException("Response exceeds size limit; request a smaller page or range");
            out.write(buffer, 0, n);
        }
        return out.toByteArray();
    }
}
