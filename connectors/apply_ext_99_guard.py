"""An edit that breaks a file's syntax is refused and the file put back as it was (guard.py, after SWE-agent)."""
import shutil


def apply(src, package, replace):
    for name in ("guard.py", "ext_guard_tests.py"):
        shutil.copyfile(src / name, package / name)
    # wraps write and edit last, around every other layer
    replace(package / "tools.py", "_task_state.install()\n",
            "_task_state.install()\nfrom . import guard as _guard\n_guard.install()\n")
    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .ext_guard_tests import GuardTests  # noqa: E402,F401 - run with the parity tests in CI\n")
