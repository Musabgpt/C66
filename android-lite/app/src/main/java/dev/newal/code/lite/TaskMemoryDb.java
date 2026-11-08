package dev.newal.code.lite;

import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.zip.GZIPInputStream;

/**
 * The task memory on the phone: the library the APK ships (assets/task_memory/library.db.gzip) unpacked once into the
 * app's files (again after an update brings a new one) and read with Android's SQLite, read-only. memory(ctx) is the
 * one shared TaskMemory; the first call unpacks (about a second), later ones are instant. The asset is not named
 * ".gz": the Android build un-gzips such assets on its own and renames them, and the app would not find it.
 */
final class TaskMemoryDb implements TaskMemoryStore {
    private static final String ASSET = "task_memory/library.db.gzip";
    private static TaskMemory shared;
    private final SQLiteDatabase db;

    private TaskMemoryDb(SQLiteDatabase db) {
        this.db = db;
    }

    static synchronized TaskMemory memory(Context ctx) throws Exception {
        if (shared == null) {
            File file = install(ctx.getApplicationContext());
            SQLiteDatabase db = SQLiteDatabase.openDatabase(file.getPath(), null,
                    SQLiteDatabase.OPEN_READONLY | SQLiteDatabase.NO_LOCALIZED_COLLATORS);
            shared = new TaskMemory(new TaskMemoryDb(db));
        }
        return shared;
    }

    static synchronized boolean ready() {
        return shared != null;
    }

    /** The unpacked library, unpacked now when it is missing or from an older build. */
    static File install(Context ctx) throws Exception {
        File dir = new File(ctx.getFilesDir(), "task_memory");
        File db = new File(dir, "library.db");
        File stamp = new File(dir, "library.version");
        String want = BuildConfig.VERSION_CODE + ":" + assetLength(ctx);
        if (db.isFile() && stamp.isFile()) {
            String have = new String(java.nio.file.Files.readAllBytes(stamp.toPath()), StandardCharsets.UTF_8).trim();
            if (have.equals(want)) {
                return db;
            }
        }
        if (!dir.isDirectory() && !dir.mkdirs()) {
            throw new IllegalStateException("cannot create " + dir);
        }
        File tmp = new File(dir, "library.db.tmp");
        try (InputStream in = new GZIPInputStream(ctx.getAssets().open(ASSET), 1 << 16);
             OutputStream out = new FileOutputStream(tmp)) {
            byte[] buf = new byte[1 << 16];
            for (int n; (n = in.read(buf)) > 0; ) {
                out.write(buf, 0, n);
            }
        }
        if (db.exists() && !db.delete()) {
            throw new IllegalStateException("cannot replace " + db);
        }
        if (!tmp.renameTo(db)) {
            throw new IllegalStateException("cannot move " + tmp);
        }
        try (OutputStream out = new FileOutputStream(stamp)) {
            out.write(want.getBytes(StandardCharsets.UTF_8));
        }
        return db;
    }

    private static long assetLength(Context ctx) {
        try (android.content.res.AssetFileDescriptor fd = ctx.getAssets().openFd(ASSET)) {
            return fd.getLength();                  // stored uncompressed (build.gradle.kts noCompress)
        } catch (Exception compressed) {
            // a build that compressed it: count it
        }
        try (InputStream in = ctx.getAssets().open(ASSET)) {
            long n = 0;
            byte[] buf = new byte[1 << 16];
            for (int r; (r = in.read(buf)) > 0; ) {
                n += r;
            }
            return n;
        } catch (Exception e) {
            return -1;
        }
    }

    // ------------------------------------------------------------------------------------------------ rows

    private static TaskMemory.Proc proc(Cursor c) {
        TaskMemory.Proc p = new TaskMemory.Proc();
        p.id = c.getLong(0);
        p.key = str(c, 1);
        p.src = str(c, 2);
        p.cat = str(c, 3);
        p.plat = str(c, 4);
        p.app = str(c, 5);
        p.pkg = str(c, 6);
        p.goal = str(c, 7);
        p.titleAr = str(c, 8);
        p.stepsJson = c.isNull(9) ? null : c.getString(9);
        p.nsteps = c.getInt(10);
        p.exec = c.getInt(11) != 0;
        p.slotsJson = c.isNull(12) ? null : c.getString(12);
        p.start = str(c, 13);
        p.rank = c.getInt(14);
        return p;
    }

    private static String str(Cursor c, int i) {
        return c.isNull(i) ? "" : c.getString(i);
    }

    private List<TaskMemory.Proc> procs(String sql, String... args) {
        List<TaskMemory.Proc> out = new ArrayList<>();
        try (Cursor c = db.rawQuery(sql, args)) {
            while (c.moveToNext()) {
                out.add(proc(c));
            }
        }
        return out;
    }

    @Override
    public Map<String, String> lexicon() {
        Map<String, String> out = new HashMap<>();
        try (Cursor c = db.rawQuery("SELECT ar, en FROM lexicon", null)) {
            while (c.moveToNext()) {
                out.put(c.getString(0), c.getString(1));
            }
        }
        return out;
    }

    @Override
    public List<TaskMemory.App> apps() {
        return appRows("SELECT name, pkg, plat, cat, n, aliases FROM apps", (String[]) null);
    }

    private List<TaskMemory.App> appRows(String sql, String... args) {
        List<TaskMemory.App> out = new ArrayList<>();
        try (Cursor c = db.rawQuery(sql, args)) {
            while (c.moveToNext()) {
                out.add(TaskMemoryDbRows.app(str(c, 0), str(c, 1), str(c, 2), str(c, 3), c.getInt(4), str(c, 5)));
            }
        }
        return out;
    }

    @Override
    public Map<String, Integer> documentFrequency(Collection<String> terms) {
        Map<String, Integer> out = new HashMap<>();
        List<String> list = new ArrayList<>(terms);
        for (int from = 0; from < list.size(); from += 400) {
            List<String> part = list.subList(from, Math.min(list.size(), from + 400));
            StringBuilder q = new StringBuilder("SELECT term, df FROM vocab WHERE term IN (");
            for (int i = 0; i < part.size(); i++) {
                q.append(i == 0 ? "?" : ",?");
            }
            q.append(')');
            try (Cursor c = db.rawQuery(q.toString(), part.toArray(new String[0]))) {
                while (c.moveToNext()) {
                    out.put(c.getString(0), c.getInt(1));
                }
            }
        }
        return out;
    }

    @Override
    public int procedureCount() {
        try (Cursor c = db.rawQuery("SELECT COUNT(*) FROM procs", null)) {
            return c.moveToFirst() ? c.getInt(0) : 0;
        }
    }

    @Override
    public List<TaskMemory.Proc> byApp(String app, int limit) {
        return procs("SELECT " + TaskMemoryDbRows.COLS + " FROM procs WHERE app = ? COLLATE NOCASE AND src <> 'insta150k' "
                + "ORDER BY rank DESC, id LIMIT " + limit, app);
    }

    @Override
    public long[] termDocs(String term, int limit) {
        try (Cursor c = db.rawQuery("SELECT docid FROM procs_fts WHERE procs_fts MATCH ? LIMIT " + limit,
                new String[] {term})) {
            long[] out = new long[c.getCount()];
            int i = 0;
            while (c.moveToNext()) {
                out[i++] = c.getLong(0);
            }
            return out;
        }
    }

    @Override
    public List<TaskMemory.Proc> rows(Collection<Long> ids) {
        if (ids.isEmpty()) {
            return new ArrayList<>();
        }
        StringBuilder q = new StringBuilder("SELECT " + TaskMemoryDbRows.COLS + " FROM procs WHERE id IN (");
        int i = 0;
        for (Long id : ids) {
            q.append(i++ == 0 ? "" : ",").append(id.longValue());
        }
        q.append(')');
        return procs(q.toString());
    }

    @Override
    public TaskMemory.Proc get(String key) {
        List<TaskMemory.Proc> r = key.startsWith("#")
                ? procs("SELECT " + TaskMemoryDbRows.COLS + " FROM procs WHERE id = ?", key.substring(1))
                : procs("SELECT " + TaskMemoryDbRows.COLS + " FROM procs WHERE key = ?", key);
        return r.isEmpty() ? null : r.get(0);
    }

    @Override
    public List<TaskMemory.Category> categories() {
        List<TaskMemory.Category> out = new ArrayList<>();
        try (Cursor c = db.rawQuery("SELECT id, icon, name_ar, name_en, n, n_exec FROM categories ORDER BY ord", null)) {
            while (c.moveToNext()) {
                TaskMemory.Category k = new TaskMemory.Category();
                k.id = str(c, 0);
                k.icon = str(c, 1);
                k.nameAr = str(c, 2);
                k.nameEn = str(c, 3);
                k.n = c.getInt(4);
                k.nExec = c.getInt(5);
                out.add(k);
            }
        }
        return out;
    }

    @Override
    public List<TaskMemory.Source> sources() {
        List<TaskMemory.Source> out = new ArrayList<>();
        try (Cursor c = db.rawQuery("SELECT id, name, name_ar, url, license, about_ar, n, n_exec, min_id, max_id "
                + "FROM sources ORDER BY ord", null)) {
            while (c.moveToNext()) {
                TaskMemory.Source s = new TaskMemory.Source();
                s.id = str(c, 0);
                s.name = str(c, 1);
                s.nameAr = str(c, 2);
                s.url = str(c, 3);
                s.license = str(c, 4);
                s.aboutAr = str(c, 5);
                s.n = c.getInt(6);
                s.nExec = c.getInt(7);
                s.minId = c.getLong(8);
                s.maxId = c.getLong(9);
                out.add(s);
            }
        }
        return out;
    }

    @Override
    public List<TaskMemory.Proc> byCategory(String category, String app, int offset, int limit) {
        if (app != null && !app.isEmpty()) {
            return procs("SELECT " + TaskMemoryDbRows.COLS + " FROM procs WHERE cat = ? AND app = ? COLLATE NOCASE "
                    + "ORDER BY rank DESC, id LIMIT " + limit + " OFFSET " + offset, category, app);
        }
        return procs("SELECT " + TaskMemoryDbRows.COLS + " FROM procs WHERE cat = ? ORDER BY rank DESC, id LIMIT "
                + limit + " OFFSET " + offset, category);
    }

    @Override
    public List<TaskMemory.Proc> bySource(String source, int offset, int limit) {
        for (TaskMemory.Source s : sources()) {
            if (s.id.equals(source)) {
                return procs("SELECT " + TaskMemoryDbRows.COLS + " FROM procs WHERE id BETWEEN ? AND ? ORDER BY id LIMIT "
                        + limit + " OFFSET " + offset, String.valueOf(s.minId), String.valueOf(s.maxId));
            }
        }
        return new ArrayList<>();
    }

    @Override
    public List<TaskMemory.App> appsIn(String category, int limit) {
        return appRows("SELECT name, pkg, plat, cat, n, aliases FROM apps WHERE cat = ? AND n > 0 ORDER BY n DESC LIMIT "
                + limit, category);
    }

    @Override
    public Map<String, String> meta() {
        Map<String, String> out = new LinkedHashMap<>();
        try (Cursor c = db.rawQuery("SELECT k, v FROM meta", null)) {
            while (c.moveToNext()) {
                out.put(c.getString(0), c.getString(1));
            }
        }
        return out;
    }
}
