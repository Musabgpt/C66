"""Applies OpenClaw Node capabilities (openclaw_node.py) to MusabAI's Python engine and lean phone prompt."""
import shutil

PROMPT_ADDITION = (
    "- Fast background node actions (no screen navigation needed): `notifications_read` (with optional `query`) lists "
    "notifications and marks replyable ones; `notification_reply` (with `target` and `text`) replies to a WhatsApp, "
    "Telegram or SMS notification directly in the background; `contacts` searches contacts by Arabic/English name "
    "(and `sms`/`call` also accept a contact's name in `number`); `calendar` and `calendar_add` read or schedule "
    "events; `location` returns GPS coordinates and a Maps link; `media` controls playback (`play_pause`, `next`, "
    "`previous`); `sensors` reads steps, motion and environment sensors.\n"
)


def apply(src, package, replace):
    for name in ("openclaw_node.py", "ext_openclaw_tests.py"):
        shutil.copyfile(src / name, package / name)

    replace(
        package / "tools.py",
        "_vital.install()\n",
        "_vital.install()\nfrom . import openclaw_node as _openclaw_node\n_openclaw_node.install()\n",
    )

    lean_md = package / "agent_prompt_lean.md"
    if lean_md.exists():
        text = lean_md.read_text(encoding="utf-8")
        anchor = "- Otherwise start with phone open_app"
        if anchor in text and "notification_reply" not in text:
            lean_md.write_text(text.replace(anchor, PROMPT_ADDITION + anchor, 1), encoding="utf-8")

    with open(package / "claude_parity_tests.py", "a", encoding="utf-8") as f:
        f.write("\nfrom .ext_openclaw_tests import OpenClawNodeTests  # noqa: E402,F401 - run with the parity tests in CI\n")
