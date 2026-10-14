"""code_check: the language servers VS Code uses (pyright, typescript-language-server), without VS Code, run in Termux
(code_check.py, lsp_once.py). On the phone the certain errors they find in the files a turn changed also hold the turn
back before it says it is done, like the built-in checks."""
import shutil


def apply(src, package, replace):
    for name in ("code_check.py", "lsp_once.py", "ext_codecheck_tests.py"):
        shutil.copyfile(src / name, package / name)

    tl = package / "tools.py"
    replace(tl, "_phone_fast.install()\n",
            "_phone_fast.install()\nfrom . import code_check as _code_check\n_code_check.install()\n")
    replace(tl, '    if settings.user().get("web", True):\n        names += ["web_search", "web_fetch"]\n',
            '    names.append("code_check")\n'
            '    if settings.user().get("web", True):\n        names += ["web_search", "web_fetch"]\n')

    replace(package / "verify_lint.py", '    if not out:\n        return ""\n',
            '    if len(out) < limit:\n'
            '        try:\n'
            '            from . import code_check, lean\n'
            '            if lean.active():\n'
            '                out += code_check.gate(ctx, limit - len(out))\n'
            '        except Exception:  # noqa: BLE001 - a check never breaks the turn\n'
            '            pass\n'
            '    if not out:\n        return ""\n')

    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .ext_codecheck_tests import CodeCheckTests  # noqa: E402,F401 - run with the parity tests in CI\n")
