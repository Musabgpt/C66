"""Builds the task memory: one SQLite database, sorted into categories, that the app ships and the agent searches.

  python3 build_db.py --out library.db --androidcontrol androidcontrol.jsonl --mind2web mind2web.jsonl \
      --insta insta150k.jsonl [--gzip assets/task_memory/library.db.gzip]

Tables: procs (every procedure, with its steps as JSON), procs_fts (FTS4 over its search terms), vocab (how many
procedures use each term, for ranking), lexicon (Arabic word -> English words), apps (name, package, category, the
names people say), categories and sources (with counts) and meta.
"""
import argparse
import collections
import gzip
import json
import os
import re
import shutil
import sqlite3
import time

import curated
import knowledge as K
import textnorm as T

SCHEMA = 1
NOISE = set(T.terms("click tap select option button screen icon page enter type press swipe go open view "
                    "the top bottom left right corner side first second third"))
M2W_SUBDOMAIN = {"Airlines": "travel", "Other": "travel", "Ground": "travel", "Restaurant": "food", "Car rental": "travel",
                 "Hotel": "travel", "General": None, "Speciality": "shopping", "Auto": "auto", "Fashion": "shopping",
                 "Digital": "shopping", "Department": "shopping", "Movie": "entertainment", "Event": "entertainment",
                 "Sports": "entertainment", "Music": "media", "Game": "entertainment"}
M2W_DOMAIN = {"Travel": "travel", "Shopping": "shopping", "Entertainment": "entertainment"}
TLD = {"com", "org", "net", "gov", "edu", "info", "io", "co", "uk", "us", "de", "fr", "ca", "au", "in", "eu", "www",
       "html", "it", "es", "nl", "jp", "ru", "br", "ch", "se", "no", "dk", "fi", "pl", "be", "at", "nz", "ie", "za",
       "mx", "ar", "cl", "app", "me", "tv", "fm", "ly", "biz"}

_CAT_WORDS = []
for cat, words in K.CATEGORY_WORDS:
    for w in T.terms(words):
        _CAT_WORDS.append((w, cat))
CAT_WORDS = collections.defaultdict(set)
for w, cat in _CAT_WORDS:
    CAT_WORDS[w].add(cat)

APP_BY_NAME = {}
for name, (cat, pkgs, aliases) in K.APPS.items():
    APP_BY_NAME[T.fold(name)] = name


def read_jsonl(path):
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


WEB_CATEGORIES = {"shopping", "food", "travel", "media", "news", "health", "education", "jobs", "home", "auto",
                  "entertainment", "finance"}


def categorize(app, goal, extra="", allowed=None):
    """The category: the app's own when it is known, else the words of the app's name (worth 3) and the goal.
    allowed limits it (a website is never "messaging" because its task says "message" or "line")."""
    known = K.APPS.get(APP_BY_NAME.get(T.fold(app), ""))
    if known:
        return known[0]
    score = collections.Counter()
    for t in T.terms(app):
        for c in CAT_WORDS.get(t, ()):
            if allowed is None or c in allowed:
                score[c] += 3
    for t in T.terms(goal + " " + extra):
        for c in CAT_WORDS.get(t, ()):
            if allowed is None or c in allowed:
                score[c] += 1
    if not score:
        return ""
    best = score.most_common()
    if len(best) > 1 and best[0][1] == best[1][1]:
        order = [c[0] for c in K.CATEGORIES]
        top = [c for c, s in best if s == best[0][1]]
        return sorted(top, key=order.index)[0]
    return best[0][0]


def domain_words(domain):
    parts = re.split(r"[.\-]", domain.lower())
    return " ".join(p for p in parts if p and p not in TLD)


def step_words(steps):
    out = []
    for s in steps or []:
        for t in s.get("t") or []:
            if "{" not in t:
                out.append(t)
        if s.get("hint"):
            out.append(s["hint"])
        if s.get("a") == "open_app" and s.get("name"):
            out.append(s["name"])
    return " ".join(out)


def canonical_apps(ac):
    """AndroidControl names the app by what was opened, or by the window's title when the demonstration started inside
    the app ("Item", "Popup Window"...): only names something opened somewhere, or a known app, are kept."""
    opened = collections.Counter()
    for r in ac:
        for s in r["steps"]:
            if s["a"] == "open_app" and s.get("name"):
                opened[s["name"]] += 1
    valid = {T.fold(n): n for n in opened}
    for n in K.APPS:
        valid.setdefault(T.fold(n), n)
    fixed = 0
    for r in ac:
        name = r.get("app") or ""
        f = T.fold(name)
        if f in valid:
            r["app"] = valid[f]
            continue
        hit = next((v for k, v in valid.items() if f and len(f) >= 4 and (k.startswith(f) or f.startswith(k))), "")
        r["app"] = hit
        fixed += 1
    return fixed


def build(args):
    t0 = time.time()
    records = []
    for c in curated.CURATED:
        r = dict(c)
        r["src"] = "curated"
        r["plat"] = "android" if not r["steps"] or r["steps"][0]["a"] != "open_url" else "android"
        r["exec"] = True
        r["start"] = "home"
        records.append(r)
    ac = read_jsonl(args.androidcontrol)
    canonical_apps(ac)
    m2w = read_jsonl(args.mind2web)
    insta = read_jsonl(args.insta)

    def rank_of(r):
        return {"curated": 100, "androidcontrol": 60 if r.get("exec") else 40,
                "mind2web": 50 if r.get("exec") else 35, "insta150k": 10}[r["src"]]

    for r in ac:
        records.append(r)
    for r in m2w:
        records.append(r)
    for r in insta:
        records.append(r)
    src_order = {"curated": 0, "androidcontrol": 1, "mind2web": 2, "insta150k": 3}
    records.sort(key=lambda r: (src_order[r["src"]], -rank_of(r), r["key"]))

    if os.path.exists(args.out):
        os.remove(args.out)
    db = sqlite3.connect(args.out)
    db.executescript("""
        PRAGMA page_size = 4096;
        CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE categories (id TEXT PRIMARY KEY, ord INTEGER, icon TEXT, name_ar TEXT, name_en TEXT,
                                 n INTEGER, n_exec INTEGER);
        CREATE TABLE sources (id TEXT PRIMARY KEY, ord INTEGER, name TEXT, name_ar TEXT, url TEXT, license TEXT,
                              about_ar TEXT, n INTEGER, n_exec INTEGER, min_id INTEGER, max_id INTEGER);
        CREATE TABLE apps (id INTEGER PRIMARY KEY, name TEXT, pkg TEXT, plat TEXT, cat TEXT, n INTEGER,
                           aliases TEXT);
        CREATE TABLE procs (id INTEGER PRIMARY KEY, key TEXT, src TEXT, cat TEXT, plat TEXT, app TEXT,
                            pkg TEXT, goal TEXT, title_ar TEXT, steps TEXT, nsteps INTEGER, exec INTEGER,
                            slots TEXT, start TEXT, rank INTEGER);
        CREATE TABLE vocab (term TEXT PRIMARY KEY, df INTEGER) WITHOUT ROWID;
        CREATE TABLE lexicon (ar TEXT PRIMARY KEY, en TEXT) WITHOUT ROWID;
    """)
    cat_n = collections.Counter()
    cat_exec = collections.Counter()
    src_n = collections.Counter()
    src_exec = collections.Counter()
    app_rows = {}
    df = collections.Counter()
    rows = []
    fts = []
    src_ids = {}
    for i, r in enumerate(records, start=1):
        src = r["src"]
        app = r.get("app") or ""
        goal = r.get("goal") or ""
        steps = r.get("steps")
        if src == "curated":
            cat = r["cat"]
        elif src == "mind2web":
            cat = M2W_SUBDOMAIN.get(r.get("subdomain")) or categorize(app, goal) or M2W_DOMAIN.get(r.get("domain"), "web")
        elif src == "insta150k":
            cat = categorize(domain_words(app), goal, allowed=WEB_CATEGORIES) or "web"
        else:
            cat = categorize(app, goal) or "other"
        known = K.APPS.get(APP_BY_NAME.get(T.fold(app), ""))
        pkg = r.get("pkg") or ""
        if not pkg and known:
            pkg = known[1][0]
        aliases = list(known[2]) if known else []
        text = " ".join([goal, r.get("title_ar", ""), " ".join(r.get("triggers") or []), app, " ".join(aliases),
                         domain_words(app) if src == "insta150k" else "", step_words(steps),
                         r.get("subdomain", "") if src == "mind2web" else ""])
        terms = []
        seen = set()
        for t in T.terms(text):
            if t in seen or (t in NOISE and src != "curated"):
                continue
            seen.add(t)
            terms.append(t)
        for t in terms:
            df[t] += 1
        slots = r.get("slots") or []
        if src == "curated":
            slot_json = json.dumps(slots, ensure_ascii=False) if slots else ""
        else:
            slot_json = json.dumps([{"example": v} for v in slots], ensure_ascii=False) if slots else ""
        extra = {}
        if r.get("alt_pkgs"):
            extra["alt_pkgs"] = r["alt_pkgs"]
        if r.get("check"):
            extra["check"] = r["check"]
        if r.get("pattern"):
            extra["pattern"] = r["pattern"]
        if r.get("triggers"):
            extra["triggers"] = r["triggers"]
        step_doc = None
        if steps:
            step_doc = {"steps": steps}
            step_doc.update(extra)
        web_task = src == "insta150k"
        rows.append((i, None if web_task else r["key"], src, cat, r.get("plat", "android"), app,
                     None if web_task else (pkg or None), goal, r.get("title_ar") or None,
                     json.dumps(step_doc, ensure_ascii=False, separators=(",", ":")) if step_doc else None,
                     len(steps or []), 1 if r.get("exec") else 0, slot_json or None,
                     None if web_task else (r.get("start") or None), rank_of(r)))
        src_ids.setdefault(src, [i, i])
        src_ids[src][1] = i
        fts.append((i, " ".join(terms)))
        cat_n[cat] += 1
        cat_exec[cat] += 1 if r.get("exec") else 0
        src_n[src] += 1
        src_exec[src] += 1 if r.get("exec") else 0
        if app and src != "insta150k":
            k = (app, r.get("plat", "android"))
            row = app_rows.setdefault(k, {"name": app, "pkg": pkg, "plat": r.get("plat", "android"), "cats": collections.Counter(),
                                          "n": 0, "aliases": aliases})
            row["n"] += 1
            row["cats"][cat] += 1
            if pkg and not row["pkg"]:
                row["pkg"] = pkg
    db.executemany("INSERT INTO procs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.execute("CREATE INDEX procs_cat ON procs(cat, rank DESC, id)")
    db.execute("CREATE UNIQUE INDEX procs_key ON procs(key) WHERE key IS NOT NULL")
    db.execute("CREATE INDEX procs_app ON procs(app COLLATE NOCASE) WHERE src <> 'insta150k'")
    # contentless: the terms are only an index (the phone works them out again from the row for ranking)
    db.execute("CREATE VIRTUAL TABLE procs_fts USING fts4(content=\"\", terms, tokenize=simple)")
    db.executemany("INSERT INTO procs_fts(docid, terms) VALUES (?, ?)", fts)
    db.execute("INSERT INTO procs_fts(procs_fts) VALUES ('optimize')")
    db.executemany("INSERT INTO vocab VALUES (?, ?)", sorted((t, n) for t, n in df.items() if n >= 2))
    lex = {}
    for ar, en in K.LEXICON.items():
        key = T.fold(ar).rstrip("_")
        words = " ".join(dict.fromkeys(T.terms(en)))
        if key and words:
            lex[key] = (lex[key] + " " + words) if key in lex else words
    db.executemany("INSERT INTO lexicon VALUES (?, ?)", sorted(lex.items()))
    for o, (cid, icon, ar, en) in enumerate(K.CATEGORIES):
        db.execute("INSERT INTO categories VALUES (?,?,?,?,?,?,?)", (cid, o, icon, ar, en, cat_n[cid], cat_exec[cid]))
    for o, (sid, name, ar, url, lic, about) in enumerate(K.SOURCES):
        lo, hi = src_ids.get(sid, [0, -1])
        db.execute("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, o, name, ar, url, lic, about, src_n[sid], src_exec[sid], lo, hi))
    for n, row in enumerate(sorted(app_rows.values(), key=lambda x: (-x["n"], x["name"])), start=1):
        alias_text = "|".join(dict.fromkeys([T.fold(row["name"])] + [T.fold(a) for a in row["aliases"]]))
        db.execute("INSERT INTO apps VALUES (?,?,?,?,?,?,?)", (n, row["name"], row["pkg"], row["plat"],
                                                               row["cats"].most_common(1)[0][0], row["n"], alias_text))
    # the apps the curated list knows by name but no procedure uses yet, so a request still finds the app
    for name, (cat, pkgs, aliases) in K.APPS.items():
        if (name, "android") not in app_rows:
            alias_text = "|".join(dict.fromkeys([T.fold(name)] + [T.fold(a) for a in aliases]))
            db.execute("INSERT INTO apps (name, pkg, plat, cat, n, aliases) VALUES (?,?,?,?,?,?)",
                       (name, pkgs[0], "android", cat, 0, alias_text))
    db.execute("CREATE INDEX apps_name ON apps(name COLLATE NOCASE)")
    meta = {"schema": str(SCHEMA), "built": time.strftime("%Y-%m-%d"), "procedures": str(len(records)),
            "executable": str(sum(src_exec.values())), "terms": str(len(df))}
    db.executemany("INSERT INTO meta VALUES (?, ?)", sorted(meta.items()))
    db.commit()
    db.execute("VACUUM")
    db.close()
    size = os.path.getsize(args.out)
    print("procedures", len(records), "executable", sum(src_exec.values()), "terms", len(df),
          "db %.1f MB" % (size / 1e6), "in %.1f s" % (time.time() - t0))
    print("by source", dict(src_n), "exec", dict(src_exec))
    print("by category", sorted(cat_n.items(), key=lambda x: -x[1]))
    if args.gzip:
        os.makedirs(os.path.dirname(args.gzip), exist_ok=True)
        with open(args.out, "rb") as f, gzip.GzipFile(args.gzip, "wb", compresslevel=9, mtime=0) as g:
            shutil.copyfileobj(f, g)
        print("gzip %.1f MB" % (os.path.getsize(args.gzip) / 1e6), args.gzip)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--androidcontrol")
    ap.add_argument("--mind2web")
    ap.add_argument("--insta")
    ap.add_argument("--gzip")
    build(ap.parse_args())
