package dev.newal.code.lite;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * Personal-assistant requests understood on the phone itself, in the Arabic people speak and in English: alarms,
 * timers, reminders, calls, messages, directions, web and YouTube searches, a screenshot and locking the screen.
 * Only the meaning is worked out here (no Android classes, so it is unit tested on the computer); QuickPhoneActions
 * carries it out. A request that is not clearly one of these is left to the agent (parse returns null).
 */
final class AssistantCommands {
    private AssistantCommands() {}

    enum Kind { ALARM, TIMER, REMINDER, CALL, SMS, WHATSAPP, NAVIGATE, WEB_SEARCH, YOUTUBE, SCREENSHOT, LOCK }

    static final class Command {
        final Kind kind;
        int hour = -1;          // 0-23, for ALARM and a timed REMINDER
        int minute = 0;
        int seconds;            // TIMER, and a REMINDER "in 10 minutes"
        int dayOffset;          // REMINDER: 0 today, 1 tomorrow, 2 the day after
        String text = "";       // the label, the message body or the search query
        String target = "";     // who to call or message, or where to go
        String targetAlt = "";  // the same name without a leading ب or ل that may belong to it

        Command(Kind kind) {
            this.kind = kind;
        }

        @Override
        public String toString() {
            return kind + "{h=" + hour + ", m=" + minute + ", s=" + seconds + ", day=" + dayOffset
                    + ", text=" + text + ", target=" + target + "}";
        }
    }

    // ------------------------------------------------------------------ words

    private static final Map<String, Integer> NUMBER_WORDS = new HashMap<>();
    static {
        String[][] words = {
            {"1", "واحد", "وحده", "الواحده", "one"},
            {"2", "اتنين", "اثنين", "تنتين", "ثنتين", "الثانيه", "التانيه", "two"},
            {"3", "تلاته", "ثلاثه", "ثلاث", "تلات", "الثالثه", "التالته", "three"},
            {"4", "اربعه", "اربع", "الرابعه", "four"},
            {"5", "خمسه", "خمس", "الخامسه", "five"},
            {"6", "سته", "ست", "السادسه", "six"},
            {"7", "سبعه", "سبع", "السابعه", "seven"},
            {"8", "تمانيه", "ثمانيه", "تمان", "ثمان", "تمنه", "الثامنه", "التامنه", "eight"},
            {"9", "تسعه", "تسع", "التاسعه", "nine"},
            {"10", "عشره", "عشر", "العاشره", "ten"},
            {"11", "حداعش", "احدعش", "حدعش", "احداعش", "eleven"},
            {"12", "اتناعش", "اتنعش", "طنعش", "اثناعش", "twelve"},
            {"15", "ربع", "خمستعش", "خمسطعش", "fifteen"},
            {"20", "عشرين", "twenty"},
            {"30", "نص", "تلاتين", "ثلاثين", "thirty"},
            {"40", "اربعين", "forty"},
            {"45", "خمسه واربعين"},
            {"50", "خمسين", "fifty"},
        };
        for (String[] row : words) {
            int value = Integer.parseInt(row[0]);
            for (int i = 1; i < row.length; i++) NUMBER_WORDS.put(row[i], value);
        }
    }

    private static final Set<String> AM = set("الصبح", "الصباح", "صباحا", "صباح", "الفجر", "صبح", "بالصبح", "am", "morning");
    private static final Set<String> PM = set("المسا", "المساء", "مساء", "مسا", "مساءا", "بالليل", "الليل", "ليلا", "العصر",
            "بالعصر", "الظهر", "الضهر", "بالظهر", "المغرب", "العشا", "pm", "evening", "night", "tonight", "afternoon");
    private static final Set<String> TODAY = set("اليوم", "today", "tonight");
    private static final Set<String> TOMORROW = set("بكرا", "بكره", "غدا", "غدوه", "بكرى", "tomorrow");

    private static final Set<String> SECOND_UNITS = set("ثانيه", "ثواني", "ثوان", "ثانيتين", "sec", "secs", "second", "seconds");
    private static final Set<String> MINUTE_UNITS = set("دقيقه", "دقايق", "دقائق", "دقيقتين", "دقيقا", "min", "mins", "minute", "minutes");
    private static final Set<String> HOUR_UNITS = set("ساعه", "ساعات", "ساعتين", "hour", "hours", "hr", "hrs");

    private static final Set<String> CLOCK_WORDS = set("الساعه", "ساعه", "at");

    private static Set<String> set(String... words) {
        Set<String> out = new HashSet<>();
        for (String w : words) out.add(TextFold.fold(w));
        return out;
    }

    // ------------------------------------------------------------------ tokens

    /** One word of the request: as said (for labels and names) and folded (for matching). */
    private static final class Tok {
        final String raw;
        final String f;
        boolean used;

        Tok(String raw) {
            this.raw = raw;
            this.f = TextFold.fold(raw);
        }
    }

    static String asciiDigits(String s) {
        StringBuilder out = new StringBuilder(s.length());
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c >= '٠' && c <= '٩') c = (char) ('0' + (c - '٠'));
            else if (c >= '۰' && c <= '۹') c = (char) ('0' + (c - '۰'));
            else if (c == '٫') c = '.';
            out.append(c);
        }
        return out.toString();
    }

    private static List<Tok> tokens(String raw) {
        String s = asciiDigits(raw == null ? "" : raw)
                .replaceAll("[،,؛;!?؟«»\"]+", " ")
                .replaceAll("\\s+", " ")
                .trim();
        // "الساعة7" and "7ونص" are said as two words
        s = s.replaceAll("(?<=[^\\d\\s:.+])(?=\\d)", " ").replaceAll("(?<=\\d)(?=[^\\d\\s:.])", " ");
        List<Tok> out = new ArrayList<>();
        if (s.isEmpty()) return out;
        for (String w : s.split(" ")) out.add(new Tok(w));
        return out;
    }

    private static boolean isInt(String f) {
        return f.matches("\\d{1,4}");
    }

    /** The number at token i ("7", "سبعه", "السابعه"), or -1. */
    private static int number(List<Tok> t, int i) {
        if (i < 0 || i >= t.size()) return -1;
        String f = t.get(i).f;
        if (isInt(f)) return Integer.parseInt(f);
        Integer w = NUMBER_WORDS.get(f);
        if (w == null && f.startsWith("ال")) w = NUMBER_WORDS.get(f.substring(2));
        return w == null ? -1 : w;
    }

    // ------------------------------------------------------------------ time of day

    private static final class Clock {
        int hour = -1;
        int minute;
        int period;     // 0 not said, 1 morning, 2 afternoon / evening / night
        boolean noon;   // "الظهر": 12 stays 12
    }

    /** "الساعة 7 ونص المسا", "7:30", "ع 6 الصبح", "8 الا ربع", "at 7.15 pm". Marks the words it used. */
    private static Clock clock(List<Tok> t) {
        Clock c = new Clock();
        int at = -1;
        for (int i = 0; i < t.size() && at < 0; i++) {
            String f = t.get(i).f;
            if (f.matches("\\d{1,2}[:.]\\d{2}")) {
                String[] p = f.split("[:.]");
                c.hour = Integer.parseInt(p[0]);
                c.minute = Integer.parseInt(p[1]);
                t.get(i).used = true;
                at = i;
                if (i > 0 && (CLOCK_WORDS.contains(t.get(i - 1).f) || isOn(t.get(i - 1).f))) t.get(i - 1).used = true;
            } else if ((CLOCK_WORDS.contains(f) || isOn(f)) && number(t, i + 1) >= 0 && number(t, i + 1) <= 24
                    && !isUnit(t, i + 2)) {
                c.hour = number(t, i + 1);
                t.get(i).used = true;
                t.get(i + 1).used = true;
                at = i + 1;
            }
        }
        if (at < 0) {
            // a bare hour right before "الصبح" / "pm"
            for (int i = 0; i + 1 < t.size() && at < 0; i++) {
                int n = number(t, i);
                if (n >= 0 && n <= 12 && (AM.contains(t.get(i + 1).f) || PM.contains(t.get(i + 1).f))) {
                    c.hour = n;
                    t.get(i).used = true;
                    at = i;
                }
            }
        }
        if (at < 0) return c;
        int i = at + 1;
        if (i < t.size()) {
            String f = t.get(i).f;
            if (f.equals("ونص") || f.equals("ونصف")) { c.minute = 30; t.get(i).used = true; i++; }
            else if (f.equals("وربع")) { c.minute = 15; t.get(i).used = true; i++; }
            else if (f.equals("وتلت") || f.equals("وثلث")) { c.minute = 20; t.get(i).used = true; i++; }
            else if ((f.equals("الا") || f.equals("ال")) && i + 1 < t.size()) {
                String g = t.get(i + 1).f;
                int back = g.equals("ربع") ? 15 : (g.equals("تلت") || g.equals("ثلث")) ? 20 : number(t, i + 1);
                if (back > 0 && back < 60) {
                    c.hour = (c.hour + 23) % 24;
                    c.minute = 60 - back;
                    t.get(i).used = true;
                    t.get(i + 1).used = true;
                    i += 2;
                    if (i < t.size() && MINUTE_UNITS.contains(t.get(i).f)) { t.get(i).used = true; i++; }
                }
            } else if (f.equals("و") && number(t, i + 1) > 0 && number(t, i + 1) < 60) {
                c.minute = number(t, i + 1);
                t.get(i).used = true;
                t.get(i + 1).used = true;
                i += 2;
                if (i < t.size() && MINUTE_UNITS.contains(t.get(i).f)) { t.get(i).used = true; i++; }
            } else if (f.startsWith("و") && NUMBER_WORDS.containsKey(f.substring(1))) {
                int m = NUMBER_WORDS.get(f.substring(1));
                if (m > 0 && m < 60) {
                    c.minute = m;
                    t.get(i).used = true;
                    i++;
                    if (i < t.size() && MINUTE_UNITS.contains(t.get(i).f)) { t.get(i).used = true; i++; }
                }
            }
        }
        for (Tok k : t) {
            if (AM.contains(k.f)) { c.period = 1; k.used = true; }
            else if (PM.contains(k.f)) {
                c.period = 2;
                c.noon |= k.f.equals("الظهر") || k.f.equals("الضهر") || k.f.equals("بالظهر");
                k.used = true;
            }
        }
        if (c.hour > 23 || c.minute > 59) c.hour = -1;
        return c;
    }

    // compared with folded words: "على" is "علي" once folded (ى to ي), "انسى" is "انسي"
    private static final String ALA = TextFold.fold("على");
    private static final String DONT_FORGET = TextFold.fold("لا تخليني انسى");

    private static boolean isOn(String f) {
        return f.equals("ع") || f.equals(ALA) || f.equals("عالساعه");
    }

    private static boolean isUnit(List<Tok> t, int i) {
        if (i >= t.size()) return false;
        String f = t.get(i).f;
        return SECOND_UNITS.contains(f) || MINUTE_UNITS.contains(f) || HOUR_UNITS.contains(f);
    }

    /** The 24-hour time a clock means: as said with a morning/evening word, else the next one from now. */
    static int resolveHour(int hour, int minute, int period, boolean noon, int nowHour, int nowMinute) {
        if (hour < 0) return -1;
        if (hour > 12 || hour == 0) return hour % 24;
        if (period == 1) return hour == 12 ? 0 : hour;
        if (period == 2) {
            if (hour == 12) return noon ? 12 : 0;
            return hour + 12;
        }
        int now = nowHour * 60 + nowMinute;
        int a = (hour % 12) * 60 + minute;      // the morning one
        int b = a + 12 * 60;                    // the afternoon one
        int da = Math.floorMod(a - now, 24 * 60);
        int db = Math.floorMod(b - now, 24 * 60);
        if (da == 0) da = 24 * 60;
        if (db == 0) db = 24 * 60;
        return (da <= db ? a : b) / 60;
    }

    // ------------------------------------------------------------------ durations

    /** "5 دقايق", "نص ساعة", "ساعة ونص", "دقيقتين", "10 min", "an hour and 20 minutes". Marks the words it used. */
    private static int duration(List<Tok> t) {
        int total = 0;
        for (int i = 0; i < t.size(); i++) {
            Tok k = t.get(i);
            String f = k.f;
            if (f.equals("دقيقتين")) { total += 120; k.used = true; continue; }
            if (f.equals("ساعتين")) { total += 7200; k.used = true; continue; }
            if (f.equals("ثانيتين")) { total += 2; k.used = true; continue; }
            boolean unit = SECOND_UNITS.contains(f) || MINUTE_UNITS.contains(f) || HOUR_UNITS.contains(f);
            if (!unit) continue;
            int per = SECOND_UNITS.contains(f) ? 1 : MINUTE_UNITS.contains(f) ? 60 : 3600;
            int n = number(t, i - 1);
            if (n > 0 && !(i >= 2 && CLOCK_WORDS.contains(t.get(i - 2).f) && per == 3600)) {
                String before = t.get(i - 1).f;
                if (before.equals("نص")) total += per / 2;
                else if (before.equals("ربع")) total += per / 4;
                else total += n * per;
                t.get(i - 1).used = true;
            } else if (i > 0 && (t.get(i - 1).f.equals("a") || t.get(i - 1).f.equals("an"))) {
                total += per;
                t.get(i - 1).used = true;
            } else {
                total += per;               // "ساعة" alone is an hour
            }
            k.used = true;
            if (i + 1 < t.size()) {
                String g = t.get(i + 1).f;
                if (g.equals("ونص") || g.equals("ونصف")) { total += per / 2; t.get(i + 1).used = true; }
                else if (g.equals("وربع")) { total += per / 4; t.get(i + 1).used = true; }
            }
        }
        return total;
    }

    // ------------------------------------------------------------------ helpers

    private static boolean has(List<Tok> t, String... folded) {
        for (Tok k : t) for (String w : folded) if (k.f.equals(w)) return true;
        return false;
    }

    private static int index(List<Tok> t, Set<String> words) {
        for (int i = 0; i < t.size(); i++) if (words.contains(t.get(i).f)) return i;
        return -1;
    }

    private static String join(List<Tok> t, int from, int to) {
        StringBuilder sb = new StringBuilder();
        for (int i = Math.max(0, from); i < Math.min(to, t.size()); i++) {
            if (t.get(i).used) continue;
            if (sb.length() > 0) sb.append(' ');
            sb.append(t.get(i).raw);
        }
        return sb.toString().trim();
    }

    private static final Set<String> LEADING_FILLER = set("ب", "بـ", "ان", "انو", "انه", "اني", "عن", "على", "ع",
            "ل", "لـ", "الى", "to", "about", "that", "me", "لي", "الي", "حتى", "مشان", "عشان");

    /** "بالدوا" said as one word is "ب" + "الدوا"; a leading "ان"/"to" goes. */
    private static String stripLeading(String s) {
        String out = s.trim();
        for (int pass = 0; pass < 3; pass++) {
            String[] w = out.split(" ", 2);
            if (w.length == 0 || w[0].isEmpty()) break;
            String f = TextFold.fold(w[0]);
            if (LEADING_FILLER.contains(f) && w.length == 2) { out = w[1].trim(); continue; }
            if (f.length() > 3 && (f.startsWith("بال") || f.startsWith("لل"))) {
                // "بالدوا" is ب + "الدوا"; "للدكتور" is ل + "الدكتور"
                String word = f.startsWith("بال") ? w[0].substring(1) : "ال" + w[0].substring(2);
                out = word + (w.length == 2 ? " " + w[1] : "");
            }
            break;
        }
        return out.trim();
    }

    private static boolean looksLikeNumber(String s) {
        String d = s.replaceAll("[\\s\\-()]", "");
        return d.matches("\\+?\\d{3,15}");
    }

    static String phoneDigits(String s) {
        return asciiDigits(s).replaceAll("[\\s\\-()]", "");
    }

    /** Who: "لأحمد" / "بأمي" / "على 0790..." / "to Sara". The name as said, and without a ب/ل that may be a prefix. */
    private static void recipient(Command c, String who) {
        String w = stripConnector(who.trim());
        if (looksLikeNumber(w)) {
            c.target = phoneDigits(w);
            return;
        }
        c.target = w;
        String f = TextFold.fold(w);
        if (f.length() > 2 && (f.charAt(0) == 'ب' || f.charAt(0) == 'ل')) c.targetAlt = w.substring(1).trim();
    }

    private static String stripConnector(String s) {
        String[] w = s.split(" ", 2);
        if (w.length == 2 && set("ل", "لـ", "الى", "على", "ع", "ب", "بـ", "to", "on").contains(TextFold.fold(w[0]))) {
            return w[1].trim();
        }
        return s.trim();
    }

    // ------------------------------------------------------------------ the requests

    static Command parse(String raw) {
        java.util.Calendar now = java.util.Calendar.getInstance();
        return parse(raw, now.get(java.util.Calendar.HOUR_OF_DAY), now.get(java.util.Calendar.MINUTE));
    }

    static Command parse(String raw, int nowHour, int nowMinute) {
        List<Tok> t = tokens(raw);
        if (t.isEmpty()) return null;
        String first = t.get(0).f;
        String all = TextFold.fold(asciiDigits(raw));

        Command c;
        if ((c = screen(t, all)) != null) return c;
        if ((c = timerOrAlarmOrReminder(t, first, all, nowHour, nowMinute)) != null) return c;
        if ((c = youtube(t, all)) != null) return c;
        if ((c = call(t, first)) != null) return c;
        if ((c = message(t, first, all)) != null) return c;
        if ((c = navigate(t, first, all)) != null) return c;
        if ((c = webSearch(t, first, all)) != null) return c;
        return null;
    }

    private static Command screen(List<Tok> t, String all) {
        if (all.contains("سكرين شوت") || all.contains("سكرينشوت") || all.contains("screenshot")
                || all.contains("screen shot") || (all.contains("لقطه") && all.contains("شاشه"))
                || all.matches("^(صور|صورلي|صوري) (ال)?شاشه.*")) {
            return t.size() <= 6 ? new Command(Kind.SCREENSHOT) : null;
        }
        if (t.size() <= 4 && all.matches("^(اقفل|قفل|سكر|سكرلي|اقفلي|lock)( ال| )?(شاشه|الشاشه|الجوال|التلفون|الهاتف|التليفون|screen|phone|the screen|the phone)$")) {
            return new Command(Kind.LOCK);
        }
        return null;
    }

    // with the polite "ممكن ..." gone the verb is often in the second person: "(ممكن) تذكرني", "تضبطلي"
    private static final Set<String> REMIND = set("ذكرني", "فكرني", "ذكريني", "فكريني", "تذكرني", "تفكرني", "remind");
    private static final Set<String> WAKE = set("صحيني", "فيقني", "قومني", "فيقيني", "تصحيني", "تفيقني");

    /** "اضبطلي منبه", "شغل مؤقت لمدة": the words around an alarm or timer that are not its label. */
    private static final Set<String> SETUP_WORDS = set("اضبط", "اضبطلي", "حط", "حطلي", "حطيلي", "ظبط", "ظبطلي", "شغل",
            "شغلي", "ركب", "عملي", "اعملي", "تضبط", "تضبطلي", "تحط", "تحطلي", "تشغل", "تشغللي", "تعملي", "تظبطلي", "set",
            "start", "منبه", "المنبه", "منبة", "alarm", "an", "a", "مؤقت", "موقت", "تايمر", "timer", "عداد", "المؤقت",
            "الموقت", "التايمر", "for", "me", "remind", "wake", "up", "لمده", "مدته");

    private static Command timerOrAlarmOrReminder(List<Tok> t, String first, String all, int nowHour, int nowMinute) {
        boolean remind = REMIND.contains(first) || all.startsWith(DONT_FORGET) || all.startsWith("remind me");
        boolean timerWord = has(t, "مؤقت", "موقت", "تايمر", "timer", "عداد", "المؤقت", "الموقت", "التايمر");
        boolean alarmWord = has(t, "منبه", "المنبه", "منبهين", "alarm", "منبة") || WAKE.contains(first)
                || all.startsWith("wake me");
        boolean notify = first.equals("نبهني") || first.equals("نبهيني");
        if (!(remind || timerWord || alarmWord || notify)) return null;
        // "افتح المنبه" / "اعدادات المنبه" are about the app
        if (!remind && (first.equals("افتح") || first.equals("open") || all.contains("اعدادات"))) return null;

        int dayOffset = 0;
        for (int i = 0; i < t.size(); i++) {
            Tok k = t.get(i);
            if (TODAY.contains(k.f)) { k.used = true; }
            else if (TOMORROW.contains(k.f)) {
                if (i > 0 && t.get(i - 1).f.equals("بعد")) { dayOffset = 2; t.get(i - 1).used = true; }
                else dayOffset = Math.max(dayOffset, 1);
                k.used = true;
            }
        }
        // "بعد 10 دقايق" / "in 10 minutes": a timer
        int afterAt = -1;
        for (int i = 0; i < t.size(); i++) {
            if ((t.get(i).f.equals("بعد") || t.get(i).f.equals("in") || t.get(i).f.equals("كمان")) && i + 1 < t.size()
                    && !TOMORROW.contains(t.get(i + 1).f)) {
                afterAt = i;
                break;
            }
        }
        boolean relative = timerWord || afterAt >= 0;
        Clock clk = relative && afterAt >= 0 ? new Clock() : clock(t);
        int seconds = relative ? duration(t) : 0;
        if (afterAt >= 0 && seconds > 0) t.get(afterAt).used = true;

        for (Tok k : t) {
            if (REMIND.contains(k.f) || WAKE.contains(k.f) || k.f.equals("نبهني") || k.f.equals("نبهيني")
                    || SETUP_WORDS.contains(k.f)) {
                k.used = true;
            }
        }
        String label = stripLeading(join(t, 0, t.size()));
        if (all.startsWith(DONT_FORGET)) label = stripLeading(label.replaceFirst("^\\S+ \\S+ \\S+", ""));
        // "ذكرني باجتماع": after "remind me", a word stuck to ب is "about" + the word
        if (remind && label.length() > 3 && TextFold.fold(label).startsWith("ب") && !TextFold.fold(label).startsWith("بال")) {
            label = label.substring(1);
        }

        if (seconds > 0 && (relative || notify || remind)) {
            Command c = new Command(remind ? Kind.REMINDER : Kind.TIMER);
            c.seconds = seconds;
            c.text = label;
            return c;
        }
        if (timerWord) return null;   // a timer with no length: let the agent ask
        int hour = resolveHour(clk.hour, clk.minute, clk.period, clk.noon, nowHour, nowMinute);
        if (remind) {
            Command c = new Command(Kind.REMINDER);
            c.hour = hour;
            c.minute = hour >= 0 ? clk.minute : 0;
            c.dayOffset = dayOffset;
            c.text = label;
            return c.text.isEmpty() ? null : c;
        }
        if (hour < 0) return null;
        Command c = new Command(Kind.ALARM);
        c.hour = hour;
        c.minute = clk.minute;
        c.text = label;
        return c;
    }

    private static final Set<String> CALL = set("اتصل", "اتصلي", "اتصللي", "دق", "دقلي", "دقي", "رن", "رنلي", "call", "ring", "phone");

    private static Command call(List<Tok> t, String first) {
        if (!CALL.contains(first) || t.size() < 2) return null;
        String rest = join(t, 1, t.size());
        String f = TextFold.fold(rest);
        if (f.startsWith("بال") && f.contains("واتس")) return null;      // a WhatsApp call: the agent
        if (f.isEmpty() || f.contains("واتس") || f.contains("whatsapp")) return null;
        if (f.startsWith("الساعه") || f.startsWith("ساعه") || f.startsWith("الباب")) return null;   // "دق الساعة 5"
        Command c = new Command(Kind.CALL);
        recipient(c, rest);
        return c.target.isEmpty() ? null : c;
    }

    private static final Set<String> SEND = set("ابعث", "ابعت", "بعت", "ارسل", "ابعثلي", "ابعتلي", "ارسللي", "ابعتي", "ابعثي",
            "ارسلي", "send", "text", "message", "راسل", "كتب", "اكتب");
    // not "نص": "اكتب نص عن ..." is writing, not a message
    private static final Set<String> MESSAGE_WORDS = set("رساله", "مسج", "مسيج", "sms", "message", "msg", "رسالة", "text");
    private static final Set<String> BODY_MARK = set("نصها", "مضمونها", "قله", "قلها", "قلو", "قوله", "قولها", "قول", "انو",
            "تقول", "بتقول", "فيها", "محتواها", "saying", "that", "says", ":", "قلهم", "يقول");

    private static Command message(List<Tok> t, String first, String all) {
        if (!SEND.contains(first) || t.size() < 3) return null;
        boolean wa = all.contains("واتساب") || all.contains("واتس") || all.contains("whatsapp");
        boolean msg = wa;
        for (Tok k : t) if (MESSAGE_WORDS.contains(k.f)) msg = true;
        if (!msg) return null;
        t.get(0).used = true;
        for (Tok k : t) {
            if (MESSAGE_WORDS.contains(k.f) || k.f.contains("واتس") || k.f.equals("whatsapp")) k.used = true;
        }
        // "على واتساب" / "via WhatsApp": the word before the app's name goes too
        for (int i = 0; i + 1 < t.size(); i++) {
            String n = t.get(i + 1).f;
            if ((t.get(i).f.equals(ALA) || t.get(i).f.equals("ع") || t.get(i).f.equals("عبر") || t.get(i).f.equals("on")
                    || t.get(i).f.equals("via")) && (n.contains("واتس") || n.equals("whatsapp"))) {
                t.get(i).used = true;
            }
        }
        int mark = -1;
        for (int i = 1; i < t.size(); i++) {
            if (!t.get(i).used && (BODY_MARK.contains(t.get(i).f) || t.get(i).raw.endsWith(":"))) { mark = i; break; }
        }
        String who;
        String body;
        if (mark > 0) {
            // "لسارة: نص" — the word with the colon is who, the rest is the message
            boolean colon = t.get(mark).raw.endsWith(":") && !BODY_MARK.contains(t.get(mark).f);
            if (colon) {
                who = join(t, 1, mark + 1).replaceFirst(":$", "");
                body = join(t, mark + 1, t.size());
            } else {
                who = join(t, 1, mark);
                t.get(mark).used = true;
                body = join(t, mark, t.size());
            }
        } else {
            int w = -1;
            for (int i = 1; i < t.size(); i++) if (!t.get(i).used) { w = i; break; }
            if (w < 0) return null;
            // "ابعث رسالة لأحمد" / "send a message to 0790 123": who is the next word, or a whole number
            int end = w + 1;
            String f0 = TextFold.fold(t.get(w).raw);
            if (set("ل", "لـ", "الى", "to").contains(f0) && w + 1 < t.size()) end = w + 2;
            while (end < t.size() && t.get(end).f.matches("[\\d\\-+]+")) end++;
            who = join(t, w, end);
            body = join(t, end, t.size());
        }
        Command c = new Command(wa ? Kind.WHATSAPP : Kind.SMS);
        recipient(c, who);
        c.text = stripLeading(body);
        return c.target.isEmpty() ? null : c;
    }

    private static Command navigate(List<Tok> t, String first, String all) {
        int from;
        if (set("خذني", "وديني", "دلني", "دليني", "وصلني", "navigate", "directions").contains(first)) from = 1;
        else if (all.startsWith("take me to") || all.startsWith("كيف بروح") || all.startsWith("كيف اروح")
                || all.startsWith("كيف بوصل") || all.startsWith("كيف اوصل") || all.startsWith("كيف بدي روح")) {
            from = all.startsWith("كيف بدي روح") ? 3 : 2;
            if (all.startsWith("take me to")) from = 3;
        } else if (all.matches("^(افتح |شغل )?(الخرائط|الخريطه|خرائط جوجل|جوجل ماب|ماب|maps|google maps) (علي|ع|الي|عند|لعند|to|for) .+")) {
            from = 0;
            for (int i = 0; i < t.size(); i++) {
                String f = t.get(i).f;
                if (set("على", "ع", "الى", "عند", "لعند", "to", "for").contains(f)) { from = i + 1; break; }
            }
        } else {
            return null;
        }
        if (from >= t.size()) return null;
        String place = join(t, from, t.size());
        place = stripConnector(place);
        String[] lead = place.split(" ", 2);
        String lf = TextFold.fold(lead[0]);
        if ((lf.equals("لعند") || lf.equals("عند") || lf.equals("الطريق") || lf.equals("طريق") || lf.equals("لل"))
                && lead.length == 2) {
            place = lead[1];
        }
        place = place.replaceFirst("^(?:ع|ل)(?=ال)", "");
        Command c = new Command(Kind.NAVIGATE);
        c.target = place.trim();
        return c.target.isEmpty() ? null : c;
    }

    private static Command webSearch(List<Tok> t, String first, String all) {
        int from = -1;
        String[][] starts = {
            {"ابحث في جوجل عن", "4"}, {"ابحث بجوجل عن", "3"}, {"ابحث على جوجل عن", "4"}, {"ابحث ع جوجل عن", "4"},
            {"ابحثلي عن", "2"}, {"ابحث عن", "2"}, {"دورلي على", "2"}, {"دورلي عن", "2"}, {"دور على", "2"},
            {"فتشلي عن", "2"}, {"فتش عن", "2"}, {"ابحث على النت عن", "4"}, {"ابحث بالنت عن", "3"},
            {"search google for", "3"}, {"search for", "2"}, {"google", "1"}, {"جوجل", "1"}, {"گوگل", "1"},
        };
        for (String[] s : starts) {
            String f = TextFold.fold(s[0]);
            if (all.equals(f) || all.startsWith(f + " ")) { from = Integer.parseInt(s[1]); break; }
        }
        if (from < 0 || from >= t.size()) return null;
        // "دور على الزر" / "ابحث عن ملف" on the screen or in files: the agent
        String q = join(t, from, t.size());
        String qf = TextFold.fold(q);
        if (qf.startsWith("الزر") || qf.startsWith("زر") || qf.startsWith("ملف") || qf.startsWith("الملف")
                || qf.contains("بالشاشه") || qf.contains("على الشاشه") || qf.contains("بالجوال")) {
            return null;
        }
        Command c = new Command(Kind.WEB_SEARCH);
        c.text = q.trim();
        return c.text.isEmpty() ? null : c;
    }

    private static Command youtube(List<Tok> t, String all) {
        if (!(all.contains("يوتيوب") || all.contains("اليوتيوب") || all.contains("youtube") || all.contains("يوتوب"))) {
            return null;
        }
        String first = t.get(0).f;
        if (!set("شغل", "شغلي", "شغللي", "حطلي", "حط", "ابحث", "ابحثلي", "دور", "دورلي", "play", "search", "افتح", "فرجيني",
                "وريني", "اعرضلي", "اسمعني").contains(first)) {
            return null;
        }
        for (Tok k : t) {
            String f = k.f;
            if (f.contains("يوتيوب") || f.contains("youtube") || f.contains("يوتوب")) k.used = true;
        }
        t.get(0).used = true;
        for (int i = 0; i < t.size(); i++) {
            String f = t.get(i).f;
            boolean beforeApp = i + 1 < t.size() && t.get(i + 1).used && (t.get(i + 1).f.contains("يوتيوب")
                    || t.get(i + 1).f.contains("youtube") || t.get(i + 1).f.contains("يوتوب"));
            if (beforeApp && set("على", "ع", "في", "بال", "من", "on", "in", "from", "عال").contains(f)) t.get(i).used = true;
        }
        String q = stripLeading(join(t, 0, t.size()));
        q = q.replaceFirst("^(?i)(?:فيديو|مقطع|video|a video of|songs? of)\\s+", "");
        if (q.isEmpty()) return null;       // "افتح يوتيوب": the app
        if (first.equals("افتح") && !all.contains(" عن ")) return null;
        Command c = new Command(Kind.YOUTUBE);
        c.text = q.trim();
        return c;
    }

    /** "7:05 ص" / "7:05 PM" for telling the person what was set. */
    static String clockText(int hour, int minute, boolean arabic) {
        int h12 = hour % 12 == 0 ? 12 : hour % 12;
        String mm = String.format(Locale.ROOT, "%02d", minute);
        if (arabic) {
            String part = hour < 12 ? "صباحاً" : hour < 17 ? "بعد الظهر" : "مساءً";
            return h12 + ":" + mm + " " + part;
        }
        return h12 + ":" + mm + (hour < 12 ? " AM" : " PM");
    }

    /** "5 دقائق" / "ساعة و10 دقائق" for telling the person how long. */
    static String durationText(int seconds) {
        int h = seconds / 3600;
        int m = (seconds % 3600) / 60;
        int s = seconds % 60;
        List<String> parts = new ArrayList<>();
        if (h > 0) parts.add(h == 1 ? "ساعة" : h == 2 ? "ساعتين" : h + " ساعات");
        if (m > 0) parts.add(m == 1 ? "دقيقة" : m == 2 ? "دقيقتين" : m <= 10 ? m + " دقائق" : m + " دقيقة");
        if (s > 0) parts.add(s == 1 ? "ثانية" : s == 2 ? "ثانيتين" : s <= 10 ? s + " ثواني" : s + " ثانية");
        return String.join(" و", parts.isEmpty() ? Arrays.asList("0 ثانية") : parts);
    }
}
