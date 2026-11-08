package dev.newal.code.lite;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;

import java.util.Arrays;
import java.util.List;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.BeforeClass;
import org.junit.Test;

/**
 * The task memory on the library the APK ships: requests people say find the procedure that does them, with the
 * values they give, across the four sources; and the library is sorted into categories with every source in it.
 */
public class TaskMemoryTest {
    private static TaskMemory tm;

    @BeforeClass
    public static void open() throws Exception {
        tm = new TaskMemory(JdbcTaskMemoryStore.shipped());
    }

    private static TaskMemory.Match best(String request) throws Exception {
        TaskMemory.Match m = tm.match(request);
        assertNotNull("nothing for " + request, m);
        return m;
    }

    private static void expect(String request, String ref) throws Exception {
        TaskMemory.Match m = best(request);
        assertEquals(request, ref, m.proc.ref());
        assertTrue(request + " should be confident (" + m.score + ")", m.confident);
    }

    @Test
    public void facebookPostFromTheUsersExample() throws Exception {
        expect("افتح فيسبوك وانشر منشور", "cur.facebook.post");
        TaskMemory.Match m = best("انشر منشور على الفيس بوك يقول صباح الخير يا جماعة");
        assertEquals("cur.facebook.post", m.proc.ref());
        assertEquals(Arrays.asList("صباح الخير يا جماعة"), m.values);
        assertTrue(m.proc.exec);
        JSONArray steps = m.proc.steps();
        assertEquals("open_app", steps.getJSONObject(0).getString("a"));
        assertEquals("com.facebook.katana", steps.getJSONObject(0).getString("pkg"));
        assertTrue(steps.getJSONObject(steps.length() - 1).optBoolean("final"));
        assertEquals("{1}", m.proc.check());
        String note = TaskMemory.note(m);
        assertTrue(note, note.contains("tm:cur.facebook.post"));
        assertTrue(note, note.contains("صباح الخير يا جماعة"));
        assertTrue(note, note.contains("phone_routine"));
    }

    @Test
    public void messagesTakeTheContactAndTheText() throws Exception {
        TaskMemory.Match m = best("ابعث رسالة واتساب لأحمد يقول وينك");
        assertEquals("cur.whatsapp.send", m.proc.ref());
        assertEquals(Arrays.asList("أحمد", "وينك"), m.values);
        m = best("ابعث ايميل لـ ali@example.com بعنوان اجتماع يقول نلتقي بكرا");
        assertEquals("cur.gmail.compose", m.proc.ref());
        assertEquals(Arrays.asList("ali@example.com", "اجتماع", "نلتقي بكرا"), m.values);
        assertTrue(m.confident);
    }

    @Test
    public void searchesTakeWhatToLookFor() throws Exception {
        TaskMemory.Match m = best("ابحث في يوتيوب عن وصفة كبسة");
        assertEquals("cur.youtube.search", m.proc.ref());
        assertEquals(Arrays.asList("وصفة كبسة"), m.values);
        m = best("ابحث في امازون عن سماعات بلوتوث");
        assertEquals("cur.amazon.search", m.proc.ref());
        assertEquals(Arrays.asList("سماعات بلوتوث"), m.values);
        m = best("نزل تطبيق سناب شات");
        assertEquals("cur.playstore.install", m.proc.ref());
        assertEquals(Arrays.asList("سناب شات"), m.values);
    }

    @Test
    public void datasetProceduresAreFoundInEnglish() throws Exception {
        expect("search for welsh cakes recipe in kitchen stories", "ac.60");
        expect("view the scheduled meetings in zoho meet", "ac.0");
        TaskMemory.Match m = best("find a vegan black bean soup recipe");
        assertEquals("insta150k", m.proc.src);
        assertFalse(m.proc.exec);
        assertTrue(TaskMemory.describe(m.proc).startsWith("open_url https://"));
    }

    @Test
    public void theAppTheRequestNames() throws Exception {
        assertEquals("Facebook", tm.detectApp("انشر عالفيس").name);
        assertEquals("WhatsApp", tm.detectApp("ابعتلو بالواتس").name);
        assertEquals("Zoho Meeting", tm.detectApp("open the zoho meet app").name);
        assertEquals(null, tm.detectApp("find a vegan black bean soup recipe"));
    }

    @Test
    public void everySourceAndCategoryIsInTheLibrary() throws Exception {
        int total = 0;
        for (TaskMemory.Source s : tm.store().sources()) {
            if (!s.id.equals("learned")) {
                assertTrue(s.id + " is empty", s.n > 0);
                total += s.n;
            }
        }
        assertTrue("the library holds " + total, total > 160_000);
        List<TaskMemory.Category> cats = tm.store().categories();
        assertTrue(cats.size() >= 20);
        int social = 0;
        for (TaskMemory.Category c : cats) {
            if (c.id.equals("social")) {
                social = c.n;
            }
        }
        assertTrue(social > 0);
        assertFalse(tm.store().byCategory("social", "Facebook", 0, 10).isEmpty());
        assertFalse(tm.store().bySource("mind2web", 0, 5).isEmpty());
        TaskMemory.Proc p = tm.store().get("cur.facebook.post");
        JSONObject j = TaskMemory.toJson(p, true);
        assertEquals("tm:cur.facebook.post", j.getString("id"));
        assertTrue(j.getBoolean("executable"));
    }

    @Test
    public void valuesFillTheSteps() {
        assertEquals("https://www.youtube.com/results?search_query=%D9%83%D8%A8%D8%B3%D8%A9",
                TaskRunner.fill("https://www.youtube.com/results?search_query={1}", Arrays.asList("كبسة"), true));
        assertEquals("صباح الخير", TaskRunner.fill("{1}", Arrays.asList("صباح الخير"), false));
        assertEquals("{x}", TaskRunner.fill("{x}", Arrays.asList("a"), false));
    }
}
