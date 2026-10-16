"""Real harness tests for deferred MCP schema activation and session safety."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from . import tool_search


def catalog(count=70):
    records = []
    for i in range(count):
        name = "mcp__android__operation_%02d" % i
        desc = "Android phone operation number %02d" % i
        if i == 47:
            name = "mcp__android__tap"
            desc = "Tap the Android phone touchscreen button"
        if i == 49:
            name = "mcp__android__notifications_read"
            desc = "Read phone notifications after Android user permission"
        records.append({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": {}}}})
    return records


class ToolSearchTests(unittest.TestCase):
    def session(self, **changes):
        state = {
            "discovered_mcp_tools": [], "active_objective": "tap phone button",
            "root": "/tmp",
            "goal": "", "tool_names": ["read", "tool_search"], "id": "fake-1",
            "save_meta": Mock(),
        }
        state.update(changes)
        return SimpleNamespace(**state)

    def test_small_catalog_remains_compatible(self):
        rows = catalog(12)
        self.assertEqual(tool_search.visible_schemas(rows, self.session()), rows)

    def test_large_catalog_defers_unrelated_tools_but_retains_relevant(self):
        rows = catalog(70)
        visible = tool_search.visible_schemas(rows, self.session())
        names = {x["function"]["name"] for x in visible}
        self.assertIn("mcp__android__tap", names)
        self.assertLessEqual(len(visible), tool_search.INITIAL_LIMIT)
        self.assertNotIn("mcp__android__notifications_read", names)

    def test_search_is_deterministic_and_limited(self):
        rows = catalog(70)
        names = [x["function"]["name"] for x in tool_search.search_schemas(
            rows, "notifications read", 8)]
        self.assertEqual(names[0], "mcp__android__notifications_read")
        with self.assertRaises(Exception):
            tool_search.search_schemas(rows, "", 8)
        with self.assertRaises(Exception):
            tool_search.search_schemas(rows, "tap", True)
        with self.assertRaises(Exception):
            tool_search.search_schemas(rows, "a" * 301, 4)

    def test_search_selects_without_invoking_tool_and_persists_selection(self):
        rows = catalog(70)
        manager = SimpleNamespace(schemas=Mock(return_value=rows), call=Mock())
        sess = self.session()
        agent = SimpleNamespace(mcp=manager, session=sess, agent_def=None,
                                _schemas=[object()])
        found = tool_search.activate(agent, "notifications_read")
        self.assertEqual(found["newly_available"], ["mcp__android__notifications_read"])
        self.assertIn("mcp__android__notifications_read", sess.discovered_mcp_tools)
        self.assertIsNone(agent._schemas)
        sess.save_meta.assert_called_once()
        manager.call.assert_not_called()
        found = tool_search.activate(agent, "notifications_read")
        self.assertEqual(found["newly_available"], [])
        sess.save_meta.assert_called_once()
        self.assertIn("mcp__android__notifications_read",
                      [s["function"]["name"] for s in tool_search.visible_schemas(rows, sess)])

    def test_subagent_tool_allowlist_cannot_be_bypassed(self):
        rows = catalog(70)
        sess = self.session()
        agent = SimpleNamespace(
            mcp=SimpleNamespace(schemas=lambda: rows), session=sess,
            agent_def={"tools": ["read", "mcp__android__tap"]}, _schemas=None)
        found = tool_search.activate(agent, "notifications")
        self.assertFalse(found["newly_available"])
        self.assertFalse(sess.discovered_mcp_tools)
        found = tool_search.activate(agent, "tap")
        self.assertEqual(found["newly_available"], ["mcp__android__tap"])

    def test_session_record_accepts_only_existing_tool_names(self):
        rows = catalog(70)
        sess = self.session(discovered_mcp_tools=[
            "mcp__evil__exfiltrate", "mcp__android__tap"])
        visible = {s["function"]["name"] for s in tool_search.visible_schemas(rows, sess)}
        self.assertIn("mcp__android__tap", visible)
        self.assertNotIn("mcp__evil__exfiltrate", visible)

    def test_agent_exposes_tools_on_next_schema_refresh(self):
        from .agent import Agent
        from . import connectors
        rows = catalog(70)
        sess = self.session(active_objective="work on a document")
        manager = SimpleNamespace(schemas=Mock(return_value=rows), stop_all=Mock())
        a = Agent.__new__(Agent)
        a.session = sess
        a.mcp = manager
        a.agent_def = None
        a._schemas = None
        a._custom_mcp_revision = 0
        a.tool_names = lambda: ["read", "tool_search"]
        with patch.object(connectors, "names", return_value=[]):
            initial = {d["function"]["name"] for d in a.schemas()}
            self.assertIn("tool_search", initial)
            self.assertNotIn("mcp__android__notifications_read", initial)
            result = tool_search.activate(a, "notifications_read")
            self.assertIn("mcp__android__notifications_read", result["newly_available"])
            refreshed = {d["function"]["name"] for d in a.schemas()}
            self.assertIn("mcp__android__notifications_read", refreshed)
            self.assertIn("read", refreshed)


if __name__ == "__main__":
    unittest.main()
