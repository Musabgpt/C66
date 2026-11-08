package dev.newal.code.lite;

import java.util.ArrayList;
import java.util.Arrays;

/** Row helpers shared by the phone's SQLite store and the tests' JDBC store. */
final class TaskMemoryDbRows {
    private TaskMemoryDbRows() {
    }

    static final String COLS = "id, key, src, cat, plat, app, pkg, goal, title_ar, steps, nsteps, exec, slots, start, rank";

    static TaskMemory.App app(String name, String pkg, String plat, String cat, int n, String aliases) {
        TaskMemory.App a = new TaskMemory.App();
        a.name = name == null ? "" : name;
        a.pkg = pkg == null ? "" : pkg;
        a.plat = plat == null ? "" : plat;
        a.cat = cat == null ? "" : cat;
        a.n = n;
        a.aliases = new ArrayList<>();
        if (aliases != null && !aliases.isEmpty()) {
            a.aliases.addAll(Arrays.asList(aliases.split("\\|")));
        }
        String own = TextFold.fold(a.name);
        if (!own.isEmpty() && !a.aliases.contains(own)) {
            a.aliases.add(0, own);
        }
        return a;
    }
}
