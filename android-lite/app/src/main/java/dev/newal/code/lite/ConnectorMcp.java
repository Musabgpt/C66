package dev.newal.code.lite;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.concurrent.ConcurrentHashMap;

/** Official MCP endpoints only. Account tokens never enter Python or the WebView. */
final class ConnectorMcp {
    private static final String PROTOCOL = "2025-06-18";
    private static final int MAX_BYTES = 4 << 20;
    private static final ConcurrentHashMap<String, Session> SESSIONS = new ConcurrentHashMap<>();

    static String endpoint(String provider) {
        switch (provider) {
            case "gitlabmcp": return "https://gitlab.com/api/v4/mcp";
            case "notionmcp": return "https://mcp.notion.com/mcp";
            case "netlify": return "https://mcp.netlify.com/mcp";
            case "miro": return "https://mcp.miro.com/";
            case "huggingface": return "https://huggingface.co/mcp";
            default: throw new IllegalArgumentException("Unknown official MCP service");
        }
    }

    static boolean supports(String provider) {
        return provider.equals("gitlabmcp") || provider.equals("notionmcp") || provider.equals("netlify") ||
                provider.equals("miro") || provider.equals("huggingface");
    }

    static void forget(String provider) { SESSIONS.remove(provider); }

    static void forgetRemote(String label) {
        String prefix = "remote:" + label + ":";
        for (String key : SESSIONS.keySet()) if (key.startsWith(prefix)) SESSIONS.remove(key);
    }

    private static String remoteKey(String label, String endpoint) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(endpoint.getBytes(StandardCharsets.UTF_8));
        return "remote:" + label + ":" + android.util.Base64.encodeToString(
                digest, android.util.Base64.URL_SAFE | android.util.Base64.NO_WRAP | android.util.Base64.NO_PADDING);
    }

    static JSONObject toolsRemote(String label, String endpoint, String token) throws Exception {
        String key = remoteKey(label, endpoint);
        Session session = SESSIONS.get(key);
        if (session == null) {
            Session created = new Session(label, endpoint);
            Session raced = SESSIONS.putIfAbsent(key, created);
            session = raced == null ? created : raced;
        }
        synchronized (session) {
            session.initialize(token);
            return new JSONObject().put("tools", new JSONArray(session.tools.toString()))
                    .put("server", session.server).put("protocol", session.protocol);
        }
    }

    static JSONObject callRemote(String label, String endpoint, String token, String name, JSONObject arguments) throws Exception {
        String key = remoteKey(label, endpoint);
        Session session = SESSIONS.get(key);
        if (session == null) {
            Session created = new Session(label, endpoint);
            Session raced = SESSIONS.putIfAbsent(key, created);
            session = raced == null ? created : raced;
        }
        synchronized (session) {
            session.initialize(token);
            boolean listed = false;
            for (int i = 0; i < session.tools.length(); i++)
                if (name.equals(session.tools.getJSONObject(i).optString("name"))) { listed = true; break; }
            if (!listed) throw new IllegalArgumentException("Tool is not offered by the connected service");
            return session.request(token, "tools/call", new JSONObject().put("name", name)
                    .put("arguments", arguments == null ? new JSONObject() : arguments));
        }
    }

    static JSONObject tools(String provider, String token) throws Exception {
        Session session = SESSIONS.computeIfAbsent(provider, Session::new);
        synchronized (session) {
            session.initialize(token);
            return new JSONObject().put("tools", new JSONArray(session.tools.toString()))
                    .put("server", session.server).put("protocol", session.protocol);
        }
    }

    static JSONObject call(String provider, String token, String name, JSONObject arguments) throws Exception {
        Session session = SESSIONS.computeIfAbsent(provider, Session::new);
        synchronized (session) {
            session.initialize(token);
            boolean listed = false;
            for (int i = 0; i < session.tools.length(); i++)
                if (name.equals(session.tools.getJSONObject(i).optString("name"))) { listed = true; break; }
            if (!listed) throw new IllegalArgumentException("Tool is not offered by the connected service");
            // Tool execution is sent exactly once. A timeout must never replay a write.
            return session.request(token, "tools/call", new JSONObject().put("name", name)
                    .put("arguments", arguments == null ? new JSONObject() : arguments));
        }
    }

    static final class Failure extends Exception {
        final int status;
        Failure(int status) { super("MCP service returned HTTP " + status + "; operation was not retried"); this.status = status; }
    }

    private static final class Session {
        final String provider;
        final String customEndpoint;
        String id = "", protocol = PROTOCOL, fingerprint = "", server = "";
        JSONArray tools = new JSONArray();
        int sequence;
        boolean ready;
        Session(String provider) { this(provider, null); }
        Session(String provider, String customEndpoint) {
            this.provider = provider;
            this.customEndpoint = customEndpoint;
        }

        void initialize(String token) throws Exception {
            String digest = android.util.Base64.encodeToString(MessageDigest.getInstance("SHA-256")
                    .digest(token.getBytes(StandardCharsets.UTF_8)), android.util.Base64.NO_WRAP);
            if (ready && digest.equals(fingerprint)) return;
            id = ""; protocol = PROTOCOL; ready = false; tools = new JSONArray();
            JSONObject init = request(token, "initialize", new JSONObject().put("protocolVersion", PROTOCOL)
                    .put("capabilities", new JSONObject()).put("clientInfo", new JSONObject()
                            .put("name", "MusabAI").put("version", "1.0")));
            protocol = init.getString("protocolVersion");
            if (!protocol.equals(PROTOCOL) && !protocol.equals("2025-11-25") && !protocol.equals("2025-03-26") && !protocol.equals("2024-11-05"))
                throw new IllegalStateException("Unsupported MCP protocol version");
            server = init.optJSONObject("serverInfo") == null ? "" : init.getJSONObject("serverInfo").optString("name");
            post(token, new JSONObject().put("jsonrpc", "2.0").put("method", "notifications/initialized"));
            String cursor = "";
            for (int page = 0; page < 20; page++) {
                JSONObject result = request(token, "tools/list", cursor.isEmpty() ? new JSONObject() : new JSONObject().put("cursor", cursor));
                JSONArray batch = result.getJSONArray("tools");
                for (int i = 0; i < batch.length(); i++) {
                    JSONObject tool = batch.getJSONObject(i);
                    if (tool.optString("name").isEmpty() || tool.optJSONObject("inputSchema") == null)
                        throw new IllegalStateException("Invalid MCP tool definition");
                    if (tools.length() >= 512) throw new IllegalStateException("MCP tool catalog is too large");
                    tools.put(tool);
                }
                String next = result.optString("nextCursor");
                if (next.isEmpty()) { ready = true; fingerprint = digest; return; }
                if (next.equals(cursor)) throw new IllegalStateException("MCP pagination did not advance");
                cursor = next;
            }
            throw new IllegalStateException("MCP tool catalog exceeded pagination limit");
        }

        JSONObject request(String token, String method, JSONObject params) throws Exception {
            JSONObject result = post(token, new JSONObject().put("jsonrpc", "2.0").put("id", ++sequence)
                    .put("method", method).put("params", params));
            if (result.has("error")) throw new IllegalStateException("MCP request failed; operation was not retried");
            return result.getJSONObject("result");
        }

        JSONObject post(String token, JSONObject message) throws Exception {
            HttpURLConnection conn = (HttpURLConnection) new URL(customEndpoint == null ? endpoint(provider) : customEndpoint).openConnection();
            conn.setInstanceFollowRedirects(false);
            conn.setConnectTimeout(15000); conn.setReadTimeout(60000);
            conn.setRequestMethod("POST"); conn.setDoOutput(true);
            conn.setRequestProperty("Accept", "application/json, text/event-stream");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setRequestProperty("Authorization", "Bearer " + token);
            conn.setRequestProperty("User-Agent", "MusabAI-Connectors/2");
            conn.setRequestProperty("MCP-Protocol-Version", protocol);
            if (!id.isEmpty()) conn.setRequestProperty("Mcp-Session-Id", id);
            try {
                byte[] body = message.toString().getBytes(StandardCharsets.UTF_8);
                conn.setFixedLengthStreamingMode(body.length);
                try (java.io.OutputStream out = conn.getOutputStream()) { out.write(body); }
                int status = conn.getResponseCode();
                if (status < 200 || status >= 300) {
                    if (status == 401 || status == 404) ready = false;
                    throw new Failure(status);
                }
                String newId = conn.getHeaderField("Mcp-Session-Id");
                if (newId != null) {
                    if (!newId.matches("[\\x21-\\x7e]{1,512}")) throw new IllegalStateException("Invalid MCP session ID");
                    id = newId;
                }
                if (!message.has("id")) return new JSONObject();
                try (InputStream in = conn.getInputStream()) {
                    return decode(in, conn.getContentType(), message.getInt("id"));
                }
            } finally { conn.disconnect(); }
        }
    }

    // SSE can keep its connection open after the reply. Stop at the matching JSON-RPC id.
    static JSONObject decode(InputStream in, String contentType, int requestId) throws Exception {
        if (contentType != null && contentType.toLowerCase(java.util.Locale.ROOT).contains("text/event-stream")) {
            ByteArrayOutputStream line = new ByteArrayOutputStream();
            StringBuilder data = new StringBuilder();
            int count = 0, ch;
            while ((ch = in.read()) != -1) {
                if (++count > MAX_BYTES) throw new IllegalStateException("MCP response exceeds size limit");
                if (ch != '\n') { line.write(ch); continue; }
                String value = new String(line.toByteArray(), StandardCharsets.UTF_8);
                line.reset();
                if (value.endsWith("\r")) value = value.substring(0, value.length() - 1);
                if (value.isEmpty()) {
                    if (data.length() != 0) {
                        JSONObject event = new JSONObject(data.toString()); data.setLength(0);
                        if (event.has("id") && event.optInt("id", -1) == requestId &&
                                (event.has("result") || event.has("error"))) return event;
                    }
                } else if (value.startsWith("data:")) {
                    String part = value.substring(5);
                    if (part.startsWith(" ")) part = part.substring(1);
                    if (data.length() != 0) data.append('\n');
                    data.append(part);
                }
            }
            throw new IllegalStateException("MCP stream ended without the requested response");
        }
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        for (int n; (n = in.read(buffer)) != -1;) {
            if (out.size() + n > MAX_BYTES) throw new IllegalStateException("MCP response exceeds size limit");
            out.write(buffer, 0, n);
        }
        JSONObject result = new JSONObject(new String(out.toByteArray(), StandardCharsets.UTF_8));
        if (!result.has("id") || result.optInt("id", -1) != requestId ||
                (!result.has("result") && !result.has("error")))
            throw new IllegalStateException("MCP response ID mismatch");
        return result;
    }
}
