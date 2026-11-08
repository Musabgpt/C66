package dev.newal.code.lite;

import java.util.Collection;
import java.util.List;
import java.util.Map;

/**
 * Where the task memory's database is read from: Android's SQLite on the phone (TaskMemoryDb), a JDBC connection in
 * the unit tests. Every method answers from the shipped library (tools/task_memory/build_db.py).
 */
interface TaskMemoryStore {
    /** Folded Arabic word -> the English terms it can mean. */
    Map<String, String> lexicon() throws Exception;

    List<TaskMemory.App> apps() throws Exception;

    /** How many procedures use each of these terms (absent: fewer than two). */
    Map<String, Integer> documentFrequency(Collection<String> terms) throws Exception;

    int procedureCount() throws Exception;

    /** The procedures of one app (not the web tasks), best first. */
    List<TaskMemory.Proc> byApp(String app, int limit) throws Exception;

    /** The ids of the procedures whose search terms contain this term, in the order of the library. */
    long[] termDocs(String term, int limit) throws Exception;

    /** These procedures (in any order). */
    List<TaskMemory.Proc> rows(Collection<Long> ids) throws Exception;

    /** One procedure by its key ("cur.facebook.post", "ac.60") or by "#id". */
    TaskMemory.Proc get(String key) throws Exception;

    List<TaskMemory.Category> categories() throws Exception;

    List<TaskMemory.Source> sources() throws Exception;

    List<TaskMemory.Proc> byCategory(String category, String app, int offset, int limit) throws Exception;

    List<TaskMemory.Proc> bySource(String source, int offset, int limit) throws Exception;

    List<TaskMemory.App> appsIn(String category, int limit) throws Exception;

    Map<String, String> meta() throws Exception;
}
