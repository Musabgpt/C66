package dev.newal.code.lite;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.List;

/**
 * The task memory as phone actions, for the agent (connectors/task_memory.py calls them through the phone server):
 * memory_match (the procedure for a request, with its values and the note the model reads), memory_search,
 * memory_get, memory_run (carry a procedure out: TaskRunner), memory_stats, and long_press, which procedures use.
 */
final class TaskMemoryActions {
    private TaskMemoryActions() {
    }

    static boolean handles(String action) {
        return action.startsWith("memory_") || "long_press".equals(action);
    }

    static TaskMemory.Proc find(Context ctx, String ref) throws Exception {
        String key = ref == null ? "" : ref.trim();
        if (key.startsWith("tm:")) {
            key = key.substring(3);
        }
        if (key.isEmpty()) {
            throw new IllegalArgumentException("memory_get needs id: tm:<key> from a note or a search");
        }
        if (key.startsWith("learned.")) {
            TaskMemory.Proc p = TaskMemoryLearned.get(ctx, key);
            if (p != null) {
                return p;
            }
        }
        TaskMemory.Proc p = TaskMemoryDb.memory(ctx).store().get(key);
        if (p == null) {
            throw new IllegalArgumentException("no procedure tm:" + key + " in the task memory");
        }
        return p;
    }

    static List<String> values(JSONObject a) {
        List<String> out = new ArrayList<>();
        JSONArray v = a.optJSONArray("values");
        for (int i = 0; v != null && i < v.length(); i++) {
            out.add(v.optString(i));
        }
        return out;
    }

    static JSONObject handle(Context ctx, JSONObject a) throws Exception {
        String action = a.optString("action");
        switch (action) {
            case "memory_match": {
                TaskMemory tm = TaskMemoryDb.memory(ctx);
                String text = a.optString("text");
                List<TaskMemory.Match> ranked = tm.rank(text, Math.max(1, Math.min(8, a.optInt("limit", 4))));
                JSONObject o = new JSONObject().put("ok", true);
                if (ranked.isEmpty()) {
                    return o.put("text", "no procedure in the task memory fits this request").put("confident", false);
                }
                TaskMemory.Match best = ranked.get(0);
                best.values = tm.slotValues(best.proc, text, best.app);
                JSONObject m = TaskMemory.toJson(best.proc, true);
                m.put("score", Math.round(best.score * 100) / 100.0);
                m.put("values", new JSONArray(best.values));
                o.put("match", m);
                o.put("confident", best.confident);
                o.put("text", TaskMemory.note(best));
                JSONArray others = new JSONArray();
                for (int i = 1; i < ranked.size(); i++) {
                    JSONObject x = TaskMemory.toJson(ranked.get(i).proc, false);
                    x.put("score", Math.round(ranked.get(i).score * 100) / 100.0);
                    others.put(x);
                }
                o.put("others", others);
                return o;
            }
            case "memory_search": {
                TaskMemory tm = TaskMemoryDb.memory(ctx);
                List<TaskMemory.Match> ranked = tm.rank(a.optString("text", a.optString("query")),
                        Math.max(1, Math.min(20, a.optInt("limit", 8))));
                JSONArray results = new JSONArray();
                StringBuilder sb = new StringBuilder();
                for (TaskMemory.Match m : ranked) {
                    JSONObject x = TaskMemory.toJson(m.proc, false);
                    x.put("score", Math.round(m.score * 100) / 100.0);
                    results.put(x);
                    sb.append("tm:").append(m.proc.ref()).append(" [").append(TaskMemory.sourceName(m.proc.src))
                            .append(m.proc.exec ? ", runnable" : ", guidance").append("] ")
                            .append(m.proc.app == null || m.proc.app.isEmpty() ? "" : m.proc.app + ": ")
                            .append(m.proc.goal).append('\n');
                }
                return new JSONObject().put("ok", true).put("results", results)
                        .put("text", sb.length() == 0 ? "nothing in the task memory" : sb.toString().trim());
            }
            case "memory_get": {
                TaskMemory.Proc p = find(ctx, a.optString("id"));
                return new JSONObject().put("ok", true).put("procedure", TaskMemory.toJson(p, true))
                        .put("text", "tm:" + p.ref() + " (" + TaskMemory.sourceName(p.src) + "): " + p.goal + "\nSteps: "
                                + TaskMemory.describe(p));
            }
            case "memory_run": {
                TaskMemory.Proc p = find(ctx, a.optString("id"));
                TaskRunner.Result r = TaskRunner.run(ctx, p, values(a), null, null);
                TaskMemoryUsage.record(ctx, p.ref(), r.done);
                return r.toJson();
            }
            case "memory_stats": {
                TaskMemory tm = TaskMemoryDb.memory(ctx);
                JSONObject o = new JSONObject().put("ok", true);
                JSONObject cats = new JSONObject();
                for (TaskMemory.Category c : tm.store().categories()) {
                    cats.put(c.id, c.n);
                }
                JSONObject srcs = new JSONObject();
                int total = 0;
                for (TaskMemory.Source s : tm.store().sources()) {
                    int n = "learned".equals(s.id) ? TaskMemoryLearned.all(ctx).size() : s.n;
                    srcs.put(s.id, n);
                    total += n;
                }
                o.put("categories", cats).put("sources", srcs).put("total", total);
                return o.put("text", "task memory: " + total + " procedures");
            }
            case "long_press": {
                PhoneControlService s = PhoneControlService.on;
                if (a.has("x") && a.has("y")) {
                    if (s == null) {
                        throw new IllegalStateException("ACCESSIBILITY_OFF: a long press needs the accessibility service");
                    }
                    if (!TaskRunner.press(s, a.optInt("x"), a.optInt("y"), 900)) {
                        throw new IllegalStateException("Android did not take the long press");
                    }
                    return Phone.ok("long-pressed at " + a.optInt("x") + "," + a.optInt("y"));
                }
                if (!TaskRunner.longPress(a.optString("text"))) {
                    throw new IllegalArgumentException("nothing on the screen says \"" + a.optString("text") + "\"");
                }
                return Phone.ok("long-pressed \"" + a.optString("text") + "\"");
            }
            default:
                throw new IllegalArgumentException("unknown task-memory action \"" + action + "\"");
        }
    }
}
