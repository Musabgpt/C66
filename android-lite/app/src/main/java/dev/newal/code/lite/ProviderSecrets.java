package dev.newal.code.lite;

import android.content.Context;
import org.json.JSONObject;

/** Secure provider credentials. Values live only in Android Keystore-backed ConnectorVault. */
final class ProviderSecrets {
    private ProviderSecrets() {}

    private static String provider(JSONObject a) {
        String p = a.optString("provider", "");
        if (!("groq".equals(p) || "gemini".equals(p) || "openrouter".equals(p) || "nvidia".equals(p) || "zhipu".equals(p) || "mistral".equals(p) || "crawl4ai".equals(p))) {
            throw new IllegalArgumentException("Unknown free AI provider");
        }
        return p;
    }

    private static String record(String provider) {
        return "ai-provider-" + provider;
    }

    static JSONObject handle(Context ctx, JSONObject a) throws Exception {
        String op = a.optString("action", "");
        String provider = provider(a);
        ConnectorVault vault = new ConnectorVault(ctx);
        if ("provider_secret_status".equals(op)) {
            JSONObject saved = vault.get(record(provider));
            return ok().put("configured", !saved.optString("value", "").isEmpty());
        }
        if ("provider_secret_get".equals(op)) {
            JSONObject saved = vault.get(record(provider));
            String value = saved.optString("value", "");
            if (value.isEmpty()) throw new IllegalStateException("Provider credential is not configured");
            return ok().put("value", value);
        }
        if ("provider_secret_set".equals(op)) {
            String value = a.optString("value", "").trim();
            if (value.length() < 8 || value.length() > 8192 || value.matches(".*\\s+.*")) {
                throw new IllegalArgumentException("Invalid provider credential");
            }
            vault.put(record(provider), new JSONObject().put("value", value).put("saved_at", System.currentTimeMillis()));
            return ok().put("configured", true);
        }
        if ("provider_secret_remove".equals(op)) {
            vault.remove(record(provider));
            return ok().put("configured", false);
        }
        throw new IllegalArgumentException("Unknown provider secret operation");
    }

    private static JSONObject ok() throws Exception {
        return new JSONObject().put("ok", true);
    }
}
