"""The extensions' tests run with the parity tests (CI runs newal_code.claude_parity_tests)."""
import shutil


def apply(src, package, replace):
    shutil.copyfile(src / "ext_tests.py", package / "ext_tests.py")
    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\n\nfrom .ext_tests import ExtensionTests  # noqa: E402,F401 - run with the parity tests in CI\n")
