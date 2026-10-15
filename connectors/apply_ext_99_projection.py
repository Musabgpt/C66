"""A long thread sent to a remote model keeps its tool calls as calls (request_context.py), and a model that copies a
shortened value from the history into a new call is refused before the call runs."""
import shutil


def apply(src, package, replace):
    shutil.copyfile(src / "ext_projection_tests.py", package / "ext_projection_tests.py")

    replace(package / "tool_protocol.py",
            "            name, args = repair.normalize(name, args, schemas)\n",
            "            name, args = repair.normalize(name, args, schemas)\n"
            "            from .request_context import OMITTED_RE\n"
            "            if OMITTED_RE.search(json.dumps(args, ensure_ascii=False)):\n"
            "                raise ValueError('an argument copies a shortened value from the history; '\n"
            "                                 'send the full value')\n")

    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .ext_projection_tests import ProjectionTests  # noqa: E402,F401 - run with the parity tests in CI\n")
