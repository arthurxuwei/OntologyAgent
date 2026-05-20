import unittest
from unittest.mock import patch

import main
from langchain_core.tools import StructuredTool


async def _fake_tool() -> dict[str, object]:
    return {"ok": True}


def _make_test_tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(name=name, description=name, coroutine=_fake_tool)


class MainToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        main.clear_tool_cache()
        main.get_agent_graph.cache_clear()
        main.get_skill_catalog.cache_clear()

    def test_build_tools_uses_rest_registry(self) -> None:
        with patch.object(
            main,
            "build_rest_tools",
            return_value=[_make_test_tool("route_payment_intent")],
        ):
            tools = main.build_tools()

        self.assertEqual({tool.name for tool in tools}, {"route_payment_intent"})

    def test_get_agent_prompt_includes_dynamic_skills(self) -> None:
        class Catalog:
            skills = ()

            def instructions_text(self):
                return "\n\nAvailable skills:\nSkill: demo\nDynamic skill instruction."

        with patch.object(main, "get_skill_catalog", return_value=Catalog()):
            prompt = main.get_agent_prompt()

        self.assertIn("You are OntologyAgent.", prompt)
        self.assertIn("Dynamic skill instruction.", prompt)

    def test_clear_tool_cache_resets_agent_graph(self) -> None:
        main.clear_tool_cache()


if __name__ == "__main__":
    unittest.main()
