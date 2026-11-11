"""Check the engine actually packaged in the APK, rather than only the source checkout."""
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    required = ["automation.py", "auto_update.py", "automation_tests.py", "auto_update_tests.py", "native-compat.json", "agent.py", "tools.py", "server.py", "plugins.py", "addons.py", "mcp.py", "session.py",
                "autonomy.py", "autonomy_tests.py", "connectors.py", "documents.py", "evolution.py", "memory_api.py", "memory_tests.py", "document_tests.py", "evolution_tests.py", "addon_tests.py", "assistant.py", "assistant_tests.py", "ui/index.html", "ui/assistant.js", "ui/assistant.css", "ui/markdown.js"]
    for path in required:
        assert "newal_code/" + path in archive.namelist(), "Missing packaged feature: " + path
    assert "pypdf/__init__.py" in archive.namelist(), "PDF reader missing from packaged engine"
    for path in ('agent_policy.py', 'agent_prompt.md', 'prompt_tests.py', 'workbench.py', 'workbench_tests.py', 'mcp_config.py', 'mcp_config_tests.py', 'mcp_bundles.py', 'provider_pool.py', 'provider_pool_tests.py'):
        assert 'newal_code/' + path in archive.namelist(), 'Prompt feature missing: ' + path
    evolution = archive.read("newal_code/evolution.py").decode()
    for anchor in ("class SelfUpdateManager", "ready_to_activate", "failed_activation", "dynamic_file", "self_update_stage", "self_update_verify"):
        assert anchor in evolution, "Phase 9 self-update feature missing: " + anchor
    server = archive.read("newal_code/server.py").decode()
    assert 'evolution.dynamic_file("ui/" + rel)' in server, "Verified hot UI serving missing"
    policy = archive.read("newal_code/agent_policy.py").decode()
    assert "evolution.dynamic_file('agent_prompt.md')" in policy, "Verified hot prompt serving missing"
    # The page is the personal assistant's one screen, and nothing of the developer interface ships with it.
    page = archive.read("newal_code/ui/index.html").decode()
    assert "assistant.js" in page and "app.js" not in page, "The app's page is not the assistant's"
    script = archive.read("newal_code/ui/assistant.js").decode()
    for anchor in ("/api/sessions", "/api/approvals/", "/api/assistant", "onPhoneVoice", "onPhoneShared"):
        assert anchor in script, "Assistant page feature missing: " + anchor
    extra = sorted(n for n in archive.namelist() if n.startswith("newal_code/ui/")
                   and n.rsplit("/", 1)[-1] not in ("index.html", "assistant.js", "assistant.css", "markdown.js", "icon.svg", "favicon.ico"))
    assert not extra, "Developer interface files shipped: " + ", ".join(extra[:6])
    prompt = archive.read("newal_code/agent_prompt_assistant.md").decode()
    assert prompt.startswith("You are MusabAI, the user's personal assistant"), "The assistant's prompt is not packaged"
    assert "MUSAB_CONNECTORS_V1" in archive.read("newal_code/tools.py").decode()
print("Packaged assistant page and prompt, connectors and Phase 9 update gates verified")

