package dev.newal.code.lite;

import java.util.Locale;

/**
 * Text compared the way people type it: lower case, the Arabic letter forms people write interchangeably folded into
 * one (أ إ آ ٱ to ا, ة to ه, ى to ي), the diacritics and the tatweel removed, and runs of spaces made one. "الاعدادات"
 * then finds the app called "الإعدادات", and "ارسال" the button "إرسال". The same rules as phone_fast._norm and Apache
 * Lucene's Arabic normalizer (after Larkey et al., SIGIR 2002).
 */
final class TextFold {
    private TextFold() {
    }

    static String fold(CharSequence text) {
        if (text == null) {
            return "";
        }
        String s = text.toString().toLowerCase(Locale.ROOT);
        StringBuilder out = new StringBuilder(s.length());
        boolean space = false;
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if ((c >= 'ً' && c <= 'ْ') || c == 'ٰ' || c == 'ـ') {
                continue;                       // harakat, the dagger alef, the tatweel
            }
            switch (c) {
                case 'أ':                  // أ
                case 'إ':                  // إ
                case 'آ':                  // آ
                case 'ٱ':                  // ٱ
                    c = 'ا';               // ا
                    break;
                case 'ة':                  // ة
                    c = 'ه';               // ه
                    break;
                case 'ى':                  // ى
                    c = 'ي';               // ي
                    break;
                default:
                    break;
            }
            if (Character.isWhitespace(c)) {
                if (out.length() > 0 && !space) {
                    out.append(' ');
                }
                space = true;
                continue;
            }
            space = false;
            out.append(c);
        }
        int n = out.length();
        if (n > 0 && out.charAt(n - 1) == ' ') {
            out.setLength(n - 1);
        }
        return out.toString();
    }

    /**
     * The folded text with each number made one "#": "Inbox (12)" and "Inbox (13)" alike, a clock or a counter that
     * ticked on the same item. Empty when no letter is left, so a number alone ("14") is never taken for another.
     */
    static String withoutNumbers(CharSequence text) {
        String f = fold(text);
        StringBuilder out = new StringBuilder(f.length());
        boolean letter = false;
        for (int i = 0; i < f.length(); i++) {
            char c = f.charAt(i);
            if (!Character.isDigit(c)) {
                out.append(c);
                letter |= Character.isLetter(c);
            } else if (out.length() == 0 || out.charAt(out.length() - 1) != '#') {
                out.append('#');
            }
        }
        return letter ? out.toString() : "";
    }
}
