import asyncio
import unittest

from mcp_tool_metadata import McpToolMetadata
from tool_schema import make_mcp_structured_tool


class ToolSchemaTests(unittest.TestCase):
    def test_make_tool_converts_json_schema_to_structured_tool(self) -> None:
        calls = []

        async def invoker(tool_name: str, arguments: dict[str, object]) -> dict[str, object]:
            calls.append((tool_name, arguments))
            return {"ok": True, "arguments": arguments}

        tool = make_mcp_structured_tool(
            McpToolMetadata(
                name="route_payment_intent",
                description="Route payment intent.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "purpose": {"type": "string", "description": "Payment purpose"},
                        "requiresAcceptance": {"type": "boolean", "default": False},
                    },
                    "required": ["purpose"],
                },
            ),
            invoker,
        )

        result = asyncio.run(tool.ainvoke({"purpose": "research"}))

        self.assertEqual(tool.name, "route_payment_intent")
        self.assertEqual(result["ok"], True)
        self.assertEqual(calls, [("route_payment_intent", {"purpose": "research"})])


if __name__ == "__main__":
    unittest.main()
