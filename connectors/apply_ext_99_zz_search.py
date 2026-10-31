"""Answers about the world that fit the question: web_search keeps results that carry the query's words
(websearch.py) and a weather tool gives the numbers (weather.py)."""
import shutil


def apply(src, package, replace):
    for name in ("websearch.py", "weather.py", "websearch_tests.py", "weather_tests.py"):
        shutil.copyfile(src / name, package / name)

    replace(package / "tools.py", r'''def web_search(query, n=6):
    """[{title, url, snippet}] from the first free engine that answers (Bing RSS, DuckDuckGo, Wikipedia)."""
    q = urllib.parse.quote_plus(query)
    try:
        xml = _get("https://www.bing.com/search?format=rss&count=10&q=" + q)
        out = []
        for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
            def tag(name):
                m = re.search(r"<%s>(.*?)</%s>" % (name, name), item, re.S)
                return _strip(m.group(1)) if m else ""
            if tag("link"):
                out.append({"title": tag("title"), "url": tag("link"), "snippet": tag("description")})
        if out:
            return out[:n]
    except Exception:  # noqa: BLE001 - the next engine
        pass
    try:
        page = _get("https://html.duckduckgo.com/html/?q=" + q)
        out = []
        for m in re.finditer(r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=class="result__a"|$)', page, re.S):
            href = html.unescape(m.group(1))
            u = re.search(r"uddg=([^&]+)", href)
            if u:
                href = urllib.parse.unquote(u.group(1))
            sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', m.group(3), re.S)
            out.append({"title": _strip(m.group(2)), "url": href, "snippet": _strip(sn.group(1)) if sn else ""})
        if out:
            return out[:n]
    except Exception:  # noqa: BLE001
        pass
    try:
        j = json.loads(_get("https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=5&srsearch="
                            + q))
        return [{"title": x["title"], "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(x["title"].replace(" ", "_")),
                 "snippet": _strip(x.get("snippet", ""))} for x in j.get("query", {}).get("search", [])][:n]
    except Exception:  # noqa: BLE001
        return []
''', '''def web_search(query, n=6):
    """[{title, url, snippet}] from the first free engine whose results fit the query (websearch.py)."""
    from . import websearch
    return websearch.search(query, n)
''')
    replace(package / "tools.py", "from . import tool_search as _tool_search\n",
            "from . import tool_search as _tool_search\n"
            "from . import weather as _weather  # noqa: F401 - registers the weather tool\n")
    replace(package / "tools.py", '        names += ["web_search", "web_fetch"]\n',
            '        names += ["web_search", "web_fetch", "weather"]\n')
    replace(package / "lean.py", '    "web_search", "web_fetch",\n', '    "web_search", "web_fetch", "weather",\n')
    replace(package / "claude_parity.py", '"web_search", "web_fetch", "tool_search"',
            '"web_search", "web_fetch", "weather", "tool_search"')
    # agent_prompt_lean.md names the weather tool: threads pick up the new text
    replace(package / "agent_policy.py", "MARKER = 'MusabAI behavior profile v12'",
            "MARKER = 'MusabAI behavior profile v13'")
