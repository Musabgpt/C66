package dev.newal.code.lite;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONObject;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

/** AES-GCM records, authenticated by their record name; key never leaves Android Keystore. */
final class ConnectorVault {
    private static final String ALIAS = "musabai.connector.v1";
    private final android.content.SharedPreferences prefs;
    ConnectorVault(Context c) { prefs = c.getSharedPreferences("connector-vault", Context.MODE_PRIVATE); }

    private static synchronized SecretKey key() throws Exception {
        KeyStore store = KeyStore.getInstance("AndroidKeyStore");
        store.load(null);
        if (!store.containsAlias(ALIAS)) {
            KeyGenerator gen = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            gen.init(new KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
            gen.generateKey();
        }
        return (SecretKey) store.getKey(ALIAS, null);
    }

    synchronized JSONObject get(String name) throws Exception {
        String encoded = prefs.getString(name, "");
        if (encoded.isEmpty()) return new JSONObject();
        byte[] packed = Base64.decode(encoded, Base64.NO_WRAP);
        if (packed.length < 29) throw new SecurityException("Invalid encrypted connection record");
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key(), new GCMParameterSpec(128, packed, 0, 12));
        cipher.updateAAD(name.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        return new JSONObject(new String(cipher.doFinal(packed, 12, packed.length - 12), java.nio.charset.StandardCharsets.UTF_8));
    }

    synchronized void put(String name, JSONObject value) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, key());
        cipher.updateAAD(name.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        byte[] body = cipher.doFinal(value.toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));
        byte[] iv = cipher.getIV(), packed = new byte[iv.length + body.length];
        System.arraycopy(iv, 0, packed, 0, iv.length);
        System.arraycopy(body, 0, packed, iv.length, body.length);
        if (!prefs.edit().putString(name, Base64.encodeToString(packed, Base64.NO_WRAP)).commit())
            throw new java.io.IOException("Could not save connection securely");
    }

    synchronized void remove(String name) {
        if (!prefs.edit().remove(name).commit()) throw new IllegalStateException("Could not remove connection");
    }
}
