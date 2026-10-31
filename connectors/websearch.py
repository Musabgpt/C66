"""web_search that answers the question asked.

Measured from the phone (2026-10-31): Bing's RSS feed, tried first, answered "سعر الذهب اليوم في الأردن" with Chinese
pages about SoundCloud and "weather Amman today" with the weather in Derby, England. Because it returned something,
DuckDuckGo, whose results were right, was never asked. Now DuckDuckGo goes first, and a list of results is taken only
when its results carry the query's words; otherwise the next engine is asked, and if none does, the list that carries
most of them is returned."""
import html
import json
import re
import urllib.parse

from . import tools

_MARKS = re.compile("[ً-ْٰـ]")          # Arabic short vowels, shadda, tatweel
_FOLD = str.maketrans("أإآٱةىؤئ", "ااااهيوي")
# words that say nothing about what is searched for (they still go to the engine)
_STOP = {"the", "and", "for", "with", "what", "how", "who", "when", "where", "which", "today", "now", "latest",
         "اليوم", "الان", "هلق", "هسا", "شو", "ما", "ماذا", "كيف", "كم", "قديش", "اين", "وين", "متى", "هل", "عن",
         "على", "الى", "من", "في", "مع", "هذا", "هذه", "اخر", "احدث", "يوم"}


def fold(text):
    return _MARKS.sub("", str(text or "").lower()).translate(_FOLD)


def keywords(query):
    """The query's distinctive words, folded (Arabic letter forms, the article and attached و/ب/ل/ف/ك dropped)."""
    out = []
    for w in re.findall(r"\w+", fold(query)):
        for prefix in ("وال", "بال", "فال", "كال", "لل", "ال"):
            if w.startswith(prefix) and len(w) - len(prefix) >= 3:
                w = w[len(prefix):]
                break
        if len(w) >= 3 and w not in _STOP and fold(w) not in _STOP:
            out.append(w)
    return list(dict.fromkeys(out))


def _hits(row, words):
    text = fold(" ".join((row.get("title", ""), row.get("snippet", ""), urllib.parse.unquote(row.get("url", "")))))
    return sum(1 for w in words if w in text)


def _need(words):
    return len(words) if len(words) <= 2 else -(-len(words) * 3 // 5)


def relevant(rows, words):
    """The rows, those that carry the query first, or [] when too few of the top ones do."""
    if not words or not rows:
        return rows
    need = _need(words)
    top = rows[:5]
    if sum(_hits(r, words) >= need for r in top) < min(2, len(top)):
        return []
    return sorted(rows, key=lambda r: _hits(r, words) < need)


def _duckduckgo(q):
    page = tools._get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(q))
    out = []
    for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|$)', page, re.S):
        href = html.unescape(m.group(1))
        u = re.search(r"uddg=([^&]+)", href)
        if u:
            href = urllib.parse.unquote(u.group(1))
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', m.group(3), re.S)
        if "duckduckgo.com/y.js" not in href:           # ads
            out.append({"title": tools._strip(m.group(2)), "url": href, "snippet": tools._strip(sn.group(1)) if sn else ""})
    return out


def _bing(q):
    xml = tools._get("https://www.bing.com/search?format=rss&count=10&q=" + urllib.parse.quote_plus(q))
    out = []
    for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(name):
            m = re.search(r"<%s>(.*?)</%s>" % (name, name), item, re.S)
            return tools._strip(m.group(1)) if m else ""
        if tag("link"):
            out.append({"title": tag("title"), "url": tag("link"), "snippet": tag("description")})
    return out


def _wikipedia(q):
    lang = "ar" if re.search("[؀-ۿ]", q) else "en"
    j = json.loads(tools._get("https://%s.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=5"
                              "&srsearch=%s" % (lang, urllib.parse.quote_plus(q))))
    return [{"title": x["title"], "url": "https://%s.wikipedia.org/wiki/%s" % (lang, urllib.parse.quote(
        x["title"].replace(" ", "_"))), "snippet": tools._strip(x.get("snippet", ""))}
        for x in j.get("query", {}).get("search", [])]


ENGINES = (_duckduckgo, _bing, _wikipedia)


def search(query, n=6):
    """[{title, url, snippet}] from the first engine whose results carry the query's words."""
    words = keywords(query)
    best, best_hits = [], -1
    for engine in ENGINES:
        try:
            rows = [r for r in engine(query) if str(r.get("url", "")).startswith("http")]
        except Exception:  # noqa: BLE001 - the next engine
            continue
        good = relevant(rows, words)
        if good:
            return good[:n]
        hits = sum(_hits(r, words) for r in rows[:5])
        if rows and hits > best_hits:
            best, best_hits = rows, hits
    return best[:n]
