package dev.newal.code.lite;

import org.json.JSONArray;
import org.json.JSONObject;

import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

/**
 * The task memory: procedures from AndroidControl, Mind2Web, InSTA-150K and hand-written ones for the apps people use
 * most, sorted into categories, in one database the app ships (tools/task_memory). Given a request ("افتح فيسبوك
 * وانشر منشور يقول صباح الخير") it finds the procedure that does it, the values the request gives for its blanks
 * ("صباح الخير"), and how sure it is. No Android classes here: the matching is unit tested on the computer against
 * the real database (TaskMemoryTest); TaskMemoryDb reads it on the phone and TaskRunner carries a procedure out.
 */
final class TaskMemory {
    static final double CONFIDENT = 0.62;

    private final TaskMemoryStore store;
    private Map<String, String> lexicon;
    private List<App> apps;
    private int total = -1;

    TaskMemory(TaskMemoryStore store) {
        this.store = store;
    }

    TaskMemoryStore store() {
        return store;
    }

    // ------------------------------------------------------------------------------------------------ the rows

    static final class Proc {
        long id;
        String key = "";
        String src = "";
        String cat = "";
        String plat = "";
        String app = "";
        String pkg = "";
        String goal = "";
        String titleAr = "";
        String stepsJson;
        int nsteps;
        boolean exec;
        String slotsJson;
        String start = "";
        int rank;
        private JSONObject doc;

        String ref() {
            return key != null && !key.isEmpty() ? key : "#" + id;
        }

        String title() {
            return titleAr != null && !titleAr.isEmpty() ? titleAr : goal;
        }

        JSONObject doc() {
            if (doc == null) {
                try {
                    doc = stepsJson == null || stepsJson.isEmpty() ? new JSONObject() : new JSONObject(stepsJson);
                } catch (Exception e) {
                    doc = new JSONObject();
                }
            }
            return doc;
        }

        /** The steps; a web task (a site and a goal, InSTA-150K) is "open the site" and the goal to plan. */
        JSONArray steps() {
            JSONArray s = doc().optJSONArray("steps");
            if (s != null) {
                return s;
            }
            JSONArray out = new JSONArray();
            try {
                if ("web".equals(plat) && app != null && !app.isEmpty()) {
                    out.put(new JSONObject().put("a", "open_url").put("url", "https://" + app));
                }
                out.put(new JSONObject().put("a", "note").put("hint", goal));
            } catch (Exception ignored) {
                // a JSON put on a fresh object does not fail
            }
            return out;
        }

        JSONArray slotSpecs() {
            try {
                return slotsJson == null || slotsJson.isEmpty() ? new JSONArray() : new JSONArray(slotsJson);
            } catch (Exception e) {
                return new JSONArray();
            }
        }

        /** The blanks a request fills ({1}, {2}...), named for the person ("نص المنشور") when they have names. */
        List<String> slotNames() {
            List<String> out = new ArrayList<>();
            JSONArray specs = slotSpecs();
            for (int i = 0; i < specs.length(); i++) {
                JSONObject o = specs.optJSONObject(i);
                if (o == null) {
                    continue;
                }
                String n = o.optString("name", "");
                if (n.isEmpty()) {
                    n = "{" + (i + 1) + "}" + (o.has("example") ? " (" + o.optString("example") + ")" : "");
                }
                out.add(n);
            }
            return out;
        }

        List<String> triggers() {
            List<String> out = new ArrayList<>();
            JSONArray t = doc().optJSONArray("triggers");
            for (int i = 0; t != null && i < t.length(); i++) {
                out.add(t.optString(i));
            }
            return out;
        }

        List<String> packages() {
            List<String> out = new ArrayList<>();
            if (pkg != null && !pkg.isEmpty() && !"web".equals(plat)) {
                out.add(pkg);
            }
            JSONArray alt = doc().optJSONArray("alt_pkgs");
            for (int i = 0; alt != null && i < alt.length(); i++) {
                if (!out.contains(alt.optString(i))) {
                    out.add(alt.optString(i));
                }
            }
            return out;
        }

        String check() {
            return doc().optString("check", "");
        }

        /** The words a person sees on the steps (labels, hints) and the app: what ranks it besides its goal. */
        String stepText() {
            StringBuilder sb = new StringBuilder();
            JSONArray s = doc().optJSONArray("steps");
            for (int i = 0; s != null && i < s.length(); i++) {
                JSONObject st = s.optJSONObject(i);
                if (st == null) {
                    continue;
                }
                JSONArray t = st.optJSONArray("t");
                for (int k = 0; t != null && k < t.length(); k++) {
                    String label = t.optString(k);
                    if (!label.contains("{")) {
                        sb.append(label).append(' ');
                    }
                }
                sb.append(st.optString("hint", "")).append(' ');
                if ("open_app".equals(st.optString("a"))) {
                    sb.append(st.optString("name", "")).append(' ');
                }
            }
            return sb.toString();
        }
    }

    static final class App {
        String name = "";
        String pkg = "";
        String plat = "";
        String cat = "";
        int n;
        List<String> aliases = new ArrayList<>();
    }

    static final class Category {
        String id = "";
        String icon = "";
        String nameAr = "";
        String nameEn = "";
        int n;
        int nExec;
    }

    static final class Source {
        String id = "";
        String name = "";
        String nameAr = "";
        String url = "";
        String license = "";
        String aboutAr = "";
        int n;
        int nExec;
        long minId;
        long maxId;
    }

    static final class Match {
        Proc proc;
        double score;
        double coverage;
        boolean confident;
        List<String> values = new ArrayList<>();
        App app;
    }

    // ------------------------------------------------------------------------------------------------ cached data

    synchronized Map<String, String> lexicon() throws Exception {
        if (lexicon == null) {
            lexicon = store.lexicon();
        }
        return lexicon;
    }

    synchronized List<App> apps() throws Exception {
        if (apps == null) {
            apps = store.apps();
        }
        return apps;
    }

    synchronized int total() throws Exception {
        if (total < 0) {
            total = Math.max(1, store.procedureCount());
        }
        return total;
    }

    // ------------------------------------------------------------------------------------------------ the app

    private static final String[] ATTACHED = {"وال", "بال", "عال", "فال", "لل", "ال", "و", "ب", "ل", "ف"};

    private final Map<String, List<String>> nameTerms = new HashMap<>();
    private final Map<String, Boolean> distinct = new HashMap<>();

    /** Whether an app's name, said in another form, still names it: one of its words is rare in the library
     *  ("zoho" yes; "All Recipes App" or "booking" no: "recipe" and "book" are in thousands of tasks). */
    private boolean distinctive(String name, List<String> terms) throws Exception {
        synchronized (distinct) {
            Boolean d = distinct.get(name);
            if (d != null) {
                return d;
            }
        }
        Map<String, Integer> df = store.documentFrequency(terms);
        boolean d = false;
        for (String t : terms) {
            Integer n = df.get(t);
            if (n == null || n < 250) {
                d = true;
                break;
            }
        }
        synchronized (distinct) {
            distinct.put(name, d);
        }
        return d;
    }

    /** The app the request names ("فيسبوك", "عالفيس", "in the Zoho Meet app"): the name with the most words said,
     *  then the longest; null when none. An English name may be said in other forms ("meet" for "Meeting"). */
    App detectApp(String request) throws Exception {
        List<String> words = TaskMemoryText.words(request);
        if (words.isEmpty()) {
            return null;
        }
        String joined = " " + String.join(" ", words) + " ";
        Set<String> single = new HashSet<>();
        for (String w : words) {
            single.add(w);
            for (String p : ATTACHED) {
                if (w.startsWith(p) && w.length() - p.length() >= 2) {
                    single.add(w.substring(p.length()));
                }
            }
        }
        Set<String> said = new HashSet<>(TaskMemoryText.terms(request));
        App best = null;
        int bestScore = 0;
        for (App a : apps()) {
            int score = 0;
            for (String alias : a.aliases) {
                if (alias.length() < 2) {
                    continue;
                }
                boolean multi = alias.indexOf(' ') >= 0;
                boolean hit = multi ? joined.contains(" " + alias + " ") : single.contains(alias);
                if (hit) {
                    score = Math.max(score, 10 * (multi ? alias.split(" ").length : 1) + alias.length());
                }
            }
            List<String> nt;
            synchronized (nameTerms) {
                nt = nameTerms.get(a.name);
                if (nt == null) {
                    nt = TaskMemoryText.terms(a.name);
                    nameTerms.put(a.name, nt);
                }
            }
            if (!nt.isEmpty() && TaskMemoryText.ascii(a.name) && said.containsAll(nt) && distinctive(a.name, nt)) {
                score = Math.max(score, 10 * nt.size() + a.name.length());
            }
            if (score == 0) {
                continue;
            }
            boolean better = score > bestScore || (score == bestScore && best != null && (a.n > best.n
                    || (a.n == best.n && "android".equals(a.plat) && !"android".equals(best.plat))));
            if (better) {
                best = a;
                bestScore = score;
            }
        }
        return best;
    }

    // ------------------------------------------------------------------------------------------------ ranking

    private static final class Word {
        final List<String> own;
        final Set<String> syn = new LinkedHashSet<>();
        boolean appWord;

        Word(List<String> own) {
            this.own = own;
        }
    }

    private List<Word> expand(String request, App app) throws Exception {
        Map<String, String> lex = lexicon();
        Set<String> alias = new HashSet<>();
        if (app != null) {
            for (String a : app.aliases) {
                alias.addAll(TaskMemoryText.terms(a));
            }
        }
        List<Word> out = new ArrayList<>();
        for (List<String> own : TaskMemoryText.query(request)) {
            Word w = new Word(own);
            for (String v : own) {
                String en = lex.get(v);
                if (en != null) {
                    for (String t : en.split(" ")) {
                        if (!t.isEmpty() && !own.contains(t)) {
                            w.syn.add(t);
                        }
                    }
                }
                if (alias.contains(v)) {
                    w.appWord = true;
                }
            }
            out.add(w);
        }
        return out;
    }

    /** Words that only say "go to the app": every procedure of the app does that, so they weigh little. */
    private static final Set<String> LIGHT = new HashSet<>(java.util.Arrays.asList(
            "open", "launch", "افتح", "افتحلي", "تطبيق", "برنامج", "روح", "ادخل"));

    private static double idf(Map<String, Integer> df, int total, String t) {
        Integer n = df.get(t);
        return Math.log(1.0 + total / (double) ((n == null ? 1 : n) + 1));
    }

    /** The procedures that best fit the request, best first (at most limit). */
    List<Match> rank(String request, int limit) throws Exception {
        App app = detectApp(request);
        List<Word> words = expand(request, app);
        if (words.isEmpty()) {
            return new ArrayList<>();
        }
        Set<String> all = new LinkedHashSet<>();
        for (Word w : words) {
            all.addAll(w.own);
            all.addAll(w.syn);
        }
        Map<String, Integer> df = store.documentFrequency(all);
        int n = total();
        Map<Long, Proc> cands = new LinkedHashMap<>();
        if (app != null) {
            for (Proc p : store.byApp(app.name, 300)) {
                cands.put(p.id, p);
            }
        }
        // a small search engine over the term index: each procedure gathers the weight of the request's words it
        // has (a word counts once, by its best form), and the best 400 are ranked in full below
        Map<Long, double[]> acc = new HashMap<>();
        for (int wi = 0; wi < words.size(); wi++) {
            Word w = words.get(wi);
            Map<Long, Double> wordBest = new HashMap<>();
            Set<String> forms = new LinkedHashSet<>(w.own);
            forms.addAll(w.syn);
            for (String t : forms) {
                if (!t.matches("[\\p{L}\\p{N}]+")) {
                    continue;
                }
                Integer d = df.get(t);
                if (d != null && d > n * 0.08 && words.size() > 1) {
                    continue;                       // a word nearly every procedure has picks nothing out
                }
                double weight = idf(df, n, t) * (w.own.contains(t) ? 1.0 : 0.85);
                for (long id : store.termDocs(t, 6000)) {
                    Double old = wordBest.get(id);
                    if (old == null || old < weight) {
                        wordBest.put(id, weight);
                    }
                }
            }
            for (Map.Entry<Long, Double> e : wordBest.entrySet()) {
                double[] a = acc.get(e.getKey());
                if (a == null) {
                    acc.put(e.getKey(), new double[] {e.getValue()});
                } else {
                    a[0] += e.getValue();
                }
            }
        }
        List<Map.Entry<Long, double[]>> top = new ArrayList<>(acc.entrySet());
        Collections.sort(top, (a, b) -> {
            int c = Double.compare(b.getValue()[0], a.getValue()[0]);
            return c != 0 ? c : Long.compare(a.getKey(), b.getKey());
        });
        List<Long> want = new ArrayList<>();
        for (int i = 0; i < top.size() && want.size() < 400; i++) {
            if (!cands.containsKey(top.get(i).getKey())) {
                want.add(top.get(i).getKey());
            }
        }
        for (Proc p : store.rows(want)) {
            cands.putIfAbsent(p.id, p);
        }
        List<Match> out = new ArrayList<>();
        for (Proc p : cands.values()) {
            Match m = score(p, words, app, df, n);
            if (m.score > 0.05) {
                out.add(m);
            }
        }
        Collections.sort(out, (a, b) -> Double.compare(b.score, a.score));
        // a procedure with blanks is judged on the rest of the request: "انشر منشور يقول صباح الخير يا جماعة" is
        // all "post" once "صباح الخير يا جماعة" is its text
        for (int i = 0; i < Math.min(out.size(), 15); i++) {
            Match m = out.get(i);
            if (m.proc.slotSpecs().length() == 0 || !"curated".equals(m.proc.src)) {
                continue;
            }
            List<String> values = slotValues(m.proc, request, app);
            String rest = request;
            boolean any = false;
            for (String v : values) {
                if (!v.isEmpty() && rest.contains(v)) {
                    rest = rest.replace(v, " ");
                    any = true;
                }
            }
            if (any) {
                List<Word> restWords = expand(rest, app);
                if (!restWords.isEmpty()) {
                    Match again = score(m.proc, restWords, app, df, n);
                    if (again.score > m.score) {
                        out.set(i, again);
                    }
                }
            }
        }
        Collections.sort(out, (a, b) -> Double.compare(b.score, a.score));
        if (out.size() > limit) {
            out = new ArrayList<>(out.subList(0, limit));
        }
        for (Match m : out) {
            m.app = app;
        }
        return out;
    }

    private Match score(Proc p, List<Word> words, App app, Map<String, Integer> df, int n) {
        StringBuilder markers = new StringBuilder();
        JSONArray specs = p.slotSpecs();
        for (int i = 0; i < specs.length(); i++) {
            JSONArray after = specs.optJSONObject(i) == null ? null : specs.optJSONObject(i).optJSONArray("after");
            for (int k = 0; after != null && k < after.length(); k++) {
                markers.append(after.optString(k)).append(' ');
            }
        }
        Set<String> doc = new HashSet<>(TaskMemoryText.terms(p.goal + " " + p.titleAr + " "
                + String.join(" ", p.triggers()) + " " + p.app + " " + p.stepText() + " " + markers));
        boolean sameApp = app != null && p.app != null && p.app.equalsIgnoreCase(app.name);
        double have = 0;
        double want = 0;
        Set<String> matchedTerms = new HashSet<>();
        for (Word w : words) {
            double best = 0;
            double max = 0;
            for (String t : w.own) {
                double weight = idf(df, n, t) * (LIGHT.contains(t) ? 0.3 : 1.0);
                max = Math.max(max, weight);
                if (doc.contains(t)) {
                    best = Math.max(best, weight);
                    matchedTerms.add(t);
                }
            }
            for (String t : w.syn) {
                max = Math.max(max, idf(df, n, t) * 0.85);
                if (doc.contains(t)) {
                    best = Math.max(best, idf(df, n, t) * 0.85);
                    matchedTerms.add(t);
                }
            }
            if (w.appWord && sameApp) {
                best = max;
            }
            have += best;
            want += max;
        }
        double coverage = want > 0 ? have / want : 0;
        // how much of the procedure's own phrase the request says (its best phrase: the goal, the Arabic title or
        // one of the ways people ask for it)
        List<String> phrases = new ArrayList<>();
        phrases.add(p.goal);
        if (p.titleAr != null && !p.titleAr.isEmpty()) {
            phrases.add(p.titleAr);
        }
        phrases.addAll(p.triggers());
        Set<String> appTerms = new HashSet<>();
        if (app != null && sameApp) {
            for (String a : app.aliases) {
                appTerms.addAll(TaskMemoryText.terms(a));
            }
            appTerms.addAll(TaskMemoryText.terms(app.name));
        }
        Set<String> said = new HashSet<>();
        for (Word w : words) {
            said.addAll(w.own);
            said.addAll(w.syn);
        }
        double phrase = 0;
        for (String ph : phrases) {
            List<List<String>> pw = TaskMemoryText.query(ph);
            int total = 0;
            int hit = 0;
            for (List<String> forms : pw) {
                boolean isApp = false;
                for (String f : forms) {
                    isApp |= appTerms.contains(f);
                }
                if (isApp) {
                    continue;
                }
                total++;
                for (String f : forms) {
                    if (said.contains(f)) {
                        hit++;
                        break;
                    }
                }
            }
            if (total > 0) {
                phrase = Math.max(phrase, hit / (double) total);
            }
        }
        double s = 0.6 * coverage + 0.4 * phrase;
        if (app != null) {
            if (sameApp) {
                s += 0.15;
            } else if (p.app != null && !p.app.isEmpty() && phrase < 0.99) {
                s -= 0.3;                       // another app, unless the request says this procedure's very words
            }
        }
        if ("curated".equals(p.src)) {
            s += 0.1;
        }
        if (p.exec) {
            s += 0.03;
        }
        Match m = new Match();
        m.proc = p;
        m.score = s;
        m.coverage = coverage;
        m.confident = s >= CONFIDENT && coverage >= 0.45;
        return m;
    }

    /** The best procedure for the request, with the values it gives; null when nothing fits at all. */
    Match match(String request) throws Exception {
        List<Match> ranked = rank(request, 5);
        if (ranked.isEmpty()) {
            return null;
        }
        Match best = ranked.get(0);
        best.values = slotValues(best.proc, request, best.app);
        return best;
    }

    // ------------------------------------------------------------------------------------------------ the blanks

    private static Set<String> folded(String... words) {
        Set<String> out = new HashSet<>();
        for (String w : words) {
            out.add(TextFold.fold(w));
        }
        return out;
    }

    private static final Set<String> CONTACT_MARKERS = folded("ل", "لـ", "الى", "إلى", "to", "with", "مع");
    private static final Set<String> STOP_AFTER_CONTACT = folded(
            "رساله", "رسالة", "message", "على", "عبر", "في", "بالواتس", "واتساب", "واتس", "on", "via", "saying", "and",
            "و", "انو", "انه", "انها", "يقول", "فيها", "فيه");
    private static final Set<String> BEFORE_APP = folded("في", "على", "عبر", "on", "in", "via", "بال", "ع", "من");

    /** Whether a word names the app ("الواتساب", "بالانستا" for WhatsApp, Instagram). */
    private static boolean isAppWord(String f, Set<String> appWords) {
        if (appWords.contains(f)) {
            return true;
        }
        for (String p : ATTACHED) {
            if (f.startsWith(p) && f.length() - p.length() >= 2 && appWords.contains(f.substring(p.length()))) {
                return true;
            }
        }
        return false;
    }

    /** Whether a word is part of how the procedure is asked for (its triggers, its title) or a filler word. */
    private static boolean isKnown(String f, Set<String> known, Set<String> appWords) {
        if (f.isEmpty() || TaskMemoryText.AR_STOP.contains(f) || TaskMemoryText.EN_STOP.contains(f)
                || isAppWord(f, appWords)) {
            return true;
        }
        for (String v : TaskMemoryText.variantsAr(f)) {
            if (known.contains(v)) {
                return true;
            }
        }
        return known.contains(TaskMemoryText.stemEn(f));
    }

    /** The values the request gives for the procedure's blanks, in order ({1}, {2}...); "" where it gives none. */
    List<String> slotValues(Proc p, String request, App app) {
        JSONArray specs = p.slotSpecs();
        List<String> out = new ArrayList<>();
        if (specs.length() == 0 || request == null) {
            return out;
        }
        if (!"curated".equals(p.src) && !"learned".equals(p.src)) {
            for (int i = 0; i < specs.length(); i++) {
                out.add("");                // a recorded demonstration's examples: the agent gives this request's values
            }
            return out;
        }
        String raw = request.replaceAll("[«»\"“”]", " ").replace(":", ": ").replaceAll("\\s+", " ").trim();
        String[] toks = raw.isEmpty() ? new String[0] : raw.split(" ");
        String[] fold = new String[toks.length];
        for (int i = 0; i < toks.length; i++) {
            fold[i] = TextFold.fold(toks[i]).replaceAll("^[\\p{P}]+|[\\p{P}]+$", "");
        }
        // the words that name the procedure's own app ("على يوتيوب" is where a search goes, never what it looks for);
        // the app the request names is the value when it is another one ("نزل تطبيق سناب شات" installs Snapchat)
        App own = null;
        try {
            for (App a : apps()) {
                if (a.name.equalsIgnoreCase(p.app) && (own == null || a.n > own.n)) {
                    own = a;
                }
            }
        } catch (Exception ignored) {
            // no app list: the request's app stands in
        }
        if (own == null && app != null && app.name.equalsIgnoreCase(p.app)) {
            own = app;
        }
        Set<String> appWords = new HashSet<>();
        if (own != null) {
            for (String a : own.aliases) {
                appWords.addAll(java.util.Arrays.asList(a.split(" ")));
            }
            appWords.add(TextFold.fold(own.name));
        }
        Set<String> known = new HashSet<>(LIGHT);
        known.addAll(folded("افتح", "افتحلي", "شغل", "شغللي", "روح", "ادخل", "open", "launch", "go", "بدي", "اريد"));
        for (String ph : p.triggers()) {
            for (String w : TaskMemoryText.words(ph)) {
                known.addAll(TaskMemoryText.variantsAr(w));
                known.add(TaskMemoryText.stemEn(w));
            }
        }
        for (String w : TaskMemoryText.words(p.titleAr + " " + p.goal)) {
            known.addAll(TaskMemoryText.variantsAr(w));
            known.add(TaskMemoryText.stemEn(w));
        }
        int colonTok = -1;
        for (int i = 0; i < toks.length; i++) {
            if (toks[i].endsWith(":") && i + 1 < toks.length) {
                colonTok = i + 1;                       // a colon starts the text that follows it
                break;
            }
        }
        // each blank's marker: where its value starts, how far into that word, and where the marker itself starts
        int[][] at = new int[specs.length()][];
        boolean[] contact = new boolean[specs.length()];
        boolean[] used = new boolean[toks.length];
        for (int s = 0; s < specs.length(); s++) {
            JSONObject spec = specs.optJSONObject(s);
            JSONArray after = spec == null ? null : spec.optJSONArray("after");
            if (isText(spec) && colonTok >= 0 && !used[colonTok]) {
                at[s] = new int[] {colonTok, 0, colonTok};
                used[colonTok] = true;
                continue;
            }
            int bestTok = Integer.MAX_VALUE;
            int bestOff = 0;
            int bestMark = 0;
            for (int k = 0; after != null && k < after.length(); k++) {
                String m = TextFold.fold(after.optString(k));
                if (m.isEmpty() || m.equals(":")) {
                    continue;
                }
                if (CONTACT_MARKERS.contains(m)) {
                    contact[s] = true;
                }
                String[] mw = m.split(" ");
                for (int i = 0; i + mw.length <= toks.length; i++) {
                    boolean same = true;
                    for (int j = 0; j < mw.length && same; j++) {
                        same = fold[i + j].equals(mw[j]);
                    }
                    int v = i + mw.length;
                    if (same && !used[i] && v < toks.length && v < bestTok && !isAppWord(fold[v], appWords)) {
                        bestTok = v;                     // (a marker before the app's own name, "على يوتيوب", is not one)
                        bestOff = 0;
                        bestMark = i;
                        break;
                    }
                    // "لأحمد", "بأحمد": a one-letter marker written on the name
                    if (mw.length == 1 && (m.equals("ل") || m.equals("ب")) && i > 0 && !used[i]
                            && fold[i].startsWith(m) && fold[i].length() > 2 && i < bestTok
                            && !isAppWord(fold[i], appWords) && !TaskMemoryText.AR_STOP.contains(fold[i])
                            && !fold[i].startsWith("ال") && !STOP_AFTER_CONTACT.contains(fold[i])
                            && !fold[i].substring(1).startsWith("ل") && !known.contains(fold[i])) {
                        bestTok = i;
                        bestOff = 1;
                        bestMark = i;
                        break;
                    }
                }
            }
            if (bestTok != Integer.MAX_VALUE) {
                at[s] = new int[] {bestTok, bestOff, bestMark};
                if (bestTok < toks.length) {
                    used[bestTok] = true;
                }
            }
        }
        for (int s = 0; s < specs.length(); s++) {
            if (at[s] == null) {
                out.add("");
                continue;
            }
            int start = at[s][0];
            int end = toks.length;
            for (int o = 0; o < specs.length(); o++) {
                if (o != s && at[o] != null && at[o][0] > start) {
                    // the next blank's marker ends this value (its value, when the marker shares this value's word)
                    end = Math.min(end, at[o][2] > start ? at[o][2] : at[o][0]);
                }
            }
            List<String> value = new ArrayList<>();
            for (int i = start; i < end; i++) {
                String t = i == start && at[s][1] > 0 ? toks[i].substring(Math.min(toks[i].length(), at[s][1])) : toks[i];
                if (contact[s] && i > start && (STOP_AFTER_CONTACT.contains(fold[i]) || value.size() >= 3)) {
                    break;
                }
                if (contact[s] && isAppWord(fold[i], appWords)) {
                    break;
                }
                value.add(t);
            }
            out.add(clean(String.join(" ", value), appWords));
        }
        // the last blank without a marker: the words of the request that are not how it is asked for
        // ("شغللي فيروز على يوتيوب" -> "فيروز"; "انشر على فيسبوك صباح الخير" -> "صباح الخير")
        int last = specs.length() - 1;
        if (last >= 0 && out.get(last).isEmpty() && !contact[last]) {
            int bestFrom = -1;
            int bestTo = -1;
            for (int i = 0; i < toks.length; ) {
                if (isKnown(fold[i], known, appWords) || used[i]) {
                    i++;
                    continue;
                }
                int j = i;
                while (j < toks.length && !isKnown(fold[j], known, appWords)) {
                    j++;
                }
                bestFrom = i;                           // the last run: what is said after the task's own words
                bestTo = j;
                i = j;
            }
            boolean tail = true;                        // only filler or the app's name after it
            for (int i = Math.max(bestTo, 0); bestFrom >= 0 && i < toks.length; i++) {
                String f = fold[i];
                if (!(TaskMemoryText.AR_STOP.contains(f) || TaskMemoryText.EN_STOP.contains(f) || isAppWord(f, appWords)
                        || BEFORE_APP.contains(f))) {
                    tail = false;
                }
            }
            if (bestFrom >= 0 && tail) {
                if (isText(specs.optJSONObject(last))) {
                    bestTo = toks.length;               // a text runs to the end of the request
                }
                List<String> rest = new ArrayList<>();
                for (int i = bestFrom; i < bestTo; i++) {
                    rest.add(toks[i]);
                }
                out.set(last, clean(String.join(" ", rest), appWords));
            }
        }
        return out;
    }

    /** A blank for free text (a post, a message): its markers include ":". */
    private static boolean isText(JSONObject spec) {
        JSONArray after = spec == null ? null : spec.optJSONArray("after");
        for (int k = 0; after != null && k < after.length(); k++) {
            if (":".equals(after.optString(k))) {
                return true;
            }
        }
        return false;
    }

    /** A value without a trailing "في يوتيوب", "on Facebook", quotes or punctuation; "" when only the app is left. */
    static String clean(String value, Set<String> appWords) {
        String v = value.trim().replaceAll("^[\\s:،,.!؟?\\-]+|[\\s:،,.!؟?\\-]+$", "");
        String[] parts = v.isEmpty() ? new String[0] : v.split(" ");
        int end = parts.length;
        while (end >= 2) {
            String f = TextFold.fold(parts[end - 1]);
            String g = TextFold.fold(parts[end - 2]);
            if (isAppWord(f, appWords) && BEFORE_APP.contains(g)) {
                end -= 2;
            } else {
                break;
            }
        }
        if (end == 1 && isAppWord(TextFold.fold(parts[0]), appWords)) {
            end = 0;
        }
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < end; i++) {
            if (sb.length() > 0) {
                sb.append(' ');
            }
            sb.append(parts[i]);
        }
        return sb.toString().trim();
    }

    // ------------------------------------------------------------------------------------------------ for the model

    /** The steps on one line: open_app "Facebook" → tap "What's on your mind?" → type {1} → tap "POST" (final). */
    static String describe(Proc p) {
        JSONArray steps = p.steps();
        List<String> out = new ArrayList<>();
        for (int i = 0; i < steps.length(); i++) {
            JSONObject s = steps.optJSONObject(i);
            if (s == null) {
                continue;
            }
            String a = s.optString("a");
            String arg;
            JSONArray t = s.optJSONArray("t");
            if (t != null && t.length() > 0) {
                arg = "\"" + t.optString(0) + "\"" + (t.length() > 1 ? " (or \"" + t.optString(1) + "\")" : "");
            } else if (s.has("name")) {
                arg = "\"" + s.optString("name") + "\"";
            } else if (s.has("text")) {
                arg = "\"" + s.optString("text") + "\"";
            } else if (s.has("url")) {
                arg = s.optString("url");
            } else if (s.has("direction")) {
                arg = s.optString("direction");
            } else if ("phone".equals(a)) {
                arg = s.optString("action");
            } else if (s.has("hint")) {
                arg = "(" + s.optString("hint") + ")";
            } else if (s.has("xy")) {
                arg = "(an unnamed button" + (s.has("hint") ? "" : "") + ")";
            } else {
                arg = "";
            }
            String extra = "";
            if (s.optBoolean("optional")) {
                extra = " if shown";
            } else if (s.optBoolean("final")) {
                extra = " (publishes/sends)";
            }
            if (!s.has("t") && s.has("hint") && !"note".equals(a) && ("tap".equals(a) || "long_press".equals(a))) {
                arg = "(" + s.optString("hint") + ")";
            }
            out.add((a + " " + arg).trim() + extra);
        }
        return String.join(" → ", out);
    }

    /** What a procedure is, for the model and the memory screen: its source in words. */
    static String sourceName(String src) {
        switch (src == null ? "" : src) {
            case "curated":
                return "MusabAI curated";
            case "androidcontrol":
                return "AndroidControl";
            case "mind2web":
                return "Mind2Web";
            case "insta150k":
                return "InSTA-150K";
            case "learned":
                return "learned on this phone";
            default:
                return src == null ? "" : src;
        }
    }

    static JSONObject toJson(Proc p, boolean withSteps) throws Exception {
        JSONObject o = new JSONObject();
        o.put("id", "tm:" + p.ref());
        o.put("title", p.title());
        o.put("goal", p.goal);
        o.put("app", p.app == null ? "" : p.app);
        o.put("package", p.pkg == null ? "" : p.pkg);
        o.put("category", p.cat);
        o.put("source", sourceName(p.src));
        o.put("executable", p.exec);
        o.put("blanks", new JSONArray(p.slotNames()));
        if (withSteps) {
            o.put("steps", p.steps());
            o.put("summary", describe(p));
            if (!p.check().isEmpty()) {
                o.put("check", p.check());
            }
        }
        return o;
    }

    /** The line the agent reads under a request that fits a procedure (phone_fast.note on the engine side). */
    static String note(Match m) {
        Proc p = m.proc;
        StringBuilder sb = new StringBuilder();
        sb.append("(Task memory ").append("tm:").append(p.ref()).append(" — ").append(sourceName(p.src));
        if (p.app != null && !p.app.isEmpty()) {
            sb.append(", ").append(p.app);
        }
        sb.append(": \"").append(p.goal).append("\"");
        List<String> names = p.slotNames();
        if (!names.isEmpty()) {
            sb.append("; blanks ");
            for (int i = 0; i < names.size(); i++) {
                if (i > 0) {
                    sb.append(", ");
                }
                sb.append('{').append(i + 1).append("} = ").append(names.get(i));
                if (i < m.values.size() && !m.values.get(i).isEmpty()) {
                    sb.append(" (this request: \"").append(m.values.get(i)).append("\")");
                }
            }
        }
        sb.append(". Steps: ").append(describe(p)).append('.');
        if (p.exec) {
            sb.append(" If it fits, call phone_routine with id tm:").append(p.ref())
                    .append(" and the values for this request: it runs every step, trying each label, and stops "
                            + "where the screen differs so you continue by hand.)");
        } else {
            sb.append(" Follow these steps with the phone tool, adapting to the screen: they come from a recorded "
                    + "demonstration, not a replay.)");
        }
        return sb.toString();
    }

    static String lower(String s) {
        return s == null ? "" : s.toLowerCase(Locale.ROOT);
    }

    static Map<String, Integer> counts(List<Category> cats) {
        Map<String, Integer> out = new HashMap<>();
        for (Category c : cats) {
            out.put(c.id, c.n);
        }
        return out;
    }
}
