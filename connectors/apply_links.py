"""One-tap Termux + GitHub link, applied by apply_parity.py after apply.py.

The setup Termux runs also installs gh, signs it in (with the app's GitHub token when it has one, else gh's own
browser sign-in once), gives git the same login and hands the token back to the app, so the app, gh and git in
Termux all use one GitHub account. The app runs that setup in Termux by itself when Android lets it (Termux's
RUN_COMMAND); otherwise it copies the command for a single paste.
"""

END = "newal-termux stop > /dev/null 2>&1 || true\nnewal-termux start\n" + "'" * 3

GH_BLOCK = r"""# GitHub: one login for gh and git in Termux and for the app, with full repository rights.
if command -v gh > /dev/null 2>&1; then
  if ! gh auth status -h github.com > /dev/null 2>&1; then
    TOK="$(curl -fsS --max-time 10 -H "X-NewAl-Key: $KEY" "$APP/api/github/token" 2>/dev/null || true)"
    if [ -n "$TOK" ]; then
      printf '%s\n' "$TOK" | gh auth login -h github.com --with-token || true
    elif ( : < /dev/tty ) 2> /dev/null; then
      echo "GitHub: sign in once. A code appears below and the browser opens: enter the code and approve."
      BROWSER=termux-open-url gh auth login -h github.com -p https -w -s repo,workflow,read:org,gist,user:email < /dev/tty || true
    fi
  fi
  if gh auth status -h github.com > /dev/null 2>&1; then
    gh auth setup-git -h github.com > /dev/null 2>&1 || true
    TOK="$(gh auth token -h github.com 2>/dev/null || true)"
    LOGIN="$(gh api user --jq .login 2>/dev/null || true)"
    if [ -n "$LOGIN" ]; then
      git config --global user.name > /dev/null 2>&1 || git config --global user.name "$LOGIN"
      git config --global user.email > /dev/null 2>&1 || git config --global user.email "$(gh api user --jq '(.id|tostring) + "+" + .login + "@users.noreply.github.com"')"
    fi
    if [ -n "$TOK" ]; then
      curl -fsS --max-time 30 -H "X-NewAl-Key: $KEY" -H "Content-Type: application/json" -d "{\"token\": \"$TOK\"}" "$APP/api/github/connect" > /dev/null 2>&1 || true
      PYTHONPATH="$H/app" NEWAL_CODE_HOME="$HOME/.newal-code" python -c 'import sys; from newal_code import github; github.connect(sys.argv[1])' "$TOK" > /dev/null 2>&1 || true
      echo "GitHub: connected${LOGIN:+ as @$LOGIN} (gh, git, NewAl Code)."
    fi
  else
    echo "GitHub: not signed in. Run this setup again, or: gh auth login"
  fi
fi
"""

PHONE_JS = r"""  async function musabTermuxLink(command) {
    // One tap: run the setup in Termux by itself when the app may (RUN_COMMAND); otherwise copy it for one paste.
    const t = (phoneStatus() || {}).termux || {};
    if (!t.installed) { NewAlPhone.termuxSetup(""); return; }
    if (!t.allowed && NewAlPhone.termuxAllow) {
      NewAlPhone.termuxAllow();
      for (let i = 0; i < 20 && !((phoneStatus() || {}).termux || {}).allowed; i++) await new Promise(r => setTimeout(r, 1000));
    }
    const before = Date.now() / 1000;
    if (((phoneStatus() || {}).termux || {}).allowed && NewAlPhone.termuxRunVisible && NewAlPhone.termuxRunVisible(command) === "started") {
      for (let i = 0; i < 12; i++) {
        await new Promise(r => setTimeout(r, 1000));
        try {
          const s = await api("/api/termux/state");
          if (s.setup_started >= before - 1) { toast("Setting up Termux and GitHub… follow the steps in Termux", 7000); pollTermuxUp(); return; }
        } catch (_) { /* engine busy */ }
      }
      command = (await api("/api/termux/link", {})).command;   // the token is single use: a fresh one to paste
    }
    NewAlPhone.termuxSetup(command);
    toast("First time only: in Termux press CTRL+C, then paste and press Enter", 10000);
    pollTermuxUp();
  }
  function pollTermuxUp() {
    const wait = setInterval(() => {
      const s2 = phoneStatus();
      if (s2 && s2.termux && s2.termux.up) { clearInterval(wait); localStorage.setItem("nc.termux.linked", "1"); toast("NewAl Code runs in Termux"); }
    }, 3000);
    setTimeout(() => clearInterval(wait), 600000);
  }
"""

GITHUB_JS = """      if (window.NewAlPhone && NewAlPhone.termuxRunVisible) box.insertAdjacentHTML("beforeend",
        '<div class="form-row"><button class="btn" id="gh-termux">Sign in with GitHub (one tap, via Termux)</button></div>');
      const ghT = box.querySelector("#gh-termux");
      if (ghT) ghT.onclick = async () => {
        await musabTermuxLink((await api("/api/termux/link", {})).command);
        const w = setInterval(async () => {
          try {
            const a2 = await api("/api/github");
            if (a2.connected) { clearInterval(w); toast("GitHub: connected" + (a2.login ? " as @" + a2.login : "")); (after || openSettings)(); }
          } catch (_) { /* keep waiting */ }
        }, 4000);
        setTimeout(() => clearInterval(w), 600000);
      };
"""


def apply(package, replace):
    tx = package / "termux.py"
    replace(tx, "_once = {}\n", "_once = {}\n_setup_used = [0.0]\n")
    replace(tx, '        exp = _once.pop(token or "", 0)\n    return exp > time.time()\n',
            '        exp = _once.pop(token or "", 0)\n    ok = exp > time.time()\n    if ok:\n'
            '        _setup_used[0] = time.time()\n    return ok\n')
    replace(tx, 'command -v git > /dev/null 2>&1 || need="$need git"\n',
            'command -v git > /dev/null 2>&1 || need="$need git"\ncommand -v gh > /dev/null 2>&1 || need="$need gh"\n')
    replace(tx, END, END[:-3] + GH_BLOCK + END[-3:])

    sv_get = '            if path == "/api/github":\n                from . import github\n                return self._json(github.account())\n'
    replace(package / "server.py", sv_get,
            '            if path == "/api/github/token":\n                from . import github\n'
            '                return self._text(github.token() or "", "text/plain")\n'
            '            if path == "/api/termux/state":\n                from . import termux\n'
            '                return self._json({"setup_started": termux._setup_used[0]})\n' + sv_get)

    app = package / "ui" / "app.js"
    replace(app, '        const d = await api("/api/termux/link", {});\n        NewAlPhone.termuxSetup(d.command);\n'
                 '        const wait = setInterval(',
            '        const d = await api("/api/termux/link", {});\n        await musabTermuxLink(d.command);\n'
            '        const wait = setInterval(')
    replace(app, "  function phoneSection(box) {\n", PHONE_JS + "  function phoneSection(box) {\n")
    replace(app, '<button class="btn primary" id="ph-tx-link">Connect Termux</button>',
            '<button class="btn primary" id="ph-tx-link">Connect Termux + GitHub</button>')
    replace(app, "      const byToken = () => oneTap(GITHUB,", GITHUB_JS + "      const byToken = () => oneTap(GITHUB,")
