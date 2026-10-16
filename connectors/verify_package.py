"""Check the engine actually packaged in the APK, rather than only the source checkout."""
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1]) as archive:
    required = ["automation.py", "auto_update.py", "automation_tests.py", "auto_update_tests.py", "native-compat.json", "ui/automatic_updates.js", "agent.py", "tools.py", "server.py", "plugins.py", "addons.py", "mcp.py", "session.py",
                "autonomy.py", "autonomy_tests.py", "connectors.py", "documents.py", "evolution.py", "memory_api.py", "memory_tests.py", "document_tests.py", "evolution_tests.py", "addon_tests.py", "ui/workspace.js", "ui/app.js", "ui/connectors.js", "ui/connectors.css"]
    for path in required:
        assert "newal_code/" + path in archive.namelist(), "Missing packaged feature: " + path
    assert "pypdf/__init__.py" in archive.namelist(), "PDF reader missing from packaged engine"
    for path in ('agent_policy.py', 'agent_prompt.md', 'prompt_tests.py', 'workbench.py', 'workbench_tests.py', 'mcp_config.py', 'mcp_config_tests.py', 'mcp_bundles.py', 'provider_pool.py', 'provider_pool_tests.py', 'ui/mcp_ui.js'):
        assert 'newal_code/' + path in archive.namelist(), 'Prompt feature missing: ' + path
    evolution = archive.read("newal_code/evolution.py").decode()
    for anchor in ("class SelfUpdateManager", "ready_to_activate", "failed_activation", "dynamic_file", "self_update_stage", "self_update_verify"):
        assert anchor in evolution, "Phase 9 self-update feature missing: " + anchor
    server = archive.read("newal_code/server.py").decode()
    assert 'evolution.dynamic_file("ui/" + rel)' in server, "Verified hot UI serving missing"
    policy = archive.read("newal_code/agent_policy.py").decode()
    assert "evolution.dynamic_file('agent_prompt.md')" in policy, "Verified hot prompt serving missing"
    app = archive.read("newal_code/ui/app.js").decode()
    for anchor in ("open-extensions", "toggle-terminal", "toggle-review", "env-picker", "/api/workspaces"):
        assert anchor in app, "Original UI feature missing: " + anchor
    assert "connectors.js" in archive.read("newal_code/ui/index.html").decode()
    assert "MUSAB_CONNECTORS_V1" in archive.read("newal_code/tools.py").decode()
print("Packaged original features, connectors and Phase 9 update gates verified")

