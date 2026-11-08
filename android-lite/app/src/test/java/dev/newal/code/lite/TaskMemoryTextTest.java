package dev.newal.code.lite;

import static org.junit.Assert.assertEquals;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import org.junit.Test;

/** TaskMemoryText makes the same search terms as tools/task_memory/textnorm.py (these expectations are its output). */
public class TaskMemoryTextTest {
    private static void check(String text, String[][] want) {
        List<List<String>> expected = new ArrayList<>();
        for (String[] w : want) {
            expected.add(Arrays.asList(w));
        }
        assertEquals(text, expected, TaskMemoryText.query(text));
    }

    @Test
    public void sameTermsAsTheLibraryBuilder() {
        check("افتح فيسبوك وانشر منشور يقول مرحبا يا جماعة", new String[][] {{"افتح"}, {"فيسبوك", "يسبوك"}, {"وانشر", "انشر"}, {"منشور"}, {"يقول"}, {"مرحبا"}, {"جماعه", "جماع"}});
        check("Open the Zoho Meet app, view the scheduled meetings", new String[][] {{"open"}, {"zoho"}, {"meet"}, {"view"}, {"schedul"}, {"meeting"}});
        check("ابعتلي رسالة واتساب لأحمد", new String[][] {{"ابعتلي", "ابعت"}, {"رساله", "رسال"}, {"واتساب", "اتساب"}, {"لاحمد", "احمد"}});
        check("الإعدادات", new String[][] {{"الاعدادات", "الاعداد", "اعدادات", "اعداد"}});
        check("منشورات", new String[][] {{"منشورات", "منشور"}});
        check("Searching for posts, photos & classes!", new String[][] {{"search"}, {"post"}, {"photo"}, {"class"}});
        check("شو الطقس اليوم؟ ٣ أيام", new String[][] {{"الطقس", "طقس"}, {"اليوم", "يوم"}, {"ايام"}});
        check("ابحث في يوتيوب عن وصفة كبسة", new String[][] {{"ابحث"}, {"يوتيوب"}, {"وصفه", "وصف", "صفه"}, {"كبسه", "كبس"}});
        check("The kids would love this", new String[][] {{"kid"}, {"love"}});
    }

    @Test
    public void arabicFormsWithoutParticlesAndEndings() {
        assertEquals(Arrays.asList("وانشر", "انشر"), TaskMemoryText.variantsAr("وانشر"));
        assertEquals(Arrays.asList("فيسبوك", "يسبوك"), TaskMemoryText.variantsAr("فيسبوك"));
        assertEquals("post", TaskMemoryText.stemEn("posts"));
        assertEquals("search", TaskMemoryText.stemEn("searching"));
    }
}
