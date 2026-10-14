"""The Termux loop on the phone (termux_loop.py): bash runs in Termux in the project folder, Termux commands start in the
project folder, local pages open in Chrome, and GitHub has one token with full rights in the app, gh and git."""
import shutil

FINE_GRAINED = ("This is a fine-grained token: it pushes only to the repositories it was given with Contents: Read "
                "and write, and never to new ones. A classic token with repo and workflow has no such limit.")
FINE_GRAINED_AR = ("هذا توكن مقيّد (fine-grained): يرفع فقط إلى المستودعات التي أُعطيت له بصلاحية Contents: Read and "
                   "write، ولا يرفع أبداً إلى مستودع جديد. التوكن الكلاسيكي بصلاحيتي repo وworkflow ليس فيه هذا القيد.")


def apply(src, package, replace):
    for name in ("termux_loop.py", "ext_loop_tests.py"):
        shutil.copyfile(src / name, package / name)

    tl = package / "tools.py"
    replace(tl, "_code_check.install()\n",
            "_code_check.install()\nfrom . import termux_loop as _termux_loop\n_termux_loop.install()\n")

    # runtime_exec and runtime_process_start start in the project folder (they started in Termux's home)
    rm = package / "runtime_manager.py"
    replace(rm, "        data = execute(command, runtime)\n",
            "        from . import termux_loop\n"
            "        data = execute(termux_loop.in_project(ctx, command, runtime), runtime)\n")
    replace(rm, "        data = process_start(command)\n",
            "        from . import termux_loop\n"
            "        data = process_start(termux_loop.in_project(ctx, command))\n")

    # GitHub: gh's minimum scopes (repo, read:org) on the token page; the kind of token; the app's token in Termux
    gh = package / "github.py"
    replace(gh, 'TOKEN_PAGE = ("https://github.com/settings/tokens/new?scopes=repo,workflow,read:user,user:email"',
            'TOKEN_PAGE = ("https://github.com/settings/tokens/new?scopes=repo,workflow,read:org,gist,read:user,'
            'user:email"')
    replace(gh, '    settings.save({"keys": dict(cfg.get("keys") or {}, github=tok), "github": profile})\n'
                '    return profile\n',
            '    from . import termux_loop\n'
            '    profile["token_kind"] = termux_loop.token_kind(tok)\n'
            '    settings.save({"keys": dict(cfg.get("keys") or {}, github=tok), "github": profile})\n'
            '    termux_loop.github_sync_later(tok)            # gh and git in Termux use it too\n'
            '    return profile\n')

    app = package / "ui/app.js"
    replace(app, '      box.querySelector("#gh-off").onclick = async () => { await api("/api/github/disconnect", {}); '
                 'githubSection(box); };\n',
            '      box.querySelector("#gh-off").onclick = async () => { await api("/api/github/disconnect", {}); '
            'githubSection(box); };\n'
            '      if (a.token_kind === "fine-grained") {\n'
            '        box.insertAdjacentHTML("beforeend", \'<p class="muted small-note">' + FINE_GRAINED + '</p>\' +\n'
            '          \'<div class="form-row"><button class="btn primary" id="gh-classic">Use a classic token</button></div>\');\n'
            '        // only a classic or OAuth token is taken from the clipboard here (the old one may still be in it)\n'
            '        const CLASSIC = Object.assign({}, GITHUB, { pattern: "(gh[po]_[A-Za-z0-9_]{20,})" });\n'
            '        box.querySelector("#gh-classic").onclick = () => oneTap(CLASSIC, async (p, tk) => {\n'
            '          const d = await connectGitHub(p, tk); (after || openSettings)(); return d; });\n'
            '      }\n')
    replace(package / "ui/i18n.js", "  const AR = {\n",
            '  const AR = {\n'
            '    "' + FINE_GRAINED + '": "' + FINE_GRAINED_AR + '",\n'
            '    "Use a classic token": "استخدم توكناً كلاسيكياً",\n')

    # the app (built after this step): open_url opens the link in the given app (Chrome for a local server)
    phone_java = package.parent.parent / "android-lite/app/src/main/java/dev/newal/code/lite/Phone.java"
    if phone_java.exists():
        text = phone_java.read_text(encoding="utf-8")
        before = ('return A11y.withScreen(start(ctx, safeUrl(ctx, a.optString("url")),\n'
                  '                        "opened " + a.optString("url")), true);')
        after = ('return A11y.withScreen(start(ctx, a.optString("package").isEmpty() ? safeUrl(ctx, a.optString("url"))\n'
                 '                        : safeUrl(ctx, a.optString("url")).setPackage(a.optString("package")),\n'
                 '                        "opened " + a.optString("url")), true);')
        if before not in text:
            raise SystemExit("Unsafe patch refused: open_url not found in Phone.java")
        phone_java.write_text(text.replace(before, after, 1), encoding="utf-8")

    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .ext_loop_tests import LoopTests  # noqa: E402,F401 - run with the parity tests in CI\n")
