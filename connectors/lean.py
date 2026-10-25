"""MusabAI on the phone, focused: the agent gets the tools of Termux, GitHub, Chrome and the phone, and nothing else.

* Termux: bash, runtime_exec and runtime_process_* (node, npm, python, git and gh live there), termux_exec/result.
* GitHub: github, github_monitor and the connected account's github_* operations.
* Code checks: code_check (the language servers VS Code uses: pyright, typescript-language-server, in Termux).
* Chrome and the web: web_search, web_fetch; pages and local servers open in Chrome with the phone's open_url.
* The phone itself: phone, screen_look and phone_routine (saved routines replayed in one call).

Everything else (documents, MCP servers and connected services other than GitHub and Termux, skills, sub-agents and
background agents, memory, self-evolution, observability, routers, RAG, templates...) stays in the code but is not
offered: fewer tools is a shorter request, a faster model and fewer wrong choices. settings "lean": false brings the
full set back.
"""

LEAN = (
    "read", "edit", "write", "glob", "grep", "bash", "job", "todo",
    "runtime_exec", "runtime_process_start", "runtime_process_status", "runtime_process_stop",
    "github", "github_monitor",
    "code_check",
    "web_search", "web_fetch",
    "phone", "screen_look", "phone_routine", "phone_sequence", "toolchain_status",
)
CONNECTOR_PREFIXES = ("github_", "termux_")
SERVICES = ("github", "termux")            # the connections the app shows


def active():
    """On the phone (the app or its Termux link), unless the user turned the full set back on."""
    try:
        from . import phone, settings
        return bool(settings.user().get("lean", True)) and phone.available()
    except Exception:  # noqa: BLE001 - a broken settings file never hides the core tools
        return False


def tools(names):
    """The built-in tools offered, in their order."""
    return [n for n in names if n in LEAN]


def connector(name):
    return name.startswith(CONNECTOR_PREFIXES) and not name.startswith("connected_")
