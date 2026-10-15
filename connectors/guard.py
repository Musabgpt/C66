"""An edit that breaks a file's syntax is refused, and the file is put back as it was.

SWE-agent's ablation (NeurIPS 2024) measured this guardrail: with it 18.0% of SWE-bench Lite was solved, without it
15.0%. The engine already reports problems after an edit (diagnostics.py); this goes one step further, the way
SWE-agent does: a write or edit that turns a file which parsed into one that does not is undone, and the model is told
where it broke, so it redoes the edit whole instead of building on a broken file.

Only regressions are refused: a file that did not parse before the edit is never blocked (a fix may take two edits),
and a new file is never blocked. Python is parsed with ast, JSON with json; for C-family languages (Java, Kotlin, C,
C++, C#, Go, Swift, Dart, Scala, Gradle, JavaScript and TypeScript without JSX) the brackets must close in order, with
strings, characters, comments, template strings and JavaScript regular expressions skipped. A checker that misreads an
unusual construct misreads it the same way before and after the edit, so it does not refuse.
"""
import ast
import json
import os

from . import tools

BRACES = (".java", ".kt", ".kts", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".go", ".swift", ".dart", ".scala",
          ".gradle", ".js", ".mjs", ".cjs", ".ts")
JS = (".js", ".mjs", ".cjs", ".ts")
INTERPOLATED = (".kt", ".kts", ".dart", ".gradle", ".scala")      # "${...}" inside a double-quoted string is code
OPEN, CLOSE = "([{", ")]}"
REGEX_AFTER = set("(,=:[!&|?{};>")         # ">" for an arrow function's body: x => /re/
REGEX_WORDS = ("return", "typeof", "case", "do", "else", "in", "of", "delete", "void", "throw", "new", "yield",
               "await", "default")


def balance(text, ext):
    """None when every bracket closes in order; else where it breaks ("line 12: '{' is never closed")."""
    js, interp = ext in JS, ext in INTERPOLATED
    stack = []          # [(char, line)]; the "${" of an interpolation is pushed as ("${", line)
    modes = []          # the quote of each string we are inside of, around a "${" expression
    state = {"i": 0, "line": 1}
    n = len(text)

    def scan_string(i, q):
        """Index just past the string's closing quote (or its line's end for a one-line string, or the text's end);
        at a "${" interpolation: pushes it and returns the index after it."""
        multiline = q == "`"
        code_inside = (q == "`" and js) or (q == '"' and interp)
        while i < n:
            ch = text[i]
            if ch == "\\":
                if i + 1 < n and text[i + 1] == "\n":
                    state["line"] += 1
                i += 2
                continue
            if ch == q:
                return i + 1
            if ch == "\n":
                if not multiline:
                    return i            # a one-line string ends at the line's end (an unclosed quote)
                state["line"] += 1
            if code_inside and text.startswith("${", i):
                modes.append(q)
                stack.append(("${", state["line"]))
                return i + 2
            i += 1
        return n

    i, last = 0, ""     # last: the previous significant character or word (for JavaScript regular expressions)
    while i < n:
        c = text[i]
        if c == "\n":
            state["line"] += 1
            i += 1
            continue
        if c in " \t\r":
            i += 1
            continue
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                return "line %d: a /* comment is never closed" % state["line"]
            state["line"] += text.count("\n", i, j)
            i = j + 2
            continue
        if text.startswith('"""', i) or text.startswith("'''", i):
            q = text[i:i + 3]
            j = text.find(q, i + 3)
            if j < 0:
                return "line %d: a %s string is never closed" % (state["line"], q)
            state["line"] += text.count("\n", i, j)
            i, last = j + 3, "a"
            continue
        if c in "\"'`":
            i, last = scan_string(i + 1, c), "a"
            continue
        if js and c == "/" and (last == "" or last in REGEX_AFTER or last in REGEX_WORDS):
            j, cls = i + 1, False
            while j < n and text[j] != "\n":
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == "[":
                    cls = True
                elif text[j] == "]":
                    cls = False
                elif text[j] == "/" and not cls:
                    break
                j += 1
            if j < n and text[j] == "/":
                i, last = j + 1, "a"        # a regular expression: skipped whole
                continue
        if c in OPEN:
            stack.append((c, state["line"]))
        elif c in CLOSE:
            if c == "}" and stack and stack[-1][0] == "${":
                stack.pop()
                i, last = scan_string(i + 1, modes.pop()), "a"
                continue
            if not stack:
                return "line %d: '%s' closes nothing" % (state["line"], c)
            top, at = stack[-1]
            want = CLOSE[OPEN.index(top)] if top in OPEN else "}"
            if c != want:
                return "line %d: '%s' where the '%s' from line %d should close" % (state["line"], c, top, at)
            stack.pop()
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (text[j].isalnum() or text[j] in "_$"):
                j += 1
            word = text[i:j]
            last = word if word in REGEX_WORDS else "a"
            i = j
            continue
        last = c
        i += 1
    if stack:
        top, at = stack[-1]
        return "line %d: '%s' is never closed" % (at, top)
    return None


def problem(path, text):
    """Where the file's syntax is broken, or None (also None for a language or a file that is not checked)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".py":
            ast.parse(text)
        elif ext == ".json":
            json.loads(text)
        elif ext in BRACES:
            if ext in JS and ("</" in text or "/>" in text):
                return None             # JSX: tags and their text are not code; not judged
            return balance(text, ext)
    except SyntaxError as e:
        return "line %s: %s" % (e.lineno, e.msg)
    except ValueError as e:
        return str(e)
    except (RecursionError, MemoryError):
        return None
    return None


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def _text(data):
    try:
        return data.decode("utf-8")
    except (UnicodeDecodeError, AttributeError):
        return None


def wrap(name):
    t = tools.REGISTRY.get(name)
    if t is None or getattr(t.fn, "_guard", False):
        return
    inner = t.fn

    def guarded(ctx, path=None, **args):
        try:
            p = tools.resolve(ctx, path, new=True)
        except Exception:  # noqa: BLE001 - the tool itself says what is wrong with the path
            p = None
        before = _read(p) if p and os.path.isfile(p) else None
        out = inner(ctx, path=path, **args)
        if before is None:
            return out                                  # a new file is never blocked
        old_text, new_text = _text(before), _text(_read(p))
        if old_text is None or new_text is None or old_text == new_text or problem(p, old_text):
            return out
        broken = problem(p, new_text)
        if not broken:
            return out
        with open(p, "wb") as f:                        # back exactly as it was (bytes, line endings)
            f.write(before)
        raise tools.ToolError(
            "edit refused: it breaks the syntax of %s (%s). The file is back as it was. Redo the edit so the file "
            "stays valid in one call: include the closing brackets or quotes, or replace the whole block."
            % (tools.rel(ctx, p), broken))
    guarded._guard = True
    t.fn = guarded


def install():
    for name in ("write", "edit"):
        wrap(name)
