"""GitHub from the chat: Actions runs and pull requests of any repository the connected account can see (not only
this project's), watched until they end, in the background too, with the failed step's log when one fails.

* github_monitor tool: overview, runs, watch, checks (a pull request's), logs, artifacts, rerun, merge.
* /gh [owner/repo]: runs and open pull requests at a glance; /watch [latest | <run id> | pr <n>] [owner/repo]:
  watched in the background, and the thread (and Android, when the app is in the background) is told how it ended.
"""
import re
import threading
import time
import urllib.parse

NAMES = ["github_monitor"]
LOOKS = ("overview", "runs", "watch", "checks", "logs", "artifacts")
POLL = 15
LONGEST = 3 * 3600
_WATCHING = {}                   # (repo, run id) -> thread: one watcher per run


class GHError(ValueError):
    pass


def _ci():
    from . import ci
    return ci


def _get(path):
    from . import github
    if not github.token():
        raise GHError("GitHub is not connected: link it with the Termux + GitHub button (or Settings > GitHub)")
    try:
        return github._api("GET", path)
    except github.GitHubError as e:
        raise GHError(str(e))


def _post(path, body=None, method="POST"):
    from . import github
    if not github.token():
        raise GHError("GitHub is not connected: link it with the Termux + GitHub button (or Settings > GitHub)")
    try:
        return github._api(method, path, body or {})
    except github.GitHubError as e:
        raise GHError(str(e))


def full_name(root="", repo=""):
    """owner/name: the one given, else this project's origin."""
    repo = re.sub(r"^(https?://)?(www\.)?github\.com/", "", str(repo or "").strip()).strip("/")
    repo = re.sub(r"\.git$", "", repo)
    if repo:
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise GHError("repo must be owner/name, e.g. octocat/hello-world")
        return repo
    from . import cloud, util
    top = util.git_root(root) if root else None
    full = cloud.repo_of(top) if top else ""
    if not full:
        raise GHError("which repository? pass repo as owner/name (this project's origin is not on GitHub)")
    return full


def _state(r):
    return _ci().state({"status": r.get("status") or "", "conclusion": r.get("conclusion") or ""})


def _ago(stamp):
    from . import github
    return github._ago(stamp or "")


def _took(r):
    import datetime
    try:
        a = datetime.datetime.strptime((r.get("run_started_at") or r.get("created_at") or "")[:19], "%Y-%m-%dT%H:%M:%S")
        b = datetime.datetime.strptime((r.get("updated_at") or "")[:19], "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return ""
    m, s = divmod(max(0, int((b - a).total_seconds())), 60)
    return "%dm %ds" % (m, s)


def run_line(r):
    return "#%s %s %s · %s · %s%s" % (r.get("run_number") or "?", r.get("name") or "workflow", _state(r),
                                      r.get("head_branch") or "", (r.get("display_title") or "")[:70],
                                      (" · " + _took(r)) if r.get("status") == "completed" else "")


def runs(full, branch="", limit=10):
    path = "/repos/%s/actions/runs?per_page=%d" % (full, limit)
    if branch:
        path += "&branch=" + urllib.parse.quote(branch, safe="")
    return (_get(path) or {}).get("workflow_runs") or []


def pull_checks(full, pr):
    """(state word, the head commit's runs) of a pull request."""
    sha = ((pr.get("head") or {}).get("sha")) or ""
    items = _ci().runs(full, sha) if sha else []
    if not items:
        return "no checks", items
    if any(i["status"] != "completed" for i in items):
        return "… running", items
    if any(i["conclusion"] in _ci().RED for i in items):
        return "✗ failing", items
    return "✓ passing", items


def overview(full, branch=""):
    """Text and data: the latest runs and the open pull requests with their checks."""
    items = runs(full, branch, 8)
    prs = _get("/repos/%s/pulls?state=open&per_page=10" % full) or []
    data = {"repo": full, "runs": [], "prs": []}
    lines = ["**%s**" % full, "", "Actions runs (latest first):"]
    for r in items:
        data["runs"].append({"id": r.get("id"), "number": r.get("run_number"), "name": r.get("name"),
                             "status": r.get("status"), "conclusion": r.get("conclusion"),
                             "state": _state(r), "branch": r.get("head_branch"), "title": r.get("display_title"),
                             "url": r.get("html_url"), "took": _took(r) if r.get("status") == "completed" else "",
                             "when": _ago(r.get("created_at"))})
        lines.append("- " + run_line(r) + "  (id %s)" % r.get("id"))
    if not items:
        lines.append("- none yet")
    lines += ["", "Open pull requests:"]
    for i, p in enumerate(prs):
        checks = pull_checks(full, p)[0] if i < 6 else ""
        data["prs"].append({"number": p.get("number"), "title": p.get("title"), "checks": checks,
                            "head": (p.get("head") or {}).get("ref"), "base": (p.get("base") or {}).get("ref"),
                            "draft": p.get("draft"), "url": p.get("html_url"), "when": _ago(p.get("updated_at"))})
        lines.append("- #%s %s (%s → %s)%s" % (p.get("number"), p.get("title"), (p.get("head") or {}).get("ref"),
                                               (p.get("base") or {}).get("ref"), (" · " + checks) if checks else ""))
    if not prs:
        lines.append("- none")
    return "\n".join(lines), data


def resolve_run(full, target="latest", branch=""):
    """A run id from 'latest', a run id, 'pr <n>' / 'pr:<n>' (the newest run of its head commit) or a run number '#n'."""
    t = str(target or "latest").strip().lower()
    m = re.fullmatch(r"(?:pr[ :#]*)(\d+)", t)
    if m:
        pr = _get("/repos/%s/pulls/%s" % (full, m.group(1))) or {}
        items = _ci().runs(full, (pr.get("head") or {}).get("sha") or "")
        if not items:
            raise GHError("pull request #%s has no Actions runs" % m.group(1))
        return sorted(items, key=lambda r: r.get("created") or "")[-1]["id"]
    if t in ("", "latest", "last"):
        items = runs(full, branch, 1)
        if not items:
            raise GHError("no Actions runs in %s%s" % (full, " on " + branch if branch else ""))
        return items[0]["id"]
    m = re.fullmatch(r"#(\d+)", t)
    if m:                                     # a run number as the web page shows it
        for r in runs(full, branch, 50):
            if str(r.get("run_number")) == m.group(1):
                return r["id"]
        raise GHError("no run #%s among the latest 50" % m.group(1))
    if t.isdigit():
        return int(t)
    raise GHError("which run? latest, a run id, #<run number> or pr <number>")


def failure_text(full, r, limit=8000):
    ci = _ci()
    item = {"id": r.get("id"), "name": r.get("name") or "run", "conclusion": r.get("conclusion") or "failure",
            "url": r.get("html_url") or ""}
    fails = ci.failures(full, [dict(item, conclusion="failure")], limit=limit)
    return ci.describe(fails) if fails else ""


def watch(full, run_id, say=None, cancel=None, poll=POLL, longest=LONGEST):
    """Waits until the run ends: (ok, text, the run)."""
    started, last = time.time(), None
    while True:
        if cancel is not None and cancel.is_set():
            return False, "Stopped watching.", {}
        r = _get("/repos/%s/actions/runs/%s" % (full, run_id)) or {}
        if r.get("status") == "completed":
            ok = (r.get("conclusion") or "") in _ci().GREEN
            text = run_line(r) + ("\n" + r.get("html_url", "") if r.get("html_url") else "")
            if not ok and r.get("conclusion") in _ci().RED:
                why = failure_text(full, r)
                if why:
                    text += "\n\n" + why
            return ok, text, r
        line = run_line(r)
        if say and line != last:
            say(line)
        last = line
        if time.time() - started > longest:
            return False, "Still running after %d minutes: %s" % (longest // 60, line), r
        if cancel is not None:
            cancel.wait(poll)
        else:
            time.sleep(poll)


def watch_in_background(service, sid, full, run_id):
    """Watches in a thread of its own; the thread `sid` is told how it ended (a notice and a gh_watch event)."""
    key = (full, str(run_id))
    t = _WATCHING.get(key)
    if t and t.is_alive():
        return False

    def run():
        try:
            ok, text, r = watch(full, run_id, poll=POLL)
        except Exception as e:  # noqa: BLE001 - said in the thread
            ok, text, r = False, "Watching %s run %s failed: %s" % (full, run_id, e), {}
        head = "%s GitHub Actions %s: %s" % ("✅" if ok else "❌", full, text.split("\n", 1)[0])
        service.broadcast({"type": "gh_watch", "state": "end", "session": sid, "ok": ok, "repo": full,
                           "run": run_id, "text": text[:6000], "title": head})
        service.broadcast({"type": "notice", "session": sid, "text": head + (("\n" + text.split("\n", 1)[1][:3000])
                                                                             if "\n" in text else "")})
        try:
            from . import phone_ext
            if phone_ext.phone_ready():
                phone_ext._phone().call("notify", timeout=10, title=("✅ " if ok else "❌ ") + full,
                                        text=text.split("\n", 1)[0][:300], only_background=True)
        except Exception:  # noqa: BLE001
            pass
        _WATCHING.pop(key, None)
    t = threading.Thread(target=run, name="musab-gh-watch", daemon=True)
    _WATCHING[key] = t
    t.start()
    service.broadcast({"type": "gh_watch", "state": "start", "session": sid, "repo": full, "run": run_id})
    return True


def artifacts(full, run_id):
    items = (_get("/repos/%s/actions/runs/%s/artifacts?per_page=50" % (full, run_id)) or {}).get("artifacts") or []
    if not items:
        return "Run %s has no artifacts." % run_id
    return "\n".join(["Artifacts of run %s (download from https://github.com/%s/actions/runs/%s):" % (run_id, full,
                                                                                                      run_id)] +
                     ["- %s · %.1f MB%s" % (a.get("name"), (a.get("size_in_bytes") or 0) / 1e6,
                                            " · expired" if a.get("expired") else "") for a in items])


def checks(full, number):
    pr = _get("/repos/%s/pulls/%s" % (full, number)) or {}
    word, items = pull_checks(full, pr)
    lines = ["#%s %s (%s → %s) · %s · mergeable: %s" % (number, pr.get("title"), (pr.get("head") or {}).get("ref"),
                                                         (pr.get("base") or {}).get("ref"), word,
                                                         pr.get("mergeable_state") or pr.get("mergeable"))]
    lines += ["- %s %s (id %s)" % (i["name"], _ci().state(i), i["id"]) for i in items]
    return "\n".join(lines), {"checks": word, "runs": len(items)}


def merge(full, number, method="merge"):
    method = method if method in ("merge", "squash", "rebase") else "merge"
    out = _post("/repos/%s/pulls/%s/merge" % (full, number), {"merge_method": method}, method="PUT") or {}
    if not out.get("merged"):
        raise GHError(out.get("message") or "not merged")
    return "Merged #%s (%s): %s" % (number, method, (out.get("sha") or "")[:7])


def do(root, action, repo="", number="", branch="", method="", say=None, cancel=None, service=None, sid=None,
       background=False):
    """One github_monitor action: (text, meta)."""
    full = full_name(root, repo)
    action = str(action or "overview").strip().lower()
    number = str(number or "").strip()
    if action == "overview":
        text, data = overview(full, branch)
        return text, {"repo": full, "runs": len(data["runs"]), "prs": len(data["prs"])}
    if action == "runs":
        items = runs(full, branch, 15)
        lines = ["- %s (id %s)" % (run_line(r), r.get("id")) for r in items] or ["- none"]
        return "\n".join(["Actions runs in %s:" % full] + lines), {"repo": full, "runs": len(items)}
    if action == "watch":
        run_id = resolve_run(full, number or "latest", branch)
        if background:
            if service is None:
                raise GHError("watching in the background needs the app")
            started = watch_in_background(service, sid, full, run_id)
            return ("Watching run %s of %s in the background: this thread is told when it ends." % (run_id, full)
                    if started else "Run %s is already being watched." % run_id), {"repo": full, "run": run_id}
        ok, text, _ = watch(full, run_id, say=say, cancel=cancel)
        return text, {"repo": full, "run": run_id, "ok": ok}
    if action == "checks":
        if not number.lstrip("#").isdigit():
            raise GHError("checks needs number (a pull request)")
        return checks(full, number.lstrip("#"))
    if action == "logs":
        run_id = resolve_run(full, number or "latest", branch)
        r = _get("/repos/%s/actions/runs/%s" % (full, run_id)) or {}
        why = failure_text(full, r) if r.get("conclusion") in _ci().RED else ""
        return (why or "Run %s: %s, no failed job." % (run_id, _state(r))), {"repo": full, "run": run_id}
    if action == "artifacts":
        run_id = resolve_run(full, number or "latest", branch)
        return artifacts(full, run_id), {"repo": full, "run": run_id}
    if action == "rerun":
        run_id = resolve_run(full, number or "latest", branch)
        _post("/repos/%s/actions/runs/%s/rerun-failed-jobs" % (full, run_id))
        return "Run %s: its failed jobs run again." % run_id, {"repo": full, "run": run_id}
    if action == "merge":
        if not number.lstrip("#").isdigit():
            raise GHError("merge needs number (a pull request)")
        return merge(full, number.lstrip("#"), method or "merge"), {"repo": full, "merged": number}
    raise GHError("action: overview, runs, watch, checks, logs, artifacts, rerun or merge")


def install():
    from . import tools

    @tools.tool("github_monitor", "GitHub Actions and pull requests of any repository the user's GitHub account sees "
                "(default: this project's). action=overview: latest runs and open PRs with their checks; runs; "
                "watch (number: latest, a run id, #<run number> or 'pr 12'): wait until the run ends and get the "
                "failed step's log if it fails (background=true: keep working, the thread is told when it ends); "
                "checks (number: a PR); logs (number: a run); artifacts; rerun (failed jobs); merge (number: a PR, "
                "method: merge/squash/rebase).",
                {"action": tools._s("overview, runs, watch, checks, logs, artifacts, rerun or merge"),
                 "repo": tools._s("owner/name (default: this project's origin)"),
                 "number": tools._s("a run id, #run number, 'latest', or a pull request number"),
                 "branch": tools._s("only runs of this branch"),
                 "method": tools._s("merge: merge, squash or rebase"),
                 "background": {"type": "boolean", "description": "watch: in the background"}},
                ["action"], "github")
    def t_github_monitor(ctx, action, repo="", number="", branch="", method="", background=False):
        from . import fleet
        try:
            text, meta = do(ctx.root, action, repo, number, branch, method,
                            say=lambda t: ctx.emit({"type": "status", "text": t}), cancel=ctx.cancel,
                            service=fleet.SERVICE, sid=ctx.session.id, background=bool(background))
        except (GHError, RuntimeError) as e:
            raise tools.ToolError(str(e))
        return tools.clip(text, 12000), meta


# ------------------------------------------------------------------ /gh, /watch and the app's routes

def command(service, s, name, args):
    words = args.split()
    repo = next((w for w in words if "/" in w), "")
    rest = [w for w in words if w != repo]
    try:
        if name == "gh":
            return {"reply": overview(full_name(s.root, repo))[0]}
        target = " ".join(rest) or "latest"
        full = full_name(s.root, repo)
        run_id = resolve_run(full, target)
        started = watch_in_background(service, s.id, full, run_id)
        return {"reply": ("👀 Watching run %s of %s: you will be told here when it ends." % (run_id, full))
                if started else "Run %s is already being watched." % run_id}
    except (GHError, RuntimeError) as e:
        return {"reply": "GitHub: %s" % e}


def route(service, method, path, q, b):
    root = ""
    sid = str(q.get("session") or b.get("session") or "")
    if sid:
        try:
            root = service.get(sid).root
        except (OSError, KeyError, ValueError):
            root = ""
    try:
        if path == "/api/gh/overview" and method == "GET":
            text, data = overview(full_name(root, q.get("repo") or ""), q.get("branch") or "")
            return data
        if path == "/api/gh/action" and method == "POST":
            action = str(b.get("action") or "")
            if action not in LOOKS + ("rerun", "merge"):
                return {"error": "unknown action"}
            text, meta = do(root, action, b.get("repo") or "", b.get("number") or "", b.get("branch") or "",
                            b.get("method") or "", service=service, sid=sid, background=action == "watch")
            return {"ok": True, "text": text, "meta": meta}
    except (GHError, RuntimeError) as e:
        return {"error": str(e)}
    return {"error": "not found"}
