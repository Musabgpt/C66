package dev.newal.code.lite;

import static org.junit.Assert.assertEquals;

import dev.newal.code.lite.AssistantCommands.Command;

import java.util.ArrayList;
import java.util.List;

import org.junit.Test;

/**
 * A wider set of real requests, the way the phone sees them (QuickPhoneActions.normalizeCommand first, at 22:00): what
 * each one must become, and ordinary talk that must stay with the agent. One failure lists every phrase that is wrong.
 */
public class AssistantCorpusTest {
    /** phrase, then the expected result: "-" for none (the agent), else "KIND|field=value|..." */
    private static final String[][] CASES = {
        // alarms
        {"صحيني الساعة 6", "ALARM|hour=6|minute=0"},
        {"صحيني ع 6 ونص", "ALARM|hour=6|minute=30"},
        {"فيقني الساعة سبعة الصبح", "ALARM|hour=7|minute=0"},
        {"اضبطلي منبه الساعة 5:45", "ALARM|hour=5|minute=45"},
        {"حط منبه الساعة 10 بالليل", "ALARM|hour=22|minute=0"},
        {"منبه الساعة 4 العصر", "ALARM|hour=16|minute=0"},
        {"بدي منبه الساعة 8 وربع", "ALARM|hour=8|minute=15"},
        {"ممكن تضبطلي منبه الساعة 7", "ALARM|hour=7|minute=0"},
        {"صحيني الساعة ٦:١٥", "ALARM|hour=6|minute=15"},
        {"قومني الساعة 9 الا ربع", "ALARM|hour=8|minute=45"},
        {"set an alarm for 6:30 am", "ALARM|hour=6|minute=30"},
        {"wake me up at 7", "ALARM|hour=7|minute=0"},
        // timers
        {"مؤقت 5 دقايق", "TIMER|seconds=300"},
        {"تايمر 3 دقائق", "TIMER|seconds=180"},
        {"شغلي عداد 30 ثانية", "TIMER|seconds=30"},
        {"مؤقت ساعتين", "TIMER|seconds=7200"},
        {"حط تايمر نص ساعة", "TIMER|seconds=1800"},
        {"نبهني بعد ربع ساعة", "TIMER|seconds=900"},
        {"timer for 90 seconds", "TIMER|seconds=90"},
        {"set a 20 minute timer", "TIMER|seconds=1200"},
        // reminders
        {"ذكرني بعد ساعة اتصل بأمي", "REMINDER|seconds=3600|text=اتصل بأمي"},
        {"ذكرني بكرا الصبح بالاجتماع", "REMINDER|day=1|text=الاجتماع"},
        {"فكرني الساعة 5 المسا اشتري خبز", "REMINDER|hour=17|text=اشتري خبز"},
        {"remind me tomorrow at 9 am to pay rent", "REMINDER|day=1|hour=9|text=pay rent"},
        {"لا تخليني انسى موعد الدكتور بكرا", "REMINDER|day=1|text=موعد الدكتور"},
        {"ذكرني بعد 20 دقيقة طفي الغاز", "REMINDER|seconds=1200|text=طفي الغاز"},
        // calls
        {"اتصل على 0790 111 2222", "CALL|target=07901112222"},
        {"دق على أبوي", "CALL|target=أبوي"},
        {"رن على 07700900123", "CALL|target=07700900123"},
        {"اتصل بـ خالد", "CALL|target=خالد"},
        {"call mom", "CALL|target=mom"},
        // messages
        {"ابعث رسالة لأمي قلها رح اتأخر", "SMS|alt=أمي|text=رح اتأخر"},
        {"ارسل مسج الى 0790 1234567 نصه وصلت بالسلامة", "SMS|target=07901234567|text=وصلت بالسلامة"},
        {"ابعث رسالة لـ 0000 تجربة", "SMS|target=0000|text=تجربة"},
        {"text Sarah I'm outside", "SMS|target=Sarah|text=I'm outside"},
        {"ابعث رسالة لأحمد", "SMS|alt=أحمد|text="},
        {"ابعث واتساب لأحمد قله صباح الخير", "WHATSAPP|alt=أحمد|text=صباح الخير"},
        {"ارسل رسالة واتس لـ +447700900123: مرحبا", "WHATSAPP|target=+447700900123|text=مرحبا"},
        {"send a whatsapp to Ali saying hi", "WHATSAPP|target=Ali|text=hi"},
        // directions
        {"خذني على البيت", "NAVIGATE|target=البيت"},
        {"خذني عالبيت", "NAVIGATE|target=البيت"},
        {"وديني على أقرب محطة بنزين", "NAVIGATE|target=أقرب محطة بنزين"},
        {"دلني ع المطار", "NAVIGATE|target=المطار"},
        {"كيف بروح على مستشفى الملكة اليزابيث", "NAVIGATE|target=مستشفى الملكة اليزابيث"},
        {"navigate to the nearest pharmacy", "NAVIGATE|target=the nearest pharmacy"},
        {"take me home", "NAVIGATE|target=home"},
        // searches
        {"ابحث عن مطاعم قريبة مني", "WEB_SEARCH|text=مطاعم قريبة مني"},
        {"دورلي على سعر الايفون 17", "WEB_SEARCH|text=سعر الايفون 17"},
        {"google best laptops 2026", "WEB_SEARCH|text=best laptops 2026"},
        {"search for flights to Amman", "WEB_SEARCH|text=flights to Amman"},
        {"شغلي اغاني ام كلثوم على يوتيوب", "YOUTUBE|text=اغاني ام كلثوم"},
        {"حطلي فيديو طبخ كبسة عاليوتيوب", "YOUTUBE|text=طبخ كبسة"},
        {"play relaxing music on youtube", "YOUTUBE|text=relaxing music"},
        {"ابحث في يوتيوب عن دروس بايثون", "YOUTUBE|text=دروس بايثون"},
        // screen
        {"صورلي الشاشة", "SCREENSHOT"},
        {"سكرين شوت", "SCREENSHOT"},
        {"take a screenshot", "SCREENSHOT"},
        {"اقفل الجوال", "LOCK"},
        {"سكر الشاشة", "LOCK"},
        // ordinary talk and other work: the agent, never a phone action
        {"مرحبا", "-"},
        {"كيف حالك", "-"},
        {"شو الطقس بكرا", "-"},
        {"اكتب قصة عن قطة", "-"},
        {"اشرحلي شو يعني ذكاء اصطناعي", "-"},
        {"ترجم hello للعربي", "-"},
        {"افتح واتساب", "-"},
        {"افتح المنبه", "-"},
        {"شو المنبهات عندي", "-"},
        {"الغي المنبه", "-"},
        {"كم الساعة هلق", "-"},
        {"اتصل", "-"},
        {"دق الباب", "-"},
        {"ابعث الملف لأحمد", "-"},
        {"اكتب رسالة شكر لمديري", "-"},
        {"ابعث رسالة شكر لمديري", "-"},
        {"ارسل الصورة لسارة", "-"},
        {"ذكرني شو قلتلك امبارح", "-"},
        {"ذكرني اسم الفيلم اللي حكيناه", "-"},
        {"شغل مؤقت الفرن", "-"},
        {"دلني كيف اعمل كيكة", "-"},
        {"دلني على طريقة اعمل كيكة", "-"},
        {"شغل الاغنية", "-"},
        {"اقفل الواتساب", "-"},
        {"حط منبه للصبح", "-"},
        {"صحيني بكير", "-"},
        {"ابعث رسالة", "-"},
        {"دور على الزر الأزرق", "-"},
        {"اكتب كود بايثون يرتب قائمة", "-"},
        {"شو رأيك بفيلم انسبشن", "-"},
        {"لخصلي هالمقال", "-"},
        {"افتح يوتيوب وشغل فيروز", "-"},
        {"what time is it", "-"},
        {"tell me a joke", "-"},
        {"write an email to my boss", "-"},
        // first measured as a held-out set (27/30 before the three fixes it found), kept here since
        {"صحيني بكرا الساعة 5 الفجر", "ALARM|hour=5|minute=0"},
        {"اضبط المنبه على الساعة 6 وعشر دقايق", "ALARM|hour=6|minute=10"},
        {"ذكرني بعد نص ساعة اطلع الغسيل", "REMINDER|seconds=1800|text=اطلع الغسيل"},
        {"مؤقت لمدة 45 دقيقة", "TIMER|seconds=2700"},
        {"حط تايمر 2 دقايق للبيض", "TIMER|seconds=120"},
        {"اتصل على 0044 7700 900 456", "CALL|target=00447700900456"},
        {"دق على ماما", "CALL|target=ماما"},
        {"ابعث رسالة لسامي قله انا جاي", "SMS|alt=سامي|text=انا جاي"},
        {"ابعث واتساب لـ +962790000000 نصها وين صرت", "WHATSAPP|target=+962790000000|text=وين صرت"},
        {"خذني على جامعة برمنغهام", "NAVIGATE|target=جامعة برمنغهام"},
        {"كيف اوصل على محطة القطار", "NAVIGATE|target=محطة القطار"},
        {"ابحث عن وصفة منسف", "WEB_SEARCH|text=وصفة منسف"},
        {"شغل سورة الكهف على اليوتيوب", "YOUTUBE|text=سورة الكهف"},
        {"خذ سكرين شوت", "SCREENSHOT"},
        {"قفل الشاشة", "LOCK"},
        {"remind me in 2 hours to check the oven", "REMINDER|seconds=7200|text=check the oven"},
        {"set alarm 5:30 pm", "ALARM|hour=17|minute=30"},
        {"call 07700900999", "CALL|target=07700900999"},
        {"directions to Bullring", "NAVIGATE|target=Bullring"},
        {"youtube cooking pasta", "YOUTUBE|text=cooking pasta"},
        {"شو يعني منبه بالانجليزي", "-"},
        {"بدي اتعلم برمجة", "-"},
        {"ابعث لي ملخص الاجتماع", "-"},
        {"خذني بعين الاعتبار", "-"},
        {"كم دقيقة بتاخد الطريق للمطار", "-"},
        {"اتصل بالانترنت", "-"},
        {"ذكرني ليش فتحت هالملف", "-"},
        {"سجل ملاحظة اشتري حليب", "-"},
        {"شغل الواي فاي", "-"},
        {"what's the weather in London", "-"},
    };

    private static String describe(Command c) {
        if (c == null) return "-";
        return c.kind + "|hour=" + c.hour + "|minute=" + c.minute + "|seconds=" + c.seconds + "|day=" + c.dayOffset
                + "|text=" + c.text + "|target=" + c.target + "|alt=" + c.targetAlt;
    }

    /** Only the fields the case names are compared. */
    private static boolean matches(Command c, String expected) {
        if (expected.equals("-")) return c == null;
        if (c == null) return false;
        String[] parts = expected.split("\\|", -1);
        if (!c.kind.name().equals(parts[0])) return false;
        for (int i = 1; i < parts.length; i++) {
            String[] kv = parts[i].split("=", 2);
            String want = kv.length > 1 ? kv[1] : "";
            String got;
            switch (kv[0]) {
                case "hour": got = String.valueOf(c.hour); break;
                case "minute": got = String.valueOf(c.minute); break;
                case "seconds": got = String.valueOf(c.seconds); break;
                case "day": got = String.valueOf(c.dayOffset); break;
                case "text": got = c.text; break;
                case "target": got = c.target; break;
                case "alt": got = c.targetAlt.isEmpty() ? c.target : c.targetAlt; break;
                default: return false;
            }
            if (!want.equals(got)) return false;
        }
        return true;
    }

    @Test public void corpus() {
        List<String> wrong = new ArrayList<>();
        int commands = 0;
        int agent = 0;
        for (String[] row : CASES) {
            if (row[1].equals("-")) agent++; else commands++;
            Command c = AssistantCommands.parse(QuickPhoneActions.normalizeCommand(row[0]), 22, 0);
            if (!matches(c, row[1])) wrong.add(row[0] + "  expected " + row[1] + "  got " + describe(c));
        }
        System.out.println("assistant corpus: " + (CASES.length - wrong.size()) + "/" + CASES.length + " right ("
                + commands + " commands, " + agent + " left to the agent)");
        assertEquals("wrong:\n" + String.join("\n", wrong), 0, wrong.size());
    }
}
