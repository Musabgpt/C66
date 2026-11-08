package dev.newal.code.lite;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

/**
 * The routines the agent learned on this phone (the engine's phone_routines.json: a phone task that ended well, its
 * steps with the request's texts as blanks), shown in the memory as procedures of the source "learned" so they can
 * be seen and run like the others.
 */
final class TaskMemoryLearned {
    private TaskMemoryLearned() {
    }

    static File file(Context ctx) {
        return new File(ctx.getFilesDir(), "home/.newal-code/phone_routines.json");
    }

    static List<TaskMemory.Proc> all(Context ctx) {
        List<TaskMemory.Proc> out = new ArrayList<>();
        File f = file(ctx);
        if (!f.isFile()) {
            return out;
        }
        try {
            JSONArray list = new JSONArray(new String(java.nio.file.Files.readAllBytes(f.toPath()), StandardCharsets.UTF_8));
            for (int i = list.length() - 1; i >= 0; i--) {
                JSONObject r = list.optJSONObject(i);
                if (r != null) {
                    out.add(proc(r));
                }
            }
        } catch (Exception ignored) {
            // a file being written right now: shown next time
        }
        return out;
    }

    static TaskMemory.Proc get(Context ctx, String key) {
        for (TaskMemory.Proc p : all(ctx)) {
            if (p.key.equals(key)) {
                return p;
            }
        }
        return null;
    }

    /** A learned routine as a procedure: {"action":"tap","text":"إرسال"} becomes {"a":"tap","t":["إرسال"]}. */
    static TaskMemory.Proc proc(JSONObject r) throws Exception {
        TaskMemory.Proc p = new TaskMemory.Proc();
        p.key = "learned." + r.optString("id");
        p.src = "learned";
        p.cat = "learned";
        p.plat = "android";
        p.goal = r.optString("request");
        p.exec = true;
        p.start = "home";
        JSONArray steps = new JSONArray();
        JSONArray in = r.optJSONArray("steps");
        String app = "";
        for (int i = 0; in != null && i < in.length(); i++) {
            JSONObject s = in.optJSONObject(i);
            if (s == null) {
                continue;
            }
            String a = s.optString("action");
            JSONObject o = new JSONObject();
            switch (a) {
                case "tap":
                    o.put("a", "tap");
                    if (s.has("text")) {
                        o.put("t", new JSONArray().put(s.optString("text")));
                    } else {
                        o.put("hint", "item " + s.optString("item", "?"));
                    }
                    break;
                case "type":
                    o.put("a", "type").put("text", s.optString("text"));
                    break;
                case "open_app":
                    o.put("a", "open_app").put("name", s.optString("name"));
                    if (app.isEmpty()) {
                        app = s.optString("name");
                    }
                    break;
                case "open_url":
                    o.put("a", "open_url").put("url", s.optString("url"));
                    break;
                case "key":
                    o.put("a", "key").put("name", s.optString("name"));
                    break;
                case "swipe":
                case "scroll":
                    o.put("a", a).put("direction", s.optString("direction", "down"));
                    break;
                case "settings":
                    o.put("a", "phone").put("action", "settings").put("page", s.optString("page"));
                    break;
                default:
                    o.put("a", "phone");
                    for (java.util.Iterator<String> it = s.keys(); it.hasNext(); ) {
                        String k = it.next();
                        o.put(k, s.get(k));
                    }
            }
            if ("tap".equals(o.optString("a")) && !o.has("t")) {
                p.exec = false;
            }
            steps.put(o);
        }
        p.app = app;
        p.nsteps = steps.length();
        p.stepsJson = new JSONObject().put("steps", steps).toString();
        JSONArray values = r.optJSONArray("values");
        if (values != null && values.length() > 0) {
            JSONArray specs = new JSONArray();
            for (int i = 0; i < values.length(); i++) {
                specs.put(new JSONObject().put("example", values.optString(i)));
            }
            p.slotsJson = specs.toString();
        }
        p.rank = 90;
        return p;
    }
}
