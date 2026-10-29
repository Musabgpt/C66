package dev.newal.code.lite;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertNull;

import dev.newal.code.lite.AssistantCommands.Command;
import dev.newal.code.lite.AssistantCommands.Kind;

import org.junit.Test;

public class AssistantCommandsTest {
    /** Parsed as if it were 22:00, so "7" without a morning/evening word is the next 7 (the morning). */
    private static Command at22(String phrase) {
        return AssistantCommands.parse(phrase, 22, 0);
    }

    private static Command at14(String phrase) {
        return AssistantCommands.parse(phrase, 14, 0);
    }

    private static void alarm(String phrase, int hour, int minute) {
        Command c = at22(phrase);
        assertNotNull(phrase, c);
        assertEquals(phrase, Kind.ALARM, c.kind);
        assertEquals(phrase + " hour", hour, c.hour);
        assertEquals(phrase + " minute", minute, c.minute);
    }

    private static void timer(String phrase, int seconds) {
        Command c = at22(phrase);
        assertNotNull(phrase, c);
        assertEquals(phrase, Kind.TIMER, c.kind);
        assertEquals(phrase + " seconds", seconds, c.seconds);
    }

    @Test public void alarms() {
        alarm("صحيني الساعة 7", 7, 0);
        alarm("اضبط منبه الساعة 6:30 الصبح", 6, 30);
        alarm("حطلي منبه ع 7 ونص", 7, 30);
        alarm("منبه الساعة ٨ وربع", 8, 15);
        alarm("صحيني الساعة 7 الا ربع", 6, 45);
        alarm("اضبط منبه الساعة 9 المسا", 21, 0);
        alarm("set an alarm at 7:05 am", 7, 5);
        alarm("wake me up at 6 am", 6, 0);
        alarm("صحيني الساعة السابعة", 7, 0);
        alarm("منبه الساعة 12 الظهر", 12, 0);
        alarm("فيقني الساعة 5 و 20 دقيقة", 5, 20);
        Command afternoon = at14("اضبط منبه الساعة 5");
        assertEquals(17, afternoon.hour);
    }

    @Test public void alarmWithoutTimeOrAboutTheAppIsNotTaken() {
        assertNull(at22("اضبط منبه"));
        assertNull(at22("افتح المنبه"));
        assertNull(at22("افتح اعدادات المنبه"));
    }

    @Test public void timers() {
        timer("مؤقت 5 دقائق", 300);
        timer("شغل مؤقت لمدة ١٠ دقايق", 600);
        timer("تايمر نص ساعة", 1800);
        timer("حط مؤقت ربع ساعة", 900);
        timer("مؤقت دقيقتين", 120);
        timer("مؤقت ساعة ونص", 5400);
        timer("timer 30 seconds", 30);
        timer("set a timer for 2 hours", 7200);
        timer("نبهني بعد 20 دقيقة", 1200);
        assertNull(at22("شغل مؤقت"));
    }

    @Test public void reminders() {
        Command in = at22("ذكرني بعد 10 دقايق اطفي الفرن");
        assertEquals(Kind.REMINDER, in.kind);
        assertEquals(600, in.seconds);
        assertEquals("اطفي الفرن", in.text);

        Command tonight = at14("ذكرني الساعة 9 المسا بالدوا");
        assertEquals(Kind.REMINDER, tonight.kind);
        assertEquals(21, tonight.hour);
        assertEquals(0, tonight.dayOffset);
        assertEquals("الدوا", tonight.text);

        Command tomorrow = at22("ذكرني بكرا الساعة 10 الصبح باجتماع الشركة");
        assertEquals(Kind.REMINDER, tomorrow.kind);
        assertEquals(1, tomorrow.dayOffset);
        assertEquals(10, tomorrow.hour);
        assertEquals("اجتماع الشركة", tomorrow.text);

        Command later = at22("ذكرني بعد بكرا اتصل بالدكتور");
        assertEquals(2, later.dayOffset);
        assertEquals(-1, later.hour);
        assertEquals("اتصل بالدكتور", later.text);

        Command en = at22("remind me to call mom in 15 minutes");
        assertEquals(Kind.REMINDER, en.kind);
        assertEquals(900, en.seconds);
        assertEquals("call mom", en.text);
    }

    @Test public void calls() {
        Command n = at22("اتصل على 0790 123 4567");
        assertEquals(Kind.CALL, n.kind);
        assertEquals("07901234567", n.target);
        Command name = at22("اتصل بأحمد");
        assertEquals(Kind.CALL, name.kind);
        assertEquals("بأحمد", name.target);
        assertEquals("أحمد", name.targetAlt);
        Command sep = at22("اتصل ب امي");
        assertEquals("امي", sep.target);
        assertEquals(Kind.CALL, at22("call +44 7700 900123").kind);
        assertNull(at22("اتصل"));
        assertNull(at22("اتصل باحمد على الواتساب"));
    }

    @Test public void messages() {
        Command sms = at22("ابعث رسالة لأحمد قله انا تأخرت شوي");
        assertEquals(Kind.SMS, sms.kind);
        assertEquals("لأحمد", sms.target);
        assertEquals("أحمد", sms.targetAlt);
        assertEquals("انا تأخرت شوي", sms.text);

        Command num = at22("ارسل رسالة الى 07901234567 نصها وصلت");
        assertEquals("07901234567", num.target);
        assertEquals("وصلت", num.text);

        Command wa = at22("ابعث واتساب لسارة: بكرا منلتقي");
        assertEquals(Kind.WHATSAPP, wa.kind);
        assertEquals("لسارة", wa.target);
        assertEquals("بكرا منلتقي", wa.text);

        Command waOn = at22("ابعث رسالة على الواتساب لخالد قله مبروك");
        assertEquals(Kind.WHATSAPP, waOn.kind);
        assertEquals("لخالد", waOn.target);
        assertEquals("مبروك", waOn.text);

        assertNull(at22("ابعث الملف"));
    }

    @Test public void directionsAndSearch() {
        Command nav = at22("خذني على مطار هيثرو");
        assertEquals(Kind.NAVIGATE, nav.kind);
        assertEquals("مطار هيثرو", nav.target);
        assertEquals("أقرب صيدلية", at22("دلني على أقرب صيدلية").target);
        assertEquals("Birmingham New Street", at22("take me to Birmingham New Street").target);
        assertEquals("السوق", at22("كيف بروح على السوق").target);

        Command web = at22("ابحث عن سعر الذهب اليوم");
        assertEquals(Kind.WEB_SEARCH, web.kind);
        assertEquals("سعر الذهب اليوم", web.text);
        assertEquals("best pizza near me", at22("search for best pizza near me").text);
        assertNull(at22("دور على الزر الأزرق"));

        Command yt = at22("شغل فيروز على اليوتيوب");
        assertEquals(Kind.YOUTUBE, yt.kind);
        assertEquals("فيروز", yt.text);
        assertEquals("طبخ المقلوبة", at22("ابحث في يوتيوب عن طبخ المقلوبة").text);
        assertNull(at22("افتح يوتيوب"));
    }

    @Test public void screenAndLock() {
        assertEquals(Kind.SCREENSHOT, at22("خذ لقطة شاشة").kind);
        assertEquals(Kind.SCREENSHOT, at22("سكرين شوت").kind);
        assertEquals(Kind.LOCK, at22("اقفل الشاشة").kind);
        assertEquals(Kind.LOCK, at22("lock the screen").kind);
    }

    @Test public void foldedWordsStillMatch() {
        alarm("حط منبه على 7", 7, 0);
        assertEquals("برج خليفة", at22("افتح خرائط جوجل على برج خليفة").target);
        Command forget = at22("لا تخليني انسى ادفع الفاتورة بكرا");
        assertEquals(Kind.REMINDER, forget.kind);
        assertEquals(1, forget.dayOffset);
        assertEquals("ادفع الفاتورة", forget.text);
    }

    @Test public void ordinaryRequestsAreLeftToTheAgent() {
        assertNull(at22("اكتب نص عن الذكاء الاصطناعي"));
        assertNull(at22("دق الساعة 5"));
        assertNull(at22("افتح يوتيوب وشغل فيروز"));
        assertNull(at22("افتح واتساب"));
        assertNull(at22("اكتب كود بايثون يرتب قائمة"));
        assertNull(at22("شو الطقس اليوم"));
        assertNull(at22("مرحبا كيفك"));
        assertNull(at22(""));
    }

    @Test public void spokenTimes() {
        assertEquals("7:30 صباحاً", AssistantCommands.clockText(7, 30, true));
        assertEquals("9:00 مساءً", AssistantCommands.clockText(21, 0, true));
        assertEquals("5 دقائق", AssistantCommands.durationText(300));
        assertEquals("ساعة و30 دقيقة", AssistantCommands.durationText(5400));
    }
}
