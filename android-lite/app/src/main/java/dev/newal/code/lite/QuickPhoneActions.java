package dev.newal.code.lite;

import android.Manifest;
import android.accessibilityservice.AccessibilityService;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.database.Cursor;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.media.AudioManager;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.AlarmClock;
import android.provider.CalendarContract;
import android.provider.ContactsContract;
import android.provider.Settings;

import java.text.SimpleDateFormat;
import java.util.Calendar;
import java.util.Date;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * Fast, fully native actions and conversational Arabic wake/attention handling for the floating voice bubble.
 * Single-step device commands (opening any installed app, flashlight, volume, battery, time, navigation, settings)
 * execute in milliseconds without waiting for an LLM round-trip, while multi-step commands pass cleanly to the agent.
 */
final class QuickPhoneActions {
    private QuickPhoneActions() {}

    static final class ActionResult {
        final boolean handled;
        final String displayMessage;
        final String spokenMessage;
        final boolean collapsePanel;

        ActionResult(boolean handled, String displayMessage, String spokenMessage, boolean collapsePanel) {
            this.handled = handled;
            this.displayMessage = displayMessage;
            this.spokenMessage = spokenMessage;
            this.collapsePanel = collapsePanel;
        }

        static ActionResult notHandled() {
            return new ActionResult(false, "", "", false);
        }

        static ActionResult ok(String display, String spoken, boolean collapse) {
            return new ActionResult(true, display, spoken, collapse);
        }
    }

    private static final String[] WAKE_PREFIXES = new String[] {
        "يا مصعب اي اي", "مصعب اي اي", "يا مصعب", "مصعب", "يا مساعد", "مساعد",
        "اممم", "امم", "أمم", "ام", "أم", "إم", "همم", "هم",
        "اسمعني", "اسمع", "تسمعني", "عم تسمعني",
        "مرحبا", "يا هلا", "هلا", "اهلا", "أهلا", "الو", "ألو", "هاي",
        "لو سمحت", "من فضلك", "ممكن", "طيب", "يلا", "حبيبي", "اخي", "أخي",
        "بدي منك", "اريد منك", "أريد منك", "بدي اياك", "بدي", "اريد", "أريد"
    };

    private static final Map<String, String[]> KNOWN_APPS = new HashMap<>();
    static {
        KNOWN_APPS.put("واتساب", new String[] {"com.whatsapp", "com.whatsapp.w4b"});
        KNOWN_APPS.put("واتس اب", new String[] {"com.whatsapp", "com.whatsapp.w4b"});
        KNOWN_APPS.put("واتس", new String[] {"com.whatsapp", "com.whatsapp.w4b"});
        KNOWN_APPS.put("whatsapp", new String[] {"com.whatsapp", "com.whatsapp.w4b"});
        KNOWN_APPS.put("واتساب الاعمال", new String[] {"com.whatsapp.w4b", "com.whatsapp"});
        KNOWN_APPS.put("واتساب بيزنس", new String[] {"com.whatsapp.w4b", "com.whatsapp"});
        KNOWN_APPS.put("يوتيوب", new String[] {"com.google.android.youtube"});
        KNOWN_APPS.put("youtube", new String[] {"com.google.android.youtube"});
        KNOWN_APPS.put("تيليجرام", new String[] {"org.telegram.messenger", "org.telegram.messenger.web"});
        KNOWN_APPS.put("تليجرام", new String[] {"org.telegram.messenger", "org.telegram.messenger.web"});
        KNOWN_APPS.put("تلغرام", new String[] {"org.telegram.messenger", "org.telegram.messenger.web"});
        KNOWN_APPS.put("telegram", new String[] {"org.telegram.messenger", "org.telegram.messenger.web"});
        KNOWN_APPS.put("كروم", new String[] {"com.android.chrome"});
        KNOWN_APPS.put("جوجل كروم", new String[] {"com.android.chrome"});
        KNOWN_APPS.put("متصفح", new String[] {"com.android.chrome", "com.sec.android.app.sbrowser"});
        KNOWN_APPS.put("chrome", new String[] {"com.android.chrome"});
        KNOWN_APPS.put("تيرمكس", new String[] {"com.termux"});
        KNOWN_APPS.put("ترمكس", new String[] {"com.termux"});
        KNOWN_APPS.put("termux", new String[] {"com.termux"});
        KNOWN_APPS.put("الاعدادات", new String[] {"com.android.settings"});
        KNOWN_APPS.put("الضبط", new String[] {"com.android.settings"});
        KNOWN_APPS.put("اعدادات", new String[] {"com.android.settings"});
        KNOWN_APPS.put("settings", new String[] {"com.android.settings"});
        KNOWN_APPS.put("الكاميرا", new String[] {"com.sec.android.app.camera", "com.android.camera", "com.google.android.GoogleCamera"});
        KNOWN_APPS.put("كاميرا", new String[] {"com.sec.android.app.camera", "com.android.camera", "com.google.android.GoogleCamera"});
        KNOWN_APPS.put("camera", new String[] {"com.sec.android.app.camera", "com.android.camera"});
        KNOWN_APPS.put("الاستوديو", new String[] {"com.sec.android.gallery3d", "com.google.android.apps.photos"});
        KNOWN_APPS.put("المعرض", new String[] {"com.sec.android.gallery3d", "com.google.android.apps.photos"});
        KNOWN_APPS.put("الصور", new String[] {"com.sec.android.gallery3d", "com.google.android.apps.photos"});
        KNOWN_APPS.put("gallery", new String[] {"com.sec.android.gallery3d", "com.google.android.apps.photos"});
        KNOWN_APPS.put("الهاتف", new String[] {"com.samsung.android.dialer", "com.google.android.dialer", "com.android.dialer"});
        KNOWN_APPS.put("الاتصال", new String[] {"com.samsung.android.dialer", "com.google.android.dialer", "com.android.dialer"});
        KNOWN_APPS.put("الرسائل", new String[] {"com.google.android.apps.messaging", "com.samsung.android.messaging"});
        KNOWN_APPS.put("رسائل", new String[] {"com.google.android.apps.messaging", "com.samsung.android.messaging"});
        KNOWN_APPS.put("جهات الاتصال", new String[] {"com.samsung.android.app.contacts", "com.google.android.contacts", "com.android.contacts"});
        KNOWN_APPS.put("الاسماء", new String[] {"com.samsung.android.app.contacts", "com.google.android.contacts", "com.android.contacts"});
        KNOWN_APPS.put("الحاسبه", new String[] {"com.sec.android.app.popupcalculator", "com.google.android.calculator"});
        KNOWN_APPS.put("حاسبه", new String[] {"com.sec.android.app.popupcalculator", "com.google.android.calculator"});
        KNOWN_APPS.put("الاله الحاسبه", new String[] {"com.sec.android.app.popupcalculator", "com.google.android.calculator"});
        KNOWN_APPS.put("الساعه", new String[] {"com.sec.android.app.clockpackage", "com.google.android.deskclock"});
        KNOWN_APPS.put("المنبه", new String[] {"com.sec.android.app.clockpackage", "com.google.android.deskclock"});
        KNOWN_APPS.put("التقويم", new String[] {"com.samsung.android.calendar", "com.google.android.calendar"});
        KNOWN_APPS.put("الملفات", new String[] {"com.sec.android.app.myfiles", "com.google.android.apps.nbu.files"});
        KNOWN_APPS.put("ملفاتي", new String[] {"com.sec.android.app.myfiles", "com.google.android.apps.nbu.files"});
        KNOWN_APPS.put("متجر بلاي", new String[] {"com.android.vending"});
        KNOWN_APPS.put("جوجل بلاي", new String[] {"com.android.vending"});
        KNOWN_APPS.put("سوق بلاي", new String[] {"com.android.vending"});
        KNOWN_APPS.put("انستجرام", new String[] {"com.instagram.android"});
        KNOWN_APPS.put("انستا", new String[] {"com.instagram.android"});
        KNOWN_APPS.put("instagram", new String[] {"com.instagram.android"});
        KNOWN_APPS.put("فيسبوك", new String[] {"com.facebook.katana", "com.facebook.lite"});
        KNOWN_APPS.put("فيس بوك", new String[] {"com.facebook.katana", "com.facebook.lite"});
        KNOWN_APPS.put("facebook", new String[] {"com.facebook.katana", "com.facebook.lite"});
        KNOWN_APPS.put("ماسنجر", new String[] {"com.facebook.orca"});
        KNOWN_APPS.put("تيك توك", new String[] {"com.zhiliaoapp.musically", "com.ss.android.ugc.trill"});
        KNOWN_APPS.put("تيكتوك", new String[] {"com.zhiliaoapp.musically", "com.ss.android.ugc.trill"});
        KNOWN_APPS.put("جيميل", new String[] {"com.google.android.gm"});
        KNOWN_APPS.put("البريد", new String[] {"com.google.android.gm"});
        KNOWN_APPS.put("خرائط جوجل", new String[] {"com.google.android.apps.maps"});
        KNOWN_APPS.put("الخرائط", new String[] {"com.google.android.apps.maps"});
        KNOWN_APPS.put("جوجل", new String[] {"com.google.android.googlequicksearchbox"});
        KNOWN_APPS.put("ملاحظات", new String[] {"com.samsung.android.app.notes", "com.google.android.keep"});
        KNOWN_APPS.put("الملاحظات", new String[] {"com.samsung.android.app.notes", "com.google.android.keep"});
    }

    static String cleanPunctuation(String phrase) {
        if (phrase == null) return "";
        return phrase.trim()
                .replaceAll("^[.!؟،,;:«»\"'\\-\\s]+", "")
                .replaceAll("[.!؟،,;:«»\"'\\-\\s]+$", "")
                .replaceAll("\\s+", " ");
    }

    /**
     * Checks if the user spoke a standalone wake word, attention check, or greeting (such as "ام", "يا مصعب", "مرحبا", "كيفك").
     * Returns a natural Arabic reply if so, or null if the phrase is an actual command/question.
     */
    static String conversationalReply(String rawPhrase) {
        String f = TextFold.fold(cleanPunctuation(rawPhrase));
        if (f.isEmpty()) return null;

        if (f.equals("ام") || f.equals("امم") || f.equals("اممم") || f.equals("هم") || f.equals("همم")
                || f.equals("اه") || f.equals("ايه") || f.equals("ايوه") || f.equals("نعم") || f.equals("عم")
                || f.equals("يا مصعب") || f.equals("مصعب") || f.equals("يا مساعد") || f.equals("مساعد")
                || f.equals("اسمع") || f.equals("اسمعني") || f.equals("تسمعني") || f.equals("عم تسمعني")
                || f.equals("انت هون") || f.equals("موجود") || f.equals("جاهز") || f.equals("انت معي")
                || f.equals("سؤال") || f.equals("عندي طلب") || f.equals("عندي مهمه") || f.equals("بدي اطلب منك طلب")) {
            return "نعم، أنا معك وعم اسمعك! احكيلي شو المهمة يلي بدك أنفذها؟";
        }
        if (f.equals("السلام عليكم") || f.equals("سلام عليكم") || f.equals("سلام") || f.equals("السلام عليكم ورحمه الله")) {
            return "وعليكم السلام ورحمة الله! أنا جاهز، احكيلي شو المهمة يلي بدك أعملها؟";
        }
        if (f.equals("مرحبا") || f.equals("مرحب") || f.equals("هلا") || f.equals("يا هلا")
                || f.equals("اهلا") || f.equals("اهلا وسهلا") || f.equals("الو") || f.equals("هاي")) {
            return "أهلاً فيك! عم اسمعك، قولي شو المهمة المطلوبة؟";
        }
        if (f.equals("صباح الخير") || f.equals("صباح النور") || f.equals("صباحو")) {
            return "صباح النور والسرور! أنا جاهز لأي مهمة، تفضل احكيلي.";
        }
        if (f.equals("مساء الخير") || f.equals("مساء النور") || f.equals("مساء الورد")) {
            return "مساء النور! أنا بالخدمة، شو حابب أعملك اليوم؟";
        }
        if (f.equals("كيفك") || f.equals("شلونك") || f.equals("كيف الحال") || f.equals("كيف حالك")
                || f.equals("شو اخبارك") || f.equals("شو الاخبار") || f.equals("طمني عنك")
                || f.equals("ام كيفك") || f.equals("مرحبا كيفك") || f.equals("هلا كيفك")) {
            return "الحمد لله تمام وبأحسن حال! أنا جاهز، قولي شو المهمة يلي بدك أنفذها؟";
        }
        if (f.equals("شكرا") || f.equals("شكرا الك") || f.equals("شكرا جزيلا") || f.equals("يسلمو")
                || f.equals("يسلموا") || f.equals("يعطيك العافيه") || f.equals("الله يعطيك العافيه")
                || f.equals("كفو") || f.equals("ممتاز") || f.equals("برافو") || f.equals("احسنت") || f.equals("اشكرك")) {
            return "الله يعافيك ويسعدك، بالخدمة دائماً! إذا بدك أي مهمة تانية بس احكيلي.";
        }
        if (f.equals("مين انت") || f.equals("من انت") || f.equals("عرفني عن حالك")
                || f.equals("شو اسمك") || f.equals("شو بتعمل") || f.equals("شو بتقدر تعمل")) {
            return "أنا مساعدك الصوتي الذكي في مصعب إي آي. بتقدر تحكي معي، تقلي أم وتعطيني أي مهمة على الهاتف أو البرمجة لأنفذها وأحكي معك خطوة بخطوة.";
        }
        return null;
    }

    /**
     * Strips leading conversational filler or wake words (e.g., "ام افتح واتساب" -> "افتح واتساب")
     * when followed by a command, preserving the command intact.
     */
    static String normalizeCommand(String rawPhrase) {
        String cleaned = cleanPunctuation(rawPhrase);
        if (cleaned.isEmpty()) return "";
        if (conversationalReply(cleaned) != null) return cleaned;

        String current = cleaned;
        for (int pass = 0; pass < 3; pass++) {
            String folded = TextFold.fold(current);
            boolean stripped = false;
            for (String prefix : WAKE_PREFIXES) {
                String foldedPrefix = TextFold.fold(prefix);
                if (folded.startsWith(foldedPrefix + " ")) {
                    // Strip the first matching number of words from current
                    int prefixWords = foldedPrefix.split("\\s+").length;
                    String[] words = current.split("\\s+");
                    if (words.length > prefixWords) {
                        StringBuilder sb = new StringBuilder();
                        for (int i = prefixWords; i < words.length; i++) {
                            if (sb.length() > 0) sb.append(' ');
                            sb.append(words[i]);
                        }
                        String candidate = cleanPunctuation(sb.toString());
                        if (!candidate.isEmpty() && conversationalReply(candidate) == null) {
                            current = candidate;
                            stripped = true;
                            break;
                        }
                    }
                }
            }
            if (!stripped) break;
        }
        return current;
    }

    static boolean isStopCommand(String rawPhrase) {
        String f = TextFold.fold(normalizeCommand(rawPhrase));
        return f.equals("وقف") || f.equals("توقف") || f.equals("ايقاف") || f.equals("الغي")
                || f.equals("الغاء") || f.equals("الغي المهمه") || f.equals("وقف المهمه")
                || f.equals("ستوب") || f.equals("stop") || f.equals("cancel")
                || f.equals("خلص وقف") || f.equals("كافي") || f.equals("لا تكمل")
                || f.equals("اسكت") || f.equals("خلاص وقف");
    }

    static boolean isStatusQuery(String rawPhrase) {
        String f = TextFold.fold(normalizeCommand(rawPhrase));
        return f.equals("شو عم تعمل") || f.equals("وين وصلت") || f.equals("شو صار")
                || f.equals("خلصت") || f.equals("شو الوضع") || f.equals("شو عم يصير")
                || f.equals("بشو عم تشتغل") || f.equals("شو صار بالمهمه");
    }

    static boolean isRepeatQuery(String rawPhrase) {
        String f = TextFold.fold(normalizeCommand(rawPhrase));
        return f.equals("عيد") || f.equals("كرر") || f.equals("شو قلت")
                || f.equals("ما سمعت") || f.equals("عيد الجواب") || f.equals("اقرا النتيجه");
    }

    static boolean isNewSessionCommand(String rawPhrase) {
        String f = TextFold.fold(normalizeCommand(rawPhrase));
        return f.equals("جلسه جديده") || f.equals("محادثه جديده") || f.equals("صفحه جديده")
                || f.equals("ابدا من جديد") || f.equals("امسح المحادثه");
    }

    /**
     * Filters out 1-character ambient room noise or speaker echo fragments ("اي ان اي اي")
     * while always allowing valid wake words ("ام", "أم", "اه", "هلا", "وقف", "عيد", etc.).
     */
    static boolean isLikelyEchoOrNoise(String rawPhrase) {
        String cleaned = cleanPunctuation(rawPhrase);
        if (cleaned.isEmpty()) return true;
        if (conversationalReply(cleaned) != null || isStopCommand(cleaned)
                || isStatusQuery(cleaned) || isRepeatQuery(cleaned) || isNewSessionCommand(cleaned)) {
            return false;
        }
        String f = TextFold.fold(cleaned);
        if (f.length() <= 2) return true;
        if (f.matches("^(اي|ان|اه|ام|ال|يا|\\s)+$")) return true;
        if (f.contains("نعم انا معك وعم اسمعك") || f.contains("احكيلي شو المهمه")
                || f.contains("حاضر عم نفذ المهمه") || f.contains("حاضر عم اشتغل عليها")
                || f.contains("بدات محادثه جديده") || f.contains("لم افهم طلبك")) {
            return true;
        }
        return false;
    }

    private static boolean isCompoundTask(String folded) {
        return folded.contains(" و ") || folded.contains(" ثم ") || folded.contains(" وبعدين ")
                || folded.contains(" بعدها ") || folded.contains(" واكتب") || folded.contains(" وابعث")
                || folded.contains(" وارسل") || folded.contains(" وابحث") || folded.contains(" واضغط")
                || folded.contains(" ودور") || folded.contains(" وشوف") || folded.contains(" واقرا")
                || folded.contains(" وشغل ") || folded.contains(" وافتح ");
    }

    static ActionResult executeQuickAction(Context context, String rawPhrase) {
        String normalized = normalizeCommand(rawPhrase);
        String f = TextFold.fold(normalized);
        if (f.isEmpty()) return ActionResult.notHandled();

        // 0. Personal-assistant requests (alarm, timer, reminder, call, message, directions, search). Before the
        // compound check: a message or a reminder says "و" inside what it carries ("قله تعال وجيب خبز").
        AssistantCommands.Command assistant = AssistantCommands.parse(normalized);
        if (assistant != null) {
            ActionResult done = runAssistant(context, assistant);
            if (done.handled) return done;
        }

        // Do not intercept multi-step compound instructions; let the full agent execute them.
        if (isCompoundTask(f)) {
            return ActionResult.notHandled();
        }

        // 1. Flashlight / Torch
        if (f.matches("(شغل|ضوي|افتح|نور|تفعيل) (ال)?(فلاش|كشاف|ضوء|ضو|تشغيل الفلاش)")
                || f.equals("فلاش") || f.equals("الفلاش") || f.equals("الكشاف")) {
            return setTorch(context, true);
        }
        if (f.matches("(طفي|اطفي|اطفئ|سكر|اغلق|ايقاف) (ال)?(فلاش|كشاف|ضوء|ضو)")) {
            return setTorch(context, false);
        }

        // 2. Volume Controls
        if (f.matches("(علي|ارفع|زود|قوي|زيد) (ال)?صوت( شوي)?")) {
            return adjustVolume(context, 2, false, false);
        }
        if (f.matches("(وطي|اخفض|نقص|هدي|قلل) (ال)?صوت( شوي)?")) {
            return adjustVolume(context, -2, false, false);
        }
        if (f.matches("(كتم|اكتم|صامت|ميوت|اطفي) (ال)?صوت") || f.equals("ميوت") || f.equals("صامت")) {
            return adjustVolume(context, 0, true, false);
        }
        if (f.matches("(فك الكتم|شغل الصوت|رجع الصوت|افتح الصوت)")) {
            return adjustVolume(context, 0, false, true);
        }

        // 3. Battery Status
        if (f.matches("(كم|شو|قديش|اعطيني|شوف|شوفلي|افحص)?\\s*(نسبه )?(ال)?(شحن|بطاريه)( الهاتف)?")
                || f.equals("حاله البطاريه") || f.equals("شحن الهاتف")) {
            return getBatteryStatus(context);
        }

        // 4. Current Time & Date
        if (f.matches("(كم|شو|قديش) الساعه( الان| هلق)?") || f.equals("الوقت") || f.equals("شو الوقت") || f.equals("كم الوقت")) {
            String time = new SimpleDateFormat("h:mm a", new Locale("ar")).format(new Date());
            String msg = "الساعة الآن " + time;
            return ActionResult.ok("🕒 " + msg, msg, false);
        }
        if (f.matches("(كم|شو) التاريخ( اليوم)?") || f.equals("تاريخ اليوم") || f.equals("شو اليوم")) {
            String date = new SimpleDateFormat("EEEE، d MMMM yyyy", new Locale("ar")).format(new Date());
            String msg = "اليوم هو " + date;
            return ActionResult.ok("📅 " + msg, msg, false);
        }

        // 5. System Navigation & Screen Scrolling
        if (f.matches("(اذهب الى |روح ع|روح على |افتح )?(الشاشه )?الرئيسيه") || f.equals("هوم") || f.equals("الشاشه الرئيسيه")) {
            PhoneControlService pcs = PhoneControlService.on;
            if (pcs != null) {
                pcs.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME);
            } else {
                Intent home = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_HOME).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                context.startActivity(home);
            }
            return ActionResult.ok("🏠 انتقلت إلى الشاشة الرئيسية.", "انتقلت إلى الشاشة الرئيسية.", true);
        }
        if (f.equals("رجوع") || f.equals("ارجع") || f.equals("للخلف") || f.equals("خطوه للخلف")) {
            PhoneControlService pcs = PhoneControlService.on;
            if (pcs == null) {
                return ActionResult.ok("⚠️ يلزم تفعيل إمكانية الوصول لتنفيذ زر الرجوع.", "يلزم تفعيل إمكانية الوصول.", false);
            }
            pcs.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK);
            return ActionResult.ok("🔙 تم الرجوع خطوة للخلف.", "تم الرجوع.", true);
        }
        if (f.matches("(افتح |اعرض |شوف )?(ال)?اشعارات") || f.equals("شريط الاشعارات")) {
            PhoneControlService pcs = PhoneControlService.on;
            if (pcs == null) {
                return ActionResult.ok("⚠️ يلزم تفعيل إمكانية الوصول لفتح الإشعارات.", "يلزم تفعيل إمكانية الوصول.", false);
            }
            pcs.performGlobalAction(AccessibilityService.GLOBAL_ACTION_NOTIFICATIONS);
            return ActionResult.ok("🔔 فتحت لوحة الإشعارات.", "فتحت الإشعارات.", true);
        }
        if (f.matches("(افتح |اعرض )?(ال)?اعدادات السريعه")) {
            PhoneControlService pcs = PhoneControlService.on;
            if (pcs != null) {
                pcs.performGlobalAction(AccessibilityService.GLOBAL_ACTION_QUICK_SETTINGS);
                return ActionResult.ok("⚙️ فتحت الإعدادات السريعة.", "فتحت الإعدادات السريعة.", true);
            }
        }
        if (f.matches("(افتح |اعرض )?التطبيقات (الاخيره|المفتوحه)")) {
            PhoneControlService pcs = PhoneControlService.on;
            if (pcs != null) {
                pcs.performGlobalAction(AccessibilityService.GLOBAL_ACTION_RECENTS);
                return ActionResult.ok("📱 عرضت التطبيقات الأخيرة.", "عرضت التطبيقات الأخيرة.", true);
            }
        }

        // 6. Specific Settings Pages
        if (f.matches("(افتح |شغل )?(اعدادات )?(ال)?(واي فاي|وايفاي| الشبكه|wifi)")) {
            return openSettingsIntent(context, Settings.ACTION_WIFI_SETTINGS, "إعدادات الواي فاي");
        }
        if (f.matches("(افتح |شغل )?(اعدادات )?(ال)?(بلوتوث|bluetooth)")) {
            return openSettingsIntent(context, Settings.ACTION_BLUETOOTH_SETTINGS, "إعدادات البلوتوث");
        }
        if (f.matches("(افتح )?اعدادات الصوت")) {
            return openSettingsIntent(context, Settings.ACTION_SOUND_SETTINGS, "إعدادات الصوت");
        }
        if (f.matches("(افتح )?اعدادات الشاشه")) {
            return openSettingsIntent(context, Settings.ACTION_DISPLAY_SETTINGS, "إعدادات الشاشة");
        }
        if (f.matches("(افتح )?اعدادات البطاريه")) {
            return openSettingsIntent(context, Intent.ACTION_POWER_USAGE_SUMMARY, "إعدادات البطارية");
        }
        if (f.matches("(افتح )?(اعدادات )?امكانيه الوصول")) {
            return openSettingsIntent(context, Settings.ACTION_ACCESSIBILITY_SETTINGS, "إعدادات إمكانية الوصول");
        }

        // 7. Single-step App Launch ("افتح <اسم التطبيق>" / "شغل <اسم التطبيق>")
        String targetApp = extractAppTarget(f);
        if (targetApp != null && !targetApp.isEmpty()) {
            ActionResult opened = openAppByName(context, targetApp);
            if (opened.handled) return opened;
        }

        return ActionResult.notHandled();
    }

    private static String extractAppTarget(String folded) {
        String[] verbs = new String[] {"افتحلي تطبيق ", "افتح تطبيق ", "شغل تطبيق ", "افتحلي برنامج ", "افتح برنامج ",
                "افتحلي ", "شغلي ", "افتح ", "شغل ", "open ", "launch "};
        for (String v : verbs) {
            if (folded.startsWith(v)) {
                String rest = folded.substring(v.length()).trim();
                // Do not treat generic object phrases as app names
                if (rest.isEmpty() || rest.startsWith("رابط") || rest.startsWith("صفحه")
                        || rest.startsWith("ملف") || rest.startsWith("محادثه") || rest.startsWith("رساله")
                        || rest.startsWith("فيديو") || rest.startsWith("صوره") || rest.startsWith("موقع")) {
                    return null;
                }
                return rest;
            }
        }
        return null;
    }

    private static ActionResult openAppByName(Context context, String rawTarget) {
        PackageManager pm = context.getPackageManager();
        String target = TextFold.fold(rawTarget);
        String targetNoAl = target.startsWith("ال") && target.length() > 2 ? target.substring(2) : target;

        // 1. Check built-in Arabic/English alias table
        for (String candidateKey : new String[] {target, targetNoAl, "ال" + targetNoAl}) {
            String[] pkgs = KNOWN_APPS.get(candidateKey);
            if (pkgs != null) {
                for (String pkg : pkgs) {
                    Intent launch = pm.getLaunchIntentForPackage(pkg);
                    if (launch != null) {
                        launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                        context.startActivity(launch);
                        return ActionResult.ok(
                                "✓ فتحت " + rawTarget + ". احكيلي إذا بدك أعمل شي بداخله.",
                                "فتحت " + rawTarget,
                                true);
                    }
                }
            }
        }

        // 2. Search all installed launcher apps dynamically by Arabic/English label
        Intent mainIntent = new Intent(Intent.ACTION_MAIN, null);
        mainIntent.addCategory(Intent.CATEGORY_LAUNCHER);
        List<ResolveInfo> apps = pm.queryIntentActivities(mainIntent, 0);
        ResolveInfo bestMatch = null;
        String bestLabel = "";
        int bestScore = 0;

        for (ResolveInfo info : apps) {
            if (info.activityInfo == null) continue;
            CharSequence labelCs = info.loadLabel(pm);
            String label = labelCs == null ? "" : labelCs.toString().trim();
            String foldedLabel = TextFold.fold(label);
            String foldedLabelNoAl = foldedLabel.startsWith("ال") && foldedLabel.length() > 2
                    ? foldedLabel.substring(2) : foldedLabel;
            String pkg = info.activityInfo.packageName;

            int score = 0;
            if (foldedLabel.equals(target) || foldedLabelNoAl.equals(targetNoAl)) {
                score = 100;
            } else if (foldedLabel.startsWith(target) || foldedLabelNoAl.startsWith(targetNoAl)) {
                score = 80;
            } else if (target.length() >= 3 && (foldedLabel.contains(target) || pkg.toLowerCase(Locale.ROOT).contains(target))) {
                score = 60;
            }
            if (score > bestScore) {
                bestScore = score;
                bestMatch = info;
                bestLabel = label.isEmpty() ? rawTarget : label;
            }
        }

        if (bestMatch != null && bestScore >= 60) {
            Intent launch = pm.getLaunchIntentForPackage(bestMatch.activityInfo.packageName);
            if (launch != null) {
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                context.startActivity(launch);
                return ActionResult.ok(
                        "✓ فتحت تطبيق " + bestLabel + ". احكيلي إذا بدك أكمل مهمة بداخله.",
                        "فتحت " + bestLabel,
                        true);
            }
        }

        return ActionResult.notHandled();
    }

    private static ActionResult setTorch(Context context, boolean enable) {
        try {
            CameraManager cm = (CameraManager) context.getSystemService(Context.CAMERA_SERVICE);
            if (cm == null) return ActionResult.ok("⚠️ الكاميرا غير متاحة.", "الكاميرا غير متاحة.", false);
            for (String id : cm.getCameraIdList()) {
                CameraCharacteristics chars = cm.getCameraCharacteristics(id);
                Boolean hasFlash = chars.get(CameraCharacteristics.FLASH_INFO_AVAILABLE);
                if (Boolean.TRUE.equals(hasFlash)) {
                    cm.setTorchMode(id, enable);
                    String msg = enable ? "شغّلت الفلاش." : "طفيت الفلاش.";
                    return ActionResult.ok((enable ? "🔦 " : "🌑 ") + msg, msg, false);
                }
            }
            return ActionResult.ok("⚠️ هذا الجهاز لا يحتوي على فلاش.", "الجهاز لا يحتوي على فلاش.", false);
        } catch (Exception e) {
            return ActionResult.ok("⚠️ تعذّر التحكم بالفلاش: " + e.getClass().getSimpleName(), "تعذر التحكم بالفلاش.", false);
        }
    }

    private static ActionResult adjustVolume(Context context, int deltaSteps, boolean mute, boolean unmute) {
        try {
            AudioManager am = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
            if (am == null) return ActionResult.notHandled();
            int stream = AudioManager.STREAM_MUSIC;
            int max = am.getStreamMaxVolume(stream);
            int cur = am.getStreamVolume(stream);
            int next = cur;
            if (mute) {
                next = 0;
            } else if (unmute) {
                next = Math.max(cur, Math.round(max * 0.6f));
            } else {
                next = Math.max(0, Math.min(max, cur + deltaSteps));
            }
            am.setStreamVolume(stream, next, AudioManager.FLAG_SHOW_UI);
            int pct = max > 0 ? Math.round((next * 100f) / max) : 0;
            String spoken = mute ? "كتمت الصوت."
                    : (deltaSteps > 0 ? "رفعت الصوت إلى " + pct + " بالمئة."
                    : (deltaSteps < 0 ? "خفضت الصوت إلى " + pct + " بالمئة."
                    : "شغلت الصوت على " + pct + " بالمئة."));
            return ActionResult.ok("🔊 " + spoken, spoken, false);
        } catch (Exception e) {
            return ActionResult.ok("⚠️ تعذّر تغيير مستوى الصوت.", "تعذر تغيير الصوت.", false);
        }
    }

    private static ActionResult getBatteryStatus(Context context) {
        try {
            Intent battery = context.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
            if (battery == null) return ActionResult.notHandled();
            int level = battery.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
            int scale = battery.getIntExtra(BatteryManager.EXTRA_SCALE, -1);
            int status = battery.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
            boolean charging = status == BatteryManager.BATTERY_STATUS_CHARGING
                    || status == BatteryManager.BATTERY_STATUS_FULL;
            int pct = (level >= 0 && scale > 0) ? Math.round((level * 100f) / scale) : -1;
            if (pct < 0) return ActionResult.notHandled();
            String stateText = charging ? "والهاتف موصول بالشاحن" : "والهاتف غير موصول بالشاحن";
            String msg = "نسبة البطارية الآن " + pct + "% " + stateText + ".";
            String spoken = "نسبة البطارية الآن " + pct + " بالمئة، " + stateText + ".";
            return ActionResult.ok("🔋 " + msg, spoken, false);
        } catch (Exception e) {
            return ActionResult.notHandled();
        }
    }

    // ------------------------------------------------------------------ personal assistant

    static ActionResult runAssistant(Context context, AssistantCommands.Command c) {
        try {
            switch (c.kind) {
                case ALARM: return setAlarm(context, c.hour, c.minute, c.text);
                case TIMER: return setTimer(context, c.seconds, c.text);
                case REMINDER: return remind(context, c);
                case CALL: return call(context, c);
                case SMS: return sms(context, c);
                case WHATSAPP: return whatsapp(context, c);
                case NAVIGATE: return navigate(context, c.target);
                case WEB_SEARCH: return webSearch(context, c.text);
                case YOUTUBE: return youtube(context, c.text);
                case SCREENSHOT: return globalAction(context, 9 /* GLOBAL_ACTION_TAKE_SCREENSHOT */, "📸 أخذت لقطة شاشة.", "أخذت لقطة شاشة.");
                case LOCK: return globalAction(context, 8 /* GLOBAL_ACTION_LOCK_SCREEN */, "🔒 قفلت الشاشة.", "قفلت الشاشة.");
                default: return ActionResult.notHandled();
            }
        } catch (Exception e) {
            // no app on this phone takes the request (no clock app, no maps): the agent can still try another way
            return ActionResult.notHandled();
        }
    }

    private static void start(Context context, Intent intent) {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        context.startActivity(intent);
    }

    private static ActionResult setAlarm(Context context, int hour, int minute, String label) {
        Intent i = new Intent(AlarmClock.ACTION_SET_ALARM)
                .putExtra(AlarmClock.EXTRA_HOUR, hour)
                .putExtra(AlarmClock.EXTRA_MINUTES, minute)
                .putExtra(AlarmClock.EXTRA_SKIP_UI, true)
                .putExtra(AlarmClock.EXTRA_MESSAGE, label == null || label.isEmpty() ? "MusabAI" : label);
        start(context, i);
        String when = AssistantCommands.clockText(hour, minute, true);
        String what = label == null || label.isEmpty() ? "" : " (" + label + ")";
        return ActionResult.ok("⏰ ضبطت منبه الساعة " + when + what + ".", "ضبطت منبه الساعة " + when, false);
    }

    private static ActionResult setTimer(Context context, int seconds, String label) {
        Intent i = new Intent(AlarmClock.ACTION_SET_TIMER)
                .putExtra(AlarmClock.EXTRA_LENGTH, seconds)
                .putExtra(AlarmClock.EXTRA_SKIP_UI, true);
        if (label != null && !label.isEmpty()) i.putExtra(AlarmClock.EXTRA_MESSAGE, label);
        start(context, i);
        String len = AssistantCommands.durationText(seconds);
        if (label != null && !label.isEmpty()) {
            return ActionResult.ok("⏳ رح ذكرك بعد " + len + ": " + label, "رح ذكرك بعد " + len, false);
        }
        return ActionResult.ok("⏳ شغلت مؤقت " + len + ".", "شغلت مؤقت " + len, false);
    }

    /**
     * "In 10 minutes": a labelled timer. "At 9 tonight": a labelled alarm. Another day, or no time said: a calendar
     * event filled in for the person to save (the calendar app keeps it and reminds them).
     */
    private static ActionResult remind(Context context, AssistantCommands.Command c) {
        if (c.seconds > 0) return setTimer(context, c.seconds, c.text);
        if (c.hour >= 0 && c.dayOffset == 0) return setAlarm(context, c.hour, c.minute, c.text);
        Calendar when = Calendar.getInstance();
        when.add(Calendar.DAY_OF_YEAR, c.dayOffset);
        if (c.hour >= 0) {
            when.set(Calendar.HOUR_OF_DAY, c.hour);
            when.set(Calendar.MINUTE, c.minute);
        } else if (c.dayOffset > 0) {
            when.set(Calendar.HOUR_OF_DAY, 9);
            when.set(Calendar.MINUTE, 0);
        } else {
            when.add(Calendar.HOUR_OF_DAY, 1);
            when.set(Calendar.MINUTE, 0);
        }
        when.set(Calendar.SECOND, 0);
        long begin = when.getTimeInMillis();
        Intent i = new Intent(Intent.ACTION_INSERT)
                .setData(CalendarContract.Events.CONTENT_URI)
                .putExtra(CalendarContract.Events.TITLE, c.text)
                .putExtra(CalendarContract.EXTRA_EVENT_BEGIN_TIME, begin)
                .putExtra(CalendarContract.EXTRA_EVENT_END_TIME, begin + 30 * 60 * 1000L)
                .putExtra(CalendarContract.Events.HAS_ALARM, 1);
        start(context, i);
        String day = c.dayOffset == 1 ? "بكرا" : c.dayOffset == 2 ? "بعد بكرا" : "اليوم";
        String time = AssistantCommands.clockText(when.get(Calendar.HOUR_OF_DAY), when.get(Calendar.MINUTE), true);
        return ActionResult.ok("📅 جهزت تذكير «" + c.text + "» " + day + " " + time + " — اضغط حفظ بالتقويم.",
                "جهزت التذكير، اضغط حفظ", true);
    }

    /** The number to use: as said, or the phone number of the contact with that name (when contacts may be read). */
    private static String numberFor(Context context, String target, String alt) {
        if (target == null || target.isEmpty()) return null;
        if (target.matches("\\+?\\d{3,15}")) return target;
        if (context.checkSelfPermission(Manifest.permission.READ_CONTACTS) != PackageManager.PERMISSION_GRANTED) {
            return null;
        }
        for (String name : new String[] {target, alt}) {
            if (name == null || name.isEmpty()) continue;
            String found = lookupContact(context, name);
            if (found != null) return found;
        }
        return null;
    }

    private static String lookupContact(Context context, String name) {
        String wanted = TextFold.fold(name);
        String best = null;
        int bestScore = 0;
        try (Cursor cur = context.getContentResolver().query(
                ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
                new String[] {ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER},
                null, null, null)) {
            if (cur == null) return null;
            while (cur.moveToNext()) {
                String display = TextFold.fold(cur.getString(0));
                String number = cur.getString(1);
                if (display.isEmpty() || number == null) continue;
                int score = display.equals(wanted) ? 100
                        : display.startsWith(wanted + " ") ? 80
                        : (" " + display + " ").contains(" " + wanted + " ") ? 70
                        : wanted.length() >= 3 && display.contains(wanted) ? 40 : 0;
                if (score > bestScore) {
                    bestScore = score;
                    best = number;
                }
            }
        } catch (Exception ignored) {
            return null;
        }
        return best == null ? null : AssistantCommands.phoneDigits(best);
    }

    private static ActionResult call(Context context, AssistantCommands.Command c) {
        String number = numberFor(context, c.target, c.targetAlt);
        if (number == null) return ActionResult.notHandled();
        start(context, new Intent(Intent.ACTION_DIAL, Uri.parse("tel:" + Uri.encode(number))));
        String who = c.target.matches("\\+?\\d{3,15}") ? number : (c.targetAlt.isEmpty() ? c.target : c.targetAlt);
        return ActionResult.ok("📞 جهزت الاتصال بـ " + who + " — اضغط زر الاتصال.", "جهزت الاتصال، اضغط اتصال", true);
    }

    private static ActionResult sms(Context context, AssistantCommands.Command c) {
        String number = numberFor(context, c.target, c.targetAlt);
        if (number == null) return ActionResult.notHandled();
        Intent i = new Intent(Intent.ACTION_SENDTO, Uri.parse("smsto:" + Uri.encode(number)));
        if (!c.text.isEmpty()) i.putExtra("sms_body", c.text);
        start(context, i);
        return ActionResult.ok("💬 كتبت الرسالة — راجعها واضغط إرسال.", "كتبت الرسالة، اضغط إرسال", true);
    }

    /** WhatsApp needs the number with its country code: a saved contact usually has it, "07..." alone does not. */
    private static ActionResult whatsapp(Context context, AssistantCommands.Command c) {
        String number = numberFor(context, c.target, c.targetAlt);
        if (number == null) return ActionResult.notHandled();
        String digits = number.startsWith("+") ? number.substring(1) : number.startsWith("00") ? number.substring(2) : null;
        if (digits == null) return ActionResult.notHandled();
        Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse("https://wa.me/" + digits
                + (c.text.isEmpty() ? "" : "?text=" + Uri.encode(c.text))));
        for (String pkg : new String[] {"com.whatsapp", "com.whatsapp.w4b"}) {
            if (context.getPackageManager().getLaunchIntentForPackage(pkg) != null) {
                i.setPackage(pkg);
                break;
            }
        }
        start(context, i);
        return ActionResult.ok("💬 فتحت محادثة واتساب وكتبت الرسالة — اضغط إرسال.", "جهزت رسالة الواتساب", true);
    }

    private static ActionResult navigate(Context context, String place) {
        Intent nav = new Intent(Intent.ACTION_VIEW, Uri.parse("google.navigation:q=" + Uri.encode(place)));
        if (nav.resolveActivity(context.getPackageManager()) == null) {
            nav = new Intent(Intent.ACTION_VIEW, Uri.parse("geo:0,0?q=" + Uri.encode(place)));
        }
        start(context, nav);
        return ActionResult.ok("🗺️ فتحت الطريق إلى " + place + ".", "فتحت الطريق إلى " + place, true);
    }

    /**
     * The results page itself, in Chrome when it is there: a web-search intent asks which app to use on a phone with
     * several, and Chrome's own search box then waits for another press.
     */
    private static ActionResult webSearch(Context context, String query) {
        Intent i = new Intent(Intent.ACTION_VIEW, Uri.parse("https://www.google.com/search?q=" + Uri.encode(query)));
        if (context.getPackageManager().getLaunchIntentForPackage("com.android.chrome") != null) {
            i.setPackage("com.android.chrome");
        }
        start(context, i);
        return ActionResult.ok("🔎 بحثت عن: " + query, "بحثت عن " + query, true);
    }

    private static ActionResult youtube(Context context, String query) {
        Intent i = new Intent(Intent.ACTION_VIEW,
                Uri.parse("https://www.youtube.com/results?search_query=" + Uri.encode(query)));
        if (context.getPackageManager().getLaunchIntentForPackage("com.google.android.youtube") != null) {
            i.setPackage("com.google.android.youtube");
        }
        start(context, i);
        return ActionResult.ok("▶️ فتحت يوتيوب على: " + query, "فتحت يوتيوب على " + query, true);
    }

    /** A screenshot waits for the floating panel to fold away, so it is not in the picture. */
    private static ActionResult globalAction(Context context, int action, String display, String spoken) {
        PhoneControlService pcs = PhoneControlService.on;
        if (pcs == null || Build.VERSION.SDK_INT < 28) {
            return ActionResult.ok("⚠️ يلزم تفعيل إمكانية الوصول لـ MusabAI أولاً.", "يلزم تفعيل إمكانية الوصول.", false);
        }
        new Handler(Looper.getMainLooper()).postDelayed(() -> pcs.performGlobalAction(action), 700);
        return ActionResult.ok(display, spoken, true);
    }

    private static ActionResult openSettingsIntent(Context context, String action, String label) {
        try {
            Intent i = new Intent(action).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            context.startActivity(i);
            return ActionResult.ok("⚙️ فتحت " + label + ".", "فتحت " + label, true);
        } catch (Exception e) {
            return ActionResult.ok("⚠️ تعذّر فتح " + label + ".", "تعذر فتح الإعدادات.", false);
        }
    }

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
        if (text.matches("(افتح|شغل|افتحلي|open|launch) (الاعدادات|الضبط|settings)"))
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
