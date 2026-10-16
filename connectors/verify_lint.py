"""Before the agent says it is done, the files it changed must be free of certain errors (syntax, undefined names,
invalid JSON, broken shell scripts) — Cursor's "fix lints before finishing". Conservative: only the built-in checks
that are never false alarms, so a turn is not held back by style warnings."""
import os


def changed_problems(ctx, limit=12):
    """Text listing the certain problems in this turn's changed files ("" when none)."""
    from . import diagnostics, tools
    try:
        from . import code_intel
    except ImportError:
        code_intel = None
    out = []
    for p in sorted(getattr(ctx, "changed", ()) or ()):
        full = p if os.path.isabs(p) else os.path.join(ctx.root, p)
        if not os.path.isfile(full):
            continue
        try:
            text = tools.read_text(full)
            found = list(diagnostics.check(full, text))
            if code_intel is not None and full.endswith((".sh", ".bash")):
                found += code_intel.extra_problems(full, ctx.root)
        except Exception:  # noqa: BLE001 - a check never breaks the turn
            continue
        for problem in found:
            out.append("%s: %s" % (os.path.relpath(full, ctx.root), problem))
            if len(out) >= limit:
                break
    if not out:
        return ""
    return ("These files you changed still have errors; fix them, then finish:\n" + "\n".join(out))
