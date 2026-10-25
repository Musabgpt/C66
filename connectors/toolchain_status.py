"""Fast Android command inventory without installing duplicate Toybox packages."""
import os
import re
import subprocess
import time

from . import tools

NAMES = ["toolchain_status"]
_CACHE = (0.0, None)


def _applets(path, listing):
    if not path or not os.path.isfile(path) or not os.access(path, os.X_OK):
        return []
    try:
        done = subprocess.run([path] + listing, capture_output=True, text=True, timeout=3,
                              check=False)
    except (OSError, subprocess.TimeoutExpired):
        return []
    raw = (done.stdout or "") + "\n" + (done.stderr or "")
    if listing:   # BusyBox --list prints one applet per line.
        return sorted({x.strip() for x in raw.splitlines() if re.fullmatch(r"[a-zA-Z0-9_.+\-\[\]]+", x.strip())})
    # Android's toybox prints compact, whitespace-separated command names.
    if "usage:" in raw.lower() and len(raw) < 100:
        return []
    return sorted(set(re.findall(r"(?<![\w.-])[a-z][a-z0-9_.+\-]{0,30}(?![\w.-])", raw)))


def snapshot():
    global _CACHE
    if time.monotonic() - _CACHE[0] < 30 and _CACHE[1] is not None:
        return dict(_CACHE[1])
    busybox = os.environ.get("NEWAL_BUSYBOX", "")
    toybox = os.environ.get("NEWAL_TOYBOX") or "/system/bin/toybox"
    busy = _applets(busybox, ["--list"])
    toy = _applets(toybox, [])
    try:
        from . import termux_loop
        termux = bool(termux_loop.termux_ready())
    except Exception:
        termux = False
    result = {"busybox": {"available": bool(busy), "count": len(busy), "commands": busy},
              "toybox": {"available": bool(toy), "count": len(toy), "commands": toy},
              "termux": {"connected": termux,
                         "package_installation": "pkg/apt via authenticated bridge" if termux else "connect Termux"},
              "chrome": {"package": "com.android.chrome", "control": "phone Accessibility service",
                         "forms": "phone_sequence; verify resulting screen"}}
    _CACHE = (time.monotonic(), result)
    return dict(result)


@tools.tool("toolchain_status",
            "Inspect actual on-device BusyBox and system Toybox commands and connected Termux "
            "before choosing an execution route. This does not install packages or grant permissions.",
            {}, [], "read")
def t_toolchain_status(ctx):
    data = snapshot()
    import json
    return json.dumps(data, ensure_ascii=False), data
