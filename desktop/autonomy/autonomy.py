"""Project-scoped persistent memory backed by observed tool evidence."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def _clip(value, limit=8000):
    value = str(value or "")
    return value if len(value) <= limit else value[:limit] + "\n…"


_SECRET_KEY = re.compile(
    r"(?i)(?:authorization|cookie|credential|password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|signature)")
_STOPWORDS = set("""a an and are as at be been but by can do for from had has have how i
    if in into is it its of on or our please that the their then there these they this to
    use using was we were what when where which with you your
    في من على علي الي الى عن ان أن إن انه أنها هو هي هذا هذه ذلك تلك مع او أو و ثم
    كان كانت يكون انا انت نحن هم هل ما ماذا كيف لم لا نعم كل فقط قبل بعد الرجاء من فضلك""".split())


def _redact_url(match):
    try:
        parts = urlsplit(match.group(0))
        host = parts.netloc.rsplit("@", 1)[-1]
        if "@" in parts.netloc:
            host = "[redacted]@" + host
        query = [(key, "[redacted]" if _SECRET_KEY.search(key) else value)
                 for key, value in parse_qsl(parts.query, keep_blank_values=True)]
        return urlunsplit((parts.scheme, host, parts.path,
                           urlencode(query, safe="[]"), parts.fragment))
    except ValueError:
        return "[redacted-url]"


def _safe_text(value, limit=8000):
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, default=str)
    value = str(value or "")
    value = re.sub(r"https?://[^\s\"'<>]+", _redact_url, value)
    value = re.sub(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)",
                   "[redacted]", value, flags=re.S)
    # Quoted JSON values may contain spaces, escaped quotes, or line breaks.
    value = re.sub(r'''(?i)(?<![\w-])(["']?[\w-]*(?:authorization|cookie|credential|password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|signature)[\w-]*["']?\s*[:=]\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''',
                   lambda match: match[1] + '"[redacted]"', value)
    value = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer|basic|token)\s+[^\s,;\"']+",
                   r"\1[redacted]", value)
    value = re.sub(r'''(?i)(?<![\w-])(["']?[\w-]*(?:authorization|cookie|credential|password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|signature)[\w-]*["']?\s*[:=]\s*)[^\s,;&}\]"']+''',
                   r"\1[redacted]", value)
    value = re.sub(r"\b(?:sk-(?:[A-Za-z0-9_-]{12,})|github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]+|hf_[A-Za-z0-9]{12,}|glpat-[A-Za-z0-9_-]+|xox[baprs]-[A-Za-z0-9-]+|AIza[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b",
                   "[redacted]", value)
    return _clip(value, limit)


def _normalize(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    value = "".join(c for c in value if not unicodedata.combining(c) and c != "ـ")
    return value.translate(str.maketrans("أإآٱىة", "اااايه"))


def _terms(value):
    result = set()
    for word in re.findall(r"[^\W_]{2,}", _normalize(value)):
        if word in _STOPWORDS:
            continue
        if word.startswith("ال") and len(word) > 4:
            word = word[2:]
        result.add(word)
    return result


def _fingerprint(topic, lesson):
    value = " ".join(_normalize(topic).split()) + "\n" + " ".join(_normalize(lesson).split())
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MemoryStore:
    """SQLite/WAL memory which survives app restarts and is safe for concurrent workers."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        base = os.environ.get("NEWAL_MEMORY_HOME") or os.path.join(
            os.environ.get("NEWAL_CODE_HOME") or os.path.expanduser("~/.newal-code"), "memory")
        os.makedirs(base, exist_ok=True)
        digest = hashlib.sha256(self.root.encode("utf-8")).hexdigest()[:16]
        self.path = os.path.join(base, digest + ".sqlite3")
        self.lock = threading.RLock()
        self._pending = {}
        self.db = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS episodes(
          id INTEGER PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL,
          session_id TEXT, task TEXT, summary TEXT, error TEXT, evidence TEXT
        );
        CREATE TABLE IF NOT EXISTS learnings(
          id INTEGER PRIMARY KEY, ts REAL NOT NULL, topic TEXT NOT NULL,
          lesson TEXT NOT NULL, evidence TEXT, hits INTEGER NOT NULL DEFAULT 0,
          last_used REAL
        );
        CREATE TABLE IF NOT EXISTS mutations(
          id INTEGER PRIMARY KEY, ts REAL NOT NULL, repo TEXT NOT NULL,
          base_ref TEXT, candidate_ref TEXT, goal TEXT, checks TEXT,
          result TEXT, promoted INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_learnings_topic ON learnings(topic);
        CREATE INDEX IF NOT EXISTS idx_episodes_task ON episodes(task);
        CREATE INDEX IF NOT EXISTS idx_episodes_kind_ts ON episodes(kind,ts DESC);
        CREATE TABLE IF NOT EXISTS memory_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_terms(
          learning_id INTEGER NOT NULL REFERENCES learnings(id) ON DELETE CASCADE,
          term TEXT NOT NULL, weight INTEGER NOT NULL,
          PRIMARY KEY(learning_id,term)
        );
        CREATE INDEX IF NOT EXISTS idx_memory_terms_term ON memory_terms(term,learning_id);
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(learnings)")}
        if "fingerprint" not in columns:
            self.db.execute("ALTER TABLE learnings ADD COLUMN fingerprint TEXT")
        self.db.execute("CREATE INDEX IF NOT EXISTS idx_learnings_fingerprint ON learnings(fingerprint)")
        # Only existing, unindexed rows are migrated; opening the app does not rescan memory.
        for row in self.db.execute("SELECT id,topic,lesson FROM learnings WHERE fingerprint IS NULL").fetchall():
            self.db.execute("UPDATE learnings SET fingerprint=? WHERE id=?", (_fingerprint(row[1], row[2]), row[0]))
            self._index_lesson(*row)
        self.db.commit()
        self._pending_generation = self._generation()

    def _generation(self):
        row = self.db.execute("SELECT value FROM memory_settings WHERE key='generation'").fetchone()
        return int(row[0]) if row else 0

    def _reset_pending(self):
        # A UI request opens its own store. Persist invalidation so live workers
        # cannot recreate evidence the user cleared or disabled elsewhere.
        self.db.execute("INSERT INTO memory_settings(key,value) VALUES('generation','1') "
                        "ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1")
        self._pending.clear()
        self._pending_generation = self._generation()

    def _index_lesson(self, lesson_id, topic, lesson):
        weights = {term: 1 for term in _terms(lesson)}
        weights.update({term: 4 for term in _terms(topic)})
        self.db.executemany("INSERT OR REPLACE INTO memory_terms(learning_id,term,weight) VALUES(?,?,?)",
                            [(lesson_id, term, weight) for term, weight in weights.items()])

    @property
    def enabled(self):
        with self.lock:
            row = self.db.execute("SELECT value FROM memory_settings WHERE key='enabled'").fetchone()
        return row is None or row[0] != "0"

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO memory_settings(key,value) VALUES('enabled',?)",
                            ("1" if enabled else "0",))
            self._reset_pending()
            self.db.commit()

    def episode(self, kind, session_id="", task="", summary="", error="", evidence=""):
        with self.lock:
            if not self.enabled:
                return
            self.db.execute(
                "INSERT INTO episodes(ts,kind,session_id,task,summary,error,evidence) VALUES(?,?,?,?,?,?,?)",
                (time.time(), _safe_text(kind, 80), _safe_text(session_id, 240), _safe_text(task), _safe_text(summary),
                 _safe_text(error), _safe_text(evidence)))
            self.db.commit()

    def learn(self, topic, lesson, evidence=""):
        topic = _safe_text(topic, 240)
        lesson = _safe_text(lesson, 4000)
        if not topic or not lesson:
            return
        with self.lock:
            if not self.enabled:
                return
            fingerprint = _fingerprint(topic, lesson)
            row = self.db.execute(
                "SELECT id FROM learnings WHERE fingerprint=? ORDER BY id LIMIT 1", (fingerprint,)).fetchone()
            if row:
                lesson_id = row[0]
                self.db.execute("UPDATE learnings SET hits=hits+1,last_used=? WHERE id=?",
                                (time.time(), row[0]))
            else:
                cursor = self.db.execute(
                    "INSERT INTO learnings(ts,topic,lesson,evidence,hits,last_used,fingerprint) VALUES(?,?,?,?,1,?,?)",
                    (time.time(), topic, lesson, _safe_text(evidence), time.time(), fingerprint))
                lesson_id = cursor.lastrowid
                self._index_lesson(lesson_id, topic, lesson)
            self.db.commit()
            return lesson_id

    def recall(self, query="", limit=12):
        with self.lock:
            return self._select_lessons(query, min(int(limit), 50)) if self.enabled else []

    def _select_lessons(self, query, limit):
        limit = max(0, min(int(limit), 100))
        terms = sorted(_terms(_safe_text(query, 4000)))[:24]
        columns = "l.id,l.topic,l.lesson,l.evidence,l.hits,l.last_used"
        if str(query or "").strip() and not terms:
            return []
        if terms:
            placeholders = ",".join("?" for _ in terms)
            rows = self.db.execute(
                "SELECT " + columns + " FROM learnings l JOIN memory_terms t ON t.learning_id=l.id "
                "WHERE t.term IN (" + placeholders + ") GROUP BY l.id "
                "ORDER BY COUNT(*) DESC,SUM(t.weight) DESC,l.hits DESC,l.last_used DESC LIMIT ?",
                (*terms, limit)).fetchall()
        else:
            rows = self.db.execute(
                "SELECT " + columns + " FROM learnings l ORDER BY l.hits DESC,l.last_used DESC,l.ts DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(zip(("id", "topic", "lesson", "evidence", "hits", "last_used"), row)) for row in rows]

    def overview(self, query="", limit=100):
        with self.lock:
            return {"enabled": self.enabled,
                    "counts": {"lessons": self.db.execute("SELECT COUNT(*) FROM learnings").fetchone()[0],
                               "episodes": self.db.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]},
                    "lessons": self._select_lessons(query, limit),
                    "recent_failures": self.recent_failures()}

    def forget(self, lesson_id):
        with self.lock:
            deleted = self.db.execute("DELETE FROM learnings WHERE id=?", (int(lesson_id),)).rowcount
            self.db.commit()
            return bool(deleted)

    def clear(self):
        with self.lock:
            self.db.execute("DELETE FROM memory_terms")
            self.db.execute("DELETE FROM learnings")
            self.db.execute("DELETE FROM episodes")
            self._reset_pending()
            self.db.commit()

    def record_tool(self, task, name, args=None, result=None, ok=None, session_id=""):
        """Learn only observed corrections; retain small diagnostics, never successful output.

        Pending failures are scoped to this live worker, task, session, and tool. They
        expire after 30 minutes. A restart cannot pair an unrelated success to one.
        """
        if type(ok) is not bool:
            return None
        result = result if isinstance(result, dict) else {"output": result}
        meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
        code = next((source[key] for source in (meta, result)
                     for key in ("exit", "exit_code", "returncode", "code")
                     if isinstance(source.get(key), int) and not isinstance(source[key], bool)), None)
        if code is not None and code != 0:
            ok = False
        if (result.get("error") or result.get("isError") or meta.get("error") or meta.get("isError")
                or result.get("ok") is False or meta.get("ok") is False):
            ok = False
        arguments = args if isinstance(args, dict) else {}
        details = []
        for key in ("command", "cmd", "path", "file", "filename", "cwd", "workdir"):
            if isinstance(arguments.get(key), str):
                details.append(key + "=" + _safe_text(arguments[key].splitlines()[0] if arguments[key] else "", 350))
        summary = "; ".join(details)[:900]
        # Do not retain arbitrary argument payloads, even for a failed call.
        key = (_safe_text(session_id, 240), _safe_text(task, 1000), _safe_text(name, 100))
        step = result.get("step") if type(result.get("step")) is int else None
        now = time.time()
        with self.lock:
            generation = self._generation()
            if generation != self._pending_generation:
                self._pending.clear()
                self._pending_generation = generation
            if not self.enabled:
                self._pending.clear()
                return None
            self._pending = {k: v for k, v in self._pending.items() if now - v["ts"] < 1800}
            if not ok:
                output = result.get("error") or meta.get("error") or result.get("output") or ""
                lines = str(output).splitlines()
                diagnostic = "\n".join(line for line in lines[:100]
                                       if re.search(r"(?i)error|fail|not found|denied|exception|no such|cannot|fatal", line))
                diagnostic = _safe_text(diagnostic or "Tool failed", 700)
                evidence = _safe_text("tool=" + key[2] + "; " + summary + "; exit=" + str(code) + "; " + diagnostic, 1800)
                self.episode("failure", key[0], key[1], key[2], diagnostic, evidence)
                self._pending[key] = {"ts": now, "summary": summary, "evidence": evidence, "step": step}
                if len(self._pending) > 64:
                    self._pending.pop(next(iter(self._pending)))
                return None
            failed = self._pending.get(key)
            # Independent reads can complete in either order in the same step.
            # A successful call in that batch is not a retry of the failed call.
            if failed and step is not None and failed["step"] is not None and step <= failed["step"]:
                return None
            self._pending.pop(key, None)
            if not failed or not summary or summary == failed["summary"]:
                return None
            lesson = ("Observed " + key[2] + " tool call succeeded: " + summary +
                      ". Earlier call failed: " + (failed["summary"] or "previous arguments") + ".")
            evidence = failed["evidence"] + "\nConfirmed success: tool=" + key[2] + "; exit=" + str(code)
            return self.learn(key[1] + " " + key[2], lesson, evidence)

    def recent_failures(self, limit=10):
        with self.lock:
            rows = self.db.execute(
                "SELECT ts,task,error,evidence FROM episodes WHERE kind='failure' "
                "ORDER BY ts DESC LIMIT ?", (max(0, min(int(limit), 50)),)).fetchall()
        return [{"ts":r[0],"task":r[1],"error":r[2],"evidence":r[3]} for r in rows]

    def close(self):
        with self.lock:
            self.db.close()


def memory_for(root):
    return MemoryStore(root)
