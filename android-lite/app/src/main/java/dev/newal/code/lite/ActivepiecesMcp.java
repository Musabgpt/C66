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
import java.net.URI;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

/**
 * Activepieces Community Edition MCP connector.
 *
 * OAuth access/refresh tokens, DCR client IDs and PKCE state live only in ConnectorVault
 * (AES-GCM backed by Android Keystore). Python and the WebView see status/tool schemas,
 * never the credentials. The browser returns only a short-lived authorization code to the
 * loopback callback served by MusabAI.
 */
final class ActivepiecesMcp {
    static final String ID = "activepieces";
    static final String NAME = "Activepieces Automation Hub";
    static final String CALLBACK = "http://127.0.0.1:" + Setup.PORT + "/api/mcp-oauth/callback";
    private static final int MAX_BYTES = 4 << 20;
    private static final String RECORD = "activepieces";
    private static final long CLOCK_SKEW_MS = 30_000L;

    private final Context ctx;
    private final ConnectorVault vault;

    ActivepiecesMcp(Context ctx, ConnectorVault vault) {
        this.ctx = ctx.getApplicationContext();
        this.vault = vault;
    }

    synchronized JSONObject status() throws Exception {
        JSONObject rec = vault.get(RECORD);
        String state = rec.optString("status", "disconnected");
        if (rec.has("access_token") && rec.optLong("expires_at") > 0
                && rec.optLong("expires_at") < System.currentTimeMillis() && !rec.has("refresh_token")) {
            state = "reauthorize";
        }
        return new JSONObject()
                .put("id", ID)
                .put("name", NAME)
                .put("status", state)
                .put("configured", true)
                .put("has_credentials", rec.has("access_token"))
                .put("tested_at", rec.optLong("tested_at"))
                .put("transport", "mcp")
                .put("generation", rec.optString("generation"))
                .put("tool_count", rec.optInt("tool_count"))
                .put("server_url", rec.optString("endpoint"))
                .put("account", rec.optString("account"))
                .put("error", rec.optString("error"));
    }

    synchronized JSONObject start(String value) throws Exception {
        String endpoint = normalizeEndpoint(value);
        JSONObject old = vault.get(RECORD);
        if (old.optString("status").equals("authorizing")
                && endpoint.equals(old.optString("endpoint"))
                && old.optLong("expires") > System.currentTimeMillis()) {
            open(old.getString("authorization_url"));
            return new JSONObject().put("ok", true).put("status", "authorizing")
                    .put("authorization_url", old.getString("authorization_url"));
        }

        OAuthMetadata metadata = discover(endpoint);
        if (!metadata.scopes.contains("mcp")) {
            throw new IllegalStateException("Activepieces MCP OAuth metadata does not advertise the mcp scope");
        }
        if (!metadata.codeChallengeMethods.contains("S256")) {
            throw new IllegalStateException("Activepieces MCP OAuth server does not support PKCE S256");
        }

        JSONObject registration = postJson(metadata.registrationEndpoint, new JSONObject()
                .put("redirect_uris", new JSONArray().put(CALLBACK))
                .put("client_name", "MusabAI Android")
                .put("grant_types", new JSONArray().put("authorization_code").put("refresh_token"))
                .put("response_types", new JSONArray().put("code"))
                .put("token_endpoint_auth_method", "none"));
        String clientId = registration.optString("client_id");
        if (clientId.isEmpty()) throw new IllegalStateException("OAuth registration did not return a client_id");

        String verifier = randomUrlSafe(48);
        String challenge = Base64.encodeToString(
                MessageDigest.getInstance("SHA-256").digest(verifier.getBytes(StandardCharsets.UTF_8)),
                Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
        String state = randomUrlSafe(24);
        String authorizationUrl = metadata.authorizationEndpoint + "?" + form(new String[][]{
                {"response_type", "code"},
                {"client_id", clientId},
                {"redirect_uri", CALLBACK},
                {"code_challenge", challenge},
                {"code_challenge_method", "S256"},
                {"state", state},
                {"scope", "mcp"},
                {"resource", endpoint},
        });

        JSONObject rec = new JSONObject()
                .put("status", "authorizing")
                .put("endpoint", endpoint)
                .put("issuer", metadata.issuer)
                .put("authorization_endpoint", metadata.authorizationEndpoint)
                .put("token_endpoint", metadata.tokenEndpoint)
                .put("registration_endpoint", metadata.registrationEndpoint)
                .put("client_id", clientId)
                .put("verifier", verifier)
                .put("state", state)
                .put("generation", state)
                .put("authorization_url", authorizationUrl)
                .put("expires", System.currentTimeMillis() + 10 * 60_000L);
        vault.put(RECORD, rec);
        ConnectorMcp.forgetRemote(ID);
        open(authorizationUrl);
        return new JSONObject().put("ok", true).put("status", "authorizing")
                .put("authorization_url", authorizationUrl);
    }

    synchronized JSONObject complete(String code, String state, String issuer, String error) throws Exception {
        JSONObject rec = vault.get(RECORD);
        if (!rec.optString("status").equals("authorizing"))
            throw new IllegalStateException("No Activepieces authorization is pending");
        if (rec.optLong("expires") < System.currentTimeMillis()) {
            fail(rec, "Authorization expired; connect again");
            throw new IllegalStateException("Authorization expired; connect again");
        }
        if (!constantTime(rec.optString("state"), state)) {
            throw new SecurityException("OAuth state mismatch");
        }
        if (error != null && !error.isEmpty()) {
            fail(rec, "Authorization was declined or failed; connect again");
            throw new IllegalStateException("Authorization was declined or failed");
        }
        if (code == null || code.isEmpty() || code.length() > 4096)
            throw new IllegalArgumentException("OAuth callback is missing its authorization code");
        if (issuer != null && !issuer.isEmpty() && !sameUrl(rec.getString("issuer"), issuer))
            throw new SecurityException("OAuth authorization-server issuer mismatch");

        // Mark the code claimed before doing any network operation. A duplicate callback can never redeem it twice.
        rec.put("status", "exchanging");
        vault.put(RECORD, rec);
        try {
            JSONObject tokens = postForm(rec.getString("token_endpoint"), new String[][]{
                    {"grant_type", "authorization_code"},
                    {"code", code},
                    {"redirect_uri", CALLBACK},
                    {"client_id", rec.getString("client_id")},
                    {"code_verifier", rec.getString("verifier")},
                    {"resource", rec.getString("endpoint")},
            });
            saveTokens(rec, tokens, false);
            JSONObject tested = test();
            return new JSONObject().put("ok", true).put("status", "connected")
                    .put("tools", tested.optInt("tool_count"));
        } catch (Exception e) {
            JSONObject current = vault.get(RECORD);
            if (current.optString("generation").equals(rec.optString("generation"))) {
                fail(current, "Activepieces authorization failed; connect again");
            }
            throw e;
        }
    }

    synchronized JSONObject disconnect() throws Exception {
        vault.remove(RECORD);
        ConnectorMcp.forgetRemote(ID);
        return new JSONObject().put("ok", true).put("revoked_locally", true)
                .put("text", "Activepieces disconnected on this device. Revoke the grant in Activepieces to invalidate it remotely.");
    }

    synchronized JSONObject test() throws Exception {
        JSONObject rec = usableToken();
        JSONObject catalog;
        try {
            catalog = ConnectorMcp.toolsRemote(ID, rec.getString("endpoint"), rec.getString("access_token"));
        } catch (ConnectorMcp.Failure failure) {
            if (failure.status == 401) {
                if (rec.has("refresh_token")) {
                    refresh(rec);
                    rec = usableToken();
                    catalog = ConnectorMcp.toolsRemote(ID, rec.getString("endpoint"), rec.getString("access_token"));
                } else {
                    rec.put("status", "reauthorize").put("error", "Authorization expired; connect again");
                    vault.put(RECORD, rec);
                    throw failure;
                }
            } else throw failure;
        }
        int count = catalog.getJSONArray("tools").length();
        if (count == 0) throw new IllegalStateException("Activepieces MCP server did not provide any tools");
        JSONObject current = vault.get(RECORD);
        if (!current.optString("generation").equals(rec.optString("generation")))
            throw new IllegalStateException("Connection changed during verification");
        current.put("status", "connected")
                .put("tool_count", count)
                .put("tested_at", System.currentTimeMillis())
                .put("account", hostLabel(current.optString("endpoint")));
        current.remove("error");
        current.remove("authorization_url");
        current.remove("verifier");
        current.remove("state");
        current.remove("expires");
        vault.put(RECORD, current);
        return new JSONObject().put("ok", true).put("status", "connected")
                .put("tool_count", count).put("server", catalog.optString("server"))
                .put("protocol", catalog.optString("protocol"));
    }

    synchronized JSONObject tools() throws Exception {
        JSONObject rec = usableToken();
        try {
            return ConnectorMcp.toolsRemote(ID, rec.getString("endpoint"), rec.getString("access_token"));
        } catch (ConnectorMcp.Failure failure) {
            if (failure.status != 401 || !rec.has("refresh_token")) throw failure;
            refresh(rec);
            rec = usableToken();
            return ConnectorMcp.toolsRemote(ID, rec.getString("endpoint"), rec.getString("access_token"));
        }
    }

    synchronized JSONObject call(String name, JSONObject arguments) throws Exception {
        JSONObject rec = usableToken();
        try {
            return ConnectorMcp.callRemote(ID, rec.getString("endpoint"), rec.getString("access_token"), name, arguments);
        } catch (ConnectorMcp.Failure failure) {
            // A tool call may be a write. Never replay it after a 401 because the remote server
            // could have performed the operation before emitting an error.
            if (failure.status == 401) {
                rec.put("status", "reauthorize").put("error", "Authorization expired; reconnect before retrying the tool");
                vault.put(RECORD, rec);
            }
            throw failure;
        }
    }

    private JSONObject usableToken() throws Exception {
        JSONObject rec = vault.get(RECORD);
        if (!rec.has("access_token")) throw new IllegalStateException("Connect Activepieces first");
        long expiry = rec.optLong("expires_at");
        if (expiry > 0 && expiry < System.currentTimeMillis() + CLOCK_SKEW_MS) {
            if (!rec.has("refresh_token")) {
                rec.put("status", "reauthorize").put("error", "Authorization expired; connect again");
                vault.put(RECORD, rec);
                throw new IllegalStateException("Authorization expired; connect again");
            }
            refresh(rec);
            rec = vault.get(RECORD);
        }
        return rec;
    }

    private void refresh(JSONObject rec) throws Exception {
        String oldRefresh = rec.optString("refresh_token");
        if (oldRefresh.isEmpty()) throw new IllegalStateException("Reconnect Activepieces");
        JSONObject tokens = postForm(rec.getString("token_endpoint"), new String[][]{
                {"grant_type", "refresh_token"},
                {"refresh_token", oldRefresh},
                {"client_id", rec.getString("client_id")},
                {"resource", rec.getString("endpoint")},
        });
        // RFC 6749 allows refresh responses to omit refresh_token. Preserve the old one.
        if (!tokens.has("refresh_token")) tokens.put("refresh_token", oldRefresh);
        saveTokens(rec, tokens, true);
        ConnectorMcp.forgetRemote(ID);
    }

    private void saveTokens(JSONObject rec, JSONObject tokens, boolean refresh) throws Exception {
        String access = tokens.optString("access_token");
        if (access.isEmpty()) throw new IllegalStateException("OAuth token endpoint did not return an access token");
        rec.put("access_token", access);
        if (tokens.has("refresh_token") && !tokens.optString("refresh_token").isEmpty())
            rec.put("refresh_token", tokens.getString("refresh_token"));
        rec.put("token_type", tokens.optString("token_type", "Bearer"));
        rec.put("scope", tokens.optString("scope", rec.optString("scope", "mcp")));
        rec.put("expires_at", tokens.has("expires_in")
                ? System.currentTimeMillis() + Math.max(1, tokens.optLong("expires_in")) * 1000L : 0L);
        if (!refresh) rec.put("status", "testing");
        vault.put(RECORD, rec);
    }

    private void fail(JSONObject rec, String message) throws Exception {
        JSONObject failed = new JSONObject()
                .put("status", "error")
                .put("endpoint", rec.optString("endpoint"))
                .put("error", message)
                .put("generation", rec.optString("generation"));
        vault.put(RECORD, failed);
        ConnectorMcp.forgetRemote(ID);
    }

    private void open(String value) throws Exception {
        Uri uri = Uri.parse(value);
        if (!"https".equalsIgnoreCase(uri.getScheme()) && !isLoopback(uri))
            throw new SecurityException("Authorization URL must use HTTPS");
        Phone.onMain(() -> {
            ctx.startActivity(new Intent(Intent.ACTION_VIEW, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
            return true;
        });
    }

    private static OAuthMetadata discover(String endpoint) throws Exception {
        URL resource = new URL(endpoint);
        String origin = resource.getProtocol() + "://" + resource.getAuthority();
        LinkedHashSet<String> candidates = new LinkedHashSet<>();

        String challenged = challengeMetadata(endpoint);
        if (challenged != null) candidates.add(challenged);

        String path = resource.getPath();
        if (path != null && !path.isEmpty() && !"/".equals(path))
            candidates.add(origin + "/.well-known/oauth-protected-resource" + path);
        candidates.add(origin + "/.well-known/oauth-protected-resource/mcp");
        candidates.add(origin + "/.well-known/oauth-protected-resource");

        JSONObject protectedMetadata = firstJson(candidates);
        String declaredResource = protectedMetadata.optString("resource");
        if (!declaredResource.isEmpty() && !sameUrl(endpoint, declaredResource))
            throw new SecurityException("OAuth protected-resource metadata describes a different MCP endpoint");
        JSONArray servers = protectedMetadata.optJSONArray("authorization_servers");
        if (servers == null || servers.length() == 0)
            throw new IllegalStateException("MCP server did not advertise an OAuth authorization server");
        String issuer = normalizeIssuer(servers.getString(0));

        URL issuerUrl = new URL(issuer);
        String issuerOrigin = issuerUrl.getProtocol() + "://" + issuerUrl.getAuthority();
        String issuerPath = issuerUrl.getPath();
        LinkedHashSet<String> authCandidates = new LinkedHashSet<>();
        authCandidates.add(issuer + "/.well-known/oauth-authorization-server");
        if (issuerPath != null && !issuerPath.isEmpty() && !"/".equals(issuerPath))
            authCandidates.add(issuerOrigin + "/.well-known/oauth-authorization-server" + issuerPath);
        authCandidates.add(issuerOrigin + "/.well-known/oauth-authorization-server");

        JSONObject auth = firstJson(authCandidates);
        if (!sameUrl(issuer, auth.optString("issuer")))
            throw new SecurityException("OAuth authorization-server issuer metadata mismatch");

        String authorize = secureEndpoint(auth.getString("authorization_endpoint"), issuer);
        String token = secureEndpoint(auth.getString("token_endpoint"), issuer);
        String register = secureEndpoint(auth.getString("registration_endpoint"), issuer);

        Set<String> scopes = strings(auth.optJSONArray("scopes_supported"));
        scopes.addAll(strings(protectedMetadata.optJSONArray("scopes_supported")));
        Set<String> challengeMethods = strings(auth.optJSONArray("code_challenge_methods_supported"));
        return new OAuthMetadata(issuer, authorize, token, register, scopes, challengeMethods);
    }

    private static String challengeMetadata(String endpoint) {
        HttpURLConnection conn = null;
        try {
            conn = (HttpURLConnection) new URL(endpoint).openConnection();
            conn.setInstanceFollowRedirects(false);
            conn.setConnectTimeout(10_000);
            conn.setReadTimeout(10_000);
            conn.setRequestMethod("POST");
            conn.setDoOutput(true);
            conn.setRequestProperty("Accept", "application/json, text/event-stream");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setRequestProperty("MCP-Protocol-Version", "2025-06-18");
            byte[] body = "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"initialize\",\"params\":{\"protocolVersion\":\"2025-06-18\",\"capabilities\":{},\"clientInfo\":{\"name\":\"MusabAI\",\"version\":\"1\"}}}".getBytes(StandardCharsets.UTF_8);
            conn.setFixedLengthStreamingMode(body.length);
            try (java.io.OutputStream out = conn.getOutputStream()) { out.write(body); }
            conn.getResponseCode();
            String header = conn.getHeaderField("WWW-Authenticate");
            return resourceMetadataFromChallenge(header, endpoint);
        } catch (Exception ignored) {
            return null;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    static String resourceMetadataFromChallenge(String header, String endpoint) throws Exception {
        if (header == null || header.length() > 8192) return null;
        java.util.regex.Matcher quoted = java.util.regex.Pattern.compile(
                "(?i)(?:^|[,\\s])resource_metadata=\"([^\"]+)\"").matcher(header);
        String value = null;
        if (quoted.find()) value = quoted.group(1);
        if (value == null) {
            java.util.regex.Matcher plain = java.util.regex.Pattern.compile(
                    "(?i)(?:^|[,\\s])resource_metadata=([^,\\s]+)").matcher(header);
            if (plain.find()) value = plain.group(1);
        }
        if (value == null) return null;
        URL candidate = new URL(value);
        URL server = new URL(endpoint);
        if (!candidate.getProtocol().equalsIgnoreCase(server.getProtocol())
                || !candidate.getHost().equalsIgnoreCase(server.getHost())
                || effectivePort(candidate) != effectivePort(server))
            throw new SecurityException("OAuth resource metadata must stay on the MCP server origin");
        if (!secure(candidate)) throw new SecurityException("OAuth resource metadata URL is not secure");
        return candidate.toString();
    }

    private static JSONObject firstJson(LinkedHashSet<String> urls) throws Exception {
        Exception last = null;
        for (String url : urls) {
            try { return getJson(url); }
            catch (Exception e) { last = e; }
        }
        throw new IllegalStateException("Could not discover MCP OAuth metadata" + (last == null ? "" : ": " + last.getMessage()));
    }

    private static JSONObject getJson(String value) throws Exception {
        HttpURLConnection conn = open(value, "GET", null);
        try {
            int status = conn.getResponseCode();
            if (status != 200) throw new IllegalStateException("OAuth metadata returned HTTP " + status);
            return readJson(conn);
        } finally { conn.disconnect(); }
    }

    private static JSONObject postJson(String value, JSONObject body) throws Exception {
        byte[] data = body.toString().getBytes(StandardCharsets.UTF_8);
        HttpURLConnection conn = open(value, "POST", "application/json");
        try {
            conn.setDoOutput(true);
            conn.setFixedLengthStreamingMode(data.length);
            try (java.io.OutputStream out = conn.getOutputStream()) { out.write(data); }
            int status = conn.getResponseCode();
            if (status != 200 && status != 201)
                throw new IllegalStateException("OAuth client registration returned HTTP " + status);
            return readJson(conn);
        } finally { conn.disconnect(); }
    }

    private static JSONObject postForm(String value, String[][] pairs) throws Exception {
        byte[] data = form(pairs).getBytes(StandardCharsets.UTF_8);
        HttpURLConnection conn = open(value, "POST", "application/x-www-form-urlencoded");
        try {
            conn.setDoOutput(true);
            conn.setFixedLengthStreamingMode(data.length);
            try (java.io.OutputStream out = conn.getOutputStream()) { out.write(data); }
            int status = conn.getResponseCode();
            if (status != 200)
                throw new IllegalStateException("OAuth token endpoint returned HTTP " + status);
            return readJson(conn);
        } finally { conn.disconnect(); }
    }

    private static HttpURLConnection open(String value, String method, String contentType) throws Exception {
        URL url = new URL(value);
        if (!secure(url)) throw new SecurityException("OAuth endpoint must use HTTPS (HTTP is allowed only on loopback)");
        HttpURLConnection conn = (HttpURLConnection) url.openConnection();
        conn.setInstanceFollowRedirects(false);
        conn.setConnectTimeout(15_000);
        conn.setReadTimeout(30_000);
        conn.setRequestMethod(method);
        conn.setRequestProperty("Accept", "application/json");
        conn.setRequestProperty("User-Agent", "MusabAI-Activepieces/1");
        if (contentType != null) conn.setRequestProperty("Content-Type", contentType);
        return conn;
    }

    private static JSONObject readJson(HttpURLConnection conn) throws Exception {
        String type = conn.getContentType();
        if (type != null && !type.toLowerCase(Locale.ROOT).contains("json"))
            throw new IllegalStateException("OAuth endpoint did not return JSON");
        try (InputStream in = conn.getInputStream()) {
            byte[] raw = read(in, MAX_BYTES);
            return new JSONObject(new String(raw, StandardCharsets.UTF_8));
        }
    }

    private static byte[] read(InputStream in, int max) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        for (int n; (n = in.read(buffer)) != -1;) {
            if (out.size() + n > max) throw new IllegalStateException("OAuth response exceeds size limit");
            out.write(buffer, 0, n);
        }
        return out.toByteArray();
    }

    private static String form(String[][] pairs) throws Exception {
        StringBuilder out = new StringBuilder();
        for (String[] pair : pairs) {
            if (pair[1] == null || pair[1].isEmpty()) continue;
            if (out.length() > 0) out.append('&');
            out.append(URLEncoder.encode(pair[0], "UTF-8")).append('=')
                    .append(URLEncoder.encode(pair[1], "UTF-8"));
        }
        return out.toString();
    }

    private static Set<String> strings(JSONArray array) {
        Set<String> out = new LinkedHashSet<>();
        if (array == null) return out;
        for (int i = 0; i < array.length(); i++) {
            String value = array.optString(i);
            if (!value.isEmpty()) out.add(value);
        }
        return out;
    }

    private static String normalizeEndpoint(String value) throws Exception {
        if (value == null) throw new IllegalArgumentException("Enter the Activepieces MCP Server URL");
        value = value.trim();
        if (value.length() > 2048) throw new IllegalArgumentException("Activepieces MCP URL is too long");
        URL url = new URL(value);
        if (!secure(url) || url.getUserInfo() != null || url.getQuery() != null || url.getRef() != null)
            throw new IllegalArgumentException("Use the HTTPS MCP Server URL from Activepieces Settings");
        String path = url.getPath() == null ? "" : url.getPath().replaceAll("/+$", "");
        if (!(path.equals("/mcp") || path.endsWith("/mcp") || path.equals("/mcp/platform") || path.endsWith("/mcp/platform")))
            throw new IllegalArgumentException("Activepieces MCP Server URL must end in /mcp or /mcp/platform");
        return url.getProtocol().toLowerCase(Locale.ROOT) + "://" + url.getAuthority() + path;
    }

    private static String normalizeIssuer(String value) throws Exception {
        if (value == null || value.isEmpty() || value.length() > 2048)
            throw new IllegalStateException("OAuth authorization server URL is invalid");
        URL url = new URL(value);
        if (!secure(url) || url.getUserInfo() != null || url.getQuery() != null || url.getRef() != null)
            throw new SecurityException("OAuth authorization server URL is not secure");
        String path = url.getPath() == null ? "" : url.getPath().replaceAll("/+$", "");
        return url.getProtocol().toLowerCase(Locale.ROOT) + "://" + url.getAuthority() + path;
    }

    private static String secureEndpoint(String value, String issuer) throws Exception {
        URL url = new URL(value);
        URL base = new URL(issuer);
        if (!secure(url) || url.getUserInfo() != null || url.getQuery() != null || url.getRef() != null)
            throw new SecurityException("OAuth endpoint is not secure");
        if (!url.getProtocol().equalsIgnoreCase(base.getProtocol())
                || !url.getHost().equalsIgnoreCase(base.getHost())
                || effectivePort(url) != effectivePort(base))
            throw new SecurityException("Activepieces OAuth endpoints must stay on the advertised issuer origin");
        return value;
    }

    private static boolean secure(URL url) {
        if ("https".equalsIgnoreCase(url.getProtocol())) return true;
        return "http".equalsIgnoreCase(url.getProtocol()) && isLoopback(Uri.parse(url.toString()));
    }

    private static boolean isLoopback(Uri uri) {
        String host = uri.getHost();
        return "127.0.0.1".equals(host) || "localhost".equalsIgnoreCase(host) || "::1".equals(host);
    }

    private static int effectivePort(URL url) {
        if (url.getPort() >= 0) return url.getPort();
        return "https".equalsIgnoreCase(url.getProtocol()) ? 443 : 80;
    }

    private static boolean sameUrl(String a, String b) {
        try {
            URL x = new URL(a), y = new URL(b);
            String xp = x.getPath() == null ? "" : x.getPath().replaceAll("/+$", "");
            String yp = y.getPath() == null ? "" : y.getPath().replaceAll("/+$", "");
            return x.getProtocol().equalsIgnoreCase(y.getProtocol())
                    && x.getHost().equalsIgnoreCase(y.getHost())
                    && effectivePort(x) == effectivePort(y)
                    && xp.equals(yp);
        } catch (Exception e) { return false; }
    }

    private static String randomUrlSafe(int bytes) {
        byte[] raw = new byte[bytes];
        new SecureRandom().nextBytes(raw);
        return Base64.encodeToString(raw, Base64.URL_SAFE | Base64.NO_WRAP | Base64.NO_PADDING);
    }

    private static boolean constantTime(String a, String b) {
        if (a == null || b == null) return false;
        return MessageDigest.isEqual(a.getBytes(StandardCharsets.UTF_8), b.getBytes(StandardCharsets.UTF_8));
    }

    private static String hostLabel(String endpoint) {
        try { return new URL(endpoint).getHost(); }
        catch (Exception e) { return ""; }
    }

    private static final class OAuthMetadata {
        final String issuer, authorizationEndpoint, tokenEndpoint, registrationEndpoint;
        final Set<String> scopes, codeChallengeMethods;
        OAuthMetadata(String issuer, String authorizationEndpoint, String tokenEndpoint,
                      String registrationEndpoint, Set<String> scopes, Set<String> codeChallengeMethods) {
            this.issuer = issuer;
            this.authorizationEndpoint = authorizationEndpoint;
            this.tokenEndpoint = tokenEndpoint;
            this.registrationEndpoint = registrationEndpoint;
            this.scopes = scopes;
            this.codeChallengeMethods = codeChallengeMethods;
        }
    }
}
