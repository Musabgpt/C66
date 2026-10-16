import unittest

from . import orchestrator


class OrchestratorTests(unittest.TestCase):
    def test_code_task_prefers_rag_then_inspection_and_tests(self):
        names = {"project_rag_search", "read", "grep", "bash", "memory_recall"}
        result = orchestrator.plan(names, "Fix the Gradle build error in this project")
        tools = [step["tool"] for step in result["steps"]]
        self.assertEqual(tools[0], "project_rag_search")
        self.assertIn("read", tools)
        self.assertIn("bash", tools)
        self.assertNotIn("search_router", tools)

    def test_web_document_task_uses_only_available_routers(self):
        names = {"search_router", "document_engine_selector"}
        result = orchestrator.plan(names, "Research the latest PDF and extract its tables")
        tools = [step["tool"] for step in result["steps"]]
        self.assertEqual(tools, ["document_engine_selector", "search_router"])

    def test_never_invents_unavailable_tools(self):
        names = {"read"}
        result = orchestrator.plan(names, "Open the browser and log in")
        self.assertTrue(all(step["tool"] in names for step in result["steps"]))
        self.assertNotIn("browser_tool_selector", [step["tool"] for step in result["steps"]])

    def test_phone_task_prefers_termux_only_when_exposed(self):
        names = {"phone"}
        result = orchestrator.plan(names, "Run this Android app on my phone")
        self.assertEqual([s["tool"] for s in result["steps"]], ["phone"])
        names.add("termux_exec")
        result = orchestrator.plan(names, "Run this Android app in Termux")
        self.assertEqual([s["tool"] for s in result["steps"]][:2], ["termux_exec", "phone"])

    def test_plan_is_bounded(self):
        names = {
            "project_rag_search", "memory_recall", "document_engine_selector",
            "browser_tool_selector", "search_router", "searxng_search",
            "termux_exec", "phone", "read", "grep", "bash",
        }
        result = orchestrator.plan(
            names,
            "Remember the previous project, fix code, research web, open browser, inspect PDF, run Android Termux tests",
        )
        self.assertLessEqual(len(result["steps"]), 8)


if __name__ == "__main__":
    unittest.main()
