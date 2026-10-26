package dev.newal.code.lite;

import android.content.Context;
import android.content.Intent;
import java.util.Locale;

/** Fast, fully native actions for explicit one-step commands (no LLM or network required). */
final class QuickPhoneActions {
    private QuickPhoneActions() {}

    static String packageToOpen(String phrase) {
        if (phrase == null) return null;
        String text = phrase.toLowerCase(Locale.ROOT).trim()
                .replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا')
                .replaceAll("[.!؟،]+$", "").replaceAll("\\s+", " ");
        if (text.matches("(افتح|شغل|افتحلي|open|launch) (ال)?(واتساب|واتس اب|whatsapp)"))
            return "com.whatsapp";
        if (text.matches("(افتح|شغل|افتحلي|open|launch) (كروم|جوجل كروم|chrome)"))
            return "com.android.chrome";
        if (text.matches("(افتح|شغل|افتحلي|open|launch) (تيرمكس|ترمكس|termux)"))
            return "com.termux";
        if (text.matches("(افتح|شغل|افتحلي|open|launch) (الاعدادات|settings)"))
            return "com.android.settings";
        return null;
    }

    static void open(Context context, String pkg) {
        Intent intent = context.getPackageManager().getLaunchIntentForPackage(pkg);
        if (intent == null) throw new IllegalStateException("التطبيق غير مثبت أو لا يمكن فتحه");
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(intent);
    }
}
