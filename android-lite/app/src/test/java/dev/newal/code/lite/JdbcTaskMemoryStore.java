package dev.newal.code.lite;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.file.Files;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.GZIPInputStream;

/** The shipped task-memory database read through JDBC (sqlite-jdbc), so the matching is tested on the real data. */
final class JdbcTaskMemoryStore implements TaskMemoryStore {
    static final String COLS = "id, key, src, cat, plat, app, pkg, goal, title_ar, steps, nsteps, exec, slots, start, rank";
    private final Connection db;

    JdbcTaskMemoryStore(File file) throws Exception {
        db = DriverManager.getConnection("jdbc:sqlite:" + file.getAbsolutePath());
    }

    /** The asset (library.db.gz), unpacked once into a temporary file. */
    static synchronized JdbcTaskMemoryStore shipped() throws Exception {
        File gz = null;
        for (String path : new String[] {"src/main/assets/task_memory/library.db.gz",
                "android-lite/app/src/main/assets/task_memory/library.db.gz",
                "app/src/main/assets/task_memory/library.db.gz"}) {
            File f = new File(path);
            if (f.isFile()) {
                gz = f;
                break;
            }
        }
        if (gz == null) {
            throw new IllegalStateException("library.db.gz not found from " + new File(".").getAbsolutePath());
        }
        File out = new File(System.getProperty("java.io.tmpdir"), "task_memory_test_" + gz.length() + ".db");
        if (!out.isFile()) {
            File tmp = Files.createTempFile("task_memory", ".db").toFile();
            try (InputStream in = new GZIPInputStream(new java.io.FileInputStream(gz));
                 OutputStream o = new FileOutputStream(tmp)) {
                in.transferTo(o);
            }
            if (!tmp.renameTo(out)) {
                Files.copy(tmp.toPath(), out.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING);
            }
        }
        return new JdbcTaskMemoryStore(out);
    }

    static TaskMemory.Proc proc(ResultSet r) throws Exception {
        TaskMemory.Proc p = new TaskMemory.Proc();
        p.id = r.getLong(1);
        p.key = r.getString(2) == null ? "" : r.getString(2);
        p.src = r.getString(3);
        p.cat = r.getString(4);
        p.plat = r.getString(5);
        p.app = r.getString(6) == null ? "" : r.getString(6);
        p.pkg = r.getString(7) == null ? "" : r.getString(7);
        p.goal = r.getString(8) == null ? "" : r.getString(8);
        p.titleAr = r.getString(9) == null ? "" : r.getString(9);
        p.stepsJson = r.getString(10);
        p.nsteps = r.getInt(11);
        p.exec = r.getInt(12) != 0;
        p.slotsJson = r.getString(13);
        p.start = r.getString(14) == null ? "" : r.getString(14);
        p.rank = r.getInt(15);
        return p;
    }

    private List<TaskMemory.Proc> procs(String sql, Object... args) throws Exception {
        try (PreparedStatement st = db.prepareStatement(sql)) {
            for (int i = 0; i < args.length; i++) {
                st.setObject(i + 1, args[i]);
            }
            List<TaskMemory.Proc> out = new ArrayList<>();
            try (ResultSet r = st.executeQuery()) {
                while (r.next()) {
                    out.add(proc(r));
                }
            }
            return out;
        }
    }

    @Override
    public Map<String, String> lexicon() throws Exception {
        Map<String, String> out = new HashMap<>();
        try (ResultSet r = db.createStatement().executeQuery("SELECT ar, en FROM lexicon")) {
            while (r.next()) {
                out.put(r.getString(1), r.getString(2));
            }
        }
        return out;
    }

    @Override
    public List<TaskMemory.App> apps() throws Exception {
        List<TaskMemory.App> out = new ArrayList<>();
        try (ResultSet r = db.createStatement().executeQuery("SELECT name, pkg, plat, cat, n, aliases FROM apps")) {
            while (r.next()) {
                out.add(TaskMemoryDbRows.app(r.getString(1), r.getString(2), r.getString(3), r.getString(4),
                        r.getInt(5), r.getString(6)));
            }
        }
        return out;
    }

    @Override
    public Map<String, Integer> documentFrequency(Collection<String> terms) throws Exception {
        Map<String, Integer> out = new HashMap<>();
        if (terms.isEmpty()) {
            return out;
        }
        StringBuilder q = new StringBuilder("SELECT term, df FROM vocab WHERE term IN (");
        for (int i = 0; i < terms.size(); i++) {
            q.append(i == 0 ? "?" : ",?");
        }
        q.append(')');
        try (PreparedStatement st = db.prepareStatement(q.toString())) {
            int i = 1;
            for (String t : terms) {
                st.setString(i++, t);
            }
            try (ResultSet r = st.executeQuery()) {
                while (r.next()) {
                    out.put(r.getString(1), r.getInt(2));
                }
            }
        }
        return out;
    }

    @Override
    public int procedureCount() throws Exception {
        try (ResultSet r = db.createStatement().executeQuery("SELECT COUNT(*) FROM procs")) {
            return r.next() ? r.getInt(1) : 0;
        }
    }

    @Override
    public List<TaskMemory.Proc> byApp(String app, int limit) throws Exception {
        return procs("SELECT " + COLS + " FROM procs WHERE app = ? COLLATE NOCASE AND src <> 'insta150k' "
                + "ORDER BY rank DESC, id LIMIT ?", app, limit);
    }

    @Override
    public long[] termDocs(String term, int limit) throws Exception {
        try (PreparedStatement st = db.prepareStatement("SELECT docid FROM procs_fts WHERE procs_fts MATCH ? LIMIT ?")) {
            st.setString(1, term);
            st.setInt(2, limit);
            List<Long> ids = new ArrayList<>();
            try (ResultSet r = st.executeQuery()) {
                while (r.next()) {
                    ids.add(r.getLong(1));
                }
            }
            long[] out = new long[ids.size()];
            for (int i = 0; i < out.length; i++) {
                out[i] = ids.get(i);
            }
            return out;
        }
    }

    @Override
    public List<TaskMemory.Proc> rows(Collection<Long> ids) throws Exception {
        if (ids.isEmpty()) {
            return new ArrayList<>();
        }
        StringBuilder q = new StringBuilder("SELECT " + COLS + " FROM procs WHERE id IN (");
        int i = 0;
        for (Long id : ids) {
            q.append(i++ == 0 ? "" : ",").append(id.longValue());
        }
        q.append(')');
        return procs(q.toString());
    }

    @Override
    public TaskMemory.Proc get(String key) throws Exception {
        List<TaskMemory.Proc> r = key.startsWith("#")
                ? procs("SELECT " + COLS + " FROM procs WHERE id = ?", Long.parseLong(key.substring(1)))
                : procs("SELECT " + COLS + " FROM procs WHERE key = ?", key);
        return r.isEmpty() ? null : r.get(0);
    }

    @Override
    public List<TaskMemory.Category> categories() throws Exception {
        List<TaskMemory.Category> out = new ArrayList<>();
        try (ResultSet r = db.createStatement().executeQuery(
                "SELECT id, icon, name_ar, name_en, n, n_exec FROM categories ORDER BY ord")) {
            while (r.next()) {
                TaskMemory.Category c = new TaskMemory.Category();
                c.id = r.getString(1);
                c.icon = r.getString(2);
                c.nameAr = r.getString(3);
                c.nameEn = r.getString(4);
                c.n = r.getInt(5);
                c.nExec = r.getInt(6);
                out.add(c);
            }
        }
        return out;
    }

    @Override
    public List<TaskMemory.Source> sources() throws Exception {
        List<TaskMemory.Source> out = new ArrayList<>();
        try (ResultSet r = db.createStatement().executeQuery("SELECT id, name, name_ar, url, license, about_ar, n, "
                + "n_exec, min_id, max_id FROM sources ORDER BY ord")) {
            while (r.next()) {
                TaskMemory.Source s = new TaskMemory.Source();
                s.id = r.getString(1);
                s.name = r.getString(2);
                s.nameAr = r.getString(3);
                s.url = r.getString(4);
                s.license = r.getString(5);
                s.aboutAr = r.getString(6);
                s.n = r.getInt(7);
                s.nExec = r.getInt(8);
                s.minId = r.getLong(9);
                s.maxId = r.getLong(10);
                out.add(s);
            }
        }
        return out;
    }

    @Override
    public List<TaskMemory.Proc> byCategory(String category, String app, int offset, int limit) throws Exception {
        if (app != null && !app.isEmpty()) {
            return procs("SELECT " + COLS + " FROM procs WHERE cat = ? AND app = ? COLLATE NOCASE ORDER BY rank DESC, id "
                    + "LIMIT ? OFFSET ?", category, app, limit, offset);
        }
        return procs("SELECT " + COLS + " FROM procs WHERE cat = ? ORDER BY rank DESC, id LIMIT ? OFFSET ?", category,
                limit, offset);
    }

    @Override
    public List<TaskMemory.Proc> bySource(String source, int offset, int limit) throws Exception {
        return procs("SELECT " + COLS + " FROM procs WHERE src = ? ORDER BY id LIMIT ? OFFSET ?", source, limit, offset);
    }

    @Override
    public List<TaskMemory.App> appsIn(String category, int limit) throws Exception {
        List<TaskMemory.App> out = new ArrayList<>();
        try (PreparedStatement st = db.prepareStatement(
                "SELECT name, pkg, plat, cat, n, aliases FROM apps WHERE cat = ? AND n > 0 ORDER BY n DESC LIMIT ?")) {
            st.setString(1, category);
            st.setInt(2, limit);
            try (ResultSet r = st.executeQuery()) {
                while (r.next()) {
                    out.add(TaskMemoryDbRows.app(r.getString(1), r.getString(2), r.getString(3), r.getString(4),
                            r.getInt(5), r.getString(6)));
                }
            }
        }
        return out;
    }

    @Override
    public Map<String, String> meta() throws Exception {
        Map<String, String> out = new LinkedHashMap<>();
        try (ResultSet r = db.createStatement().executeQuery("SELECT k, v FROM meta")) {
            while (r.next()) {
                out.put(r.getString(1), r.getString(2));
            }
        }
        return out;
    }
}
