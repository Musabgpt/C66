package dev.newal.code.lite;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.List;

import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.BeforeClass;
import org.junit.Test;

/**
 * The phone actions the agent's engine calls (connectors/task_memory.py), on the shipped library and a simulated
 * Facebook: the request gets the note with its values, and memory_run publishes the post.
 */
public class TaskMemoryActionsTest {
    private static TaskMemory memory;

    @BeforeClass
    public static void open() throws Exception {
        memory = new TaskMemory(JdbcTaskMemoryStore.shipped());
    }

    static final class Env implements TaskMemoryActions.Env {
        final TaskRunnerTest.FakeFacebook fb = new TaskRunnerTest.FakeFacebook();
        final List<String> used = new ArrayList<>();

        @Override
        public TaskMemory memory() {
            return memory;
        }

        @Override
        public TaskRunner.Device device() {
            return fb;
        }

        @Override
        public List<TaskMemory.Proc> learned() {
            return new ArrayList<>();
        }

        @Override
        public void used(String ref, boolean done) {
            used.add(ref + (done ? " done" : " not done"));
        }
    }

    private static JSONObject call(Env env, String action, Object... kv) throws Exception {
        JSONObject a = new JSONObject().put("action", action);
        for (int i = 0; i + 1 < kv.length; i += 2) {
            a.put((String) kv[i], kv[i + 1]);
        }
        return TaskMemoryActions.handle(env, a);
    }

    @Test
    public void theRequestGetsTheProcedureAndTheModelRunsIt() throws Exception {
        Env env = new Env();
        JSONObject m = call(env, "memory_match", "text", "افتح الفيس بوك وانشر منشور يقول مساء الخير");
        assertTrue(m.getBoolean("confident"));
        JSONObject match = m.getJSONObject("match");
        assertEquals("tm:cur.facebook.post", match.getString("id"));
        assertEquals("مساء الخير", match.getJSONArray("values").getString(0));
        assertTrue(m.getString("text"), m.getString("text").contains("phone_routine"));

        JSONObject run = call(env, "memory_run", "id", match.getString("id"),
                "values", new JSONArray().put("مساء الخير"));
        assertTrue(run.toString(), run.getBoolean("done"));
        assertEquals("posted", env.fb.state);
        assertTrue(run.getString("screen").contains("مساء الخير"));
        assertEquals("cur.facebook.post done", env.used.get(0));
    }

    @Test
    public void searchGetAndStats() throws Exception {
        Env env = new Env();
        JSONObject s = call(env, "memory_search", "text", "send a whatsapp message", "limit", 3);
        assertTrue(s.getString("text"), s.getString("text").contains("tm:cur.whatsapp.send"));
        JSONObject g = call(env, "memory_get", "id", "tm:ac.60");
        assertEquals("Kitchen Stories", g.getJSONObject("procedure").getString("app"));
        assertTrue(g.getString("text").contains("Steps:"));
        JSONObject st = call(env, "memory_stats");
        assertTrue(st.getInt("total") > 160_000);
        assertFalse(call(env, "memory_match", "text", "qwxzv").optBoolean("confident"));
        try {
            call(env, "memory_get", "id", "tm:nothing.here");
            throw new AssertionError("an unknown id must fail");
        } catch (IllegalArgumentException expected) {
            assertTrue(expected.getMessage().contains("no procedure"));
        }
    }
}
