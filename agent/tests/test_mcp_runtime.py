import asyncio
import unittest

from mcp_runtime import McpRuntime
from mcp_tool_metadata import McpToolMetadata
from skill_loader import McpServerDefinition, SkillCatalog, SkillDefinition


class FakeClient:
    def __init__(self, tools):
        self.tools = tools
        self.calls = []

    async def describe_tools(self):
        return self.tools

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return {"isError": False, "value": {"tool": tool_name, "arguments": arguments}}


class McpRuntimeTests(unittest.TestCase):
    def test_runtime_exposes_only_skill_allowlisted_tools(self) -> None:
        client = FakeClient(
            [
                McpToolMetadata("route_payment_intent", "Route payments.", {"type": "object"}),
                McpToolMetadata("internal_debug_tool", "Debug.", {"type": "object"}),
            ]
        )
        catalog = SkillCatalog(
            skills=(
                SkillDefinition(
                    name="payment-routing",
                    description="",
                    mcp_tools={"ledger": ("route_payment_intent",)},
                    hidden_mcp_tools={"ledger": ("internal_debug_tool",)},
                    mcp_servers={
                        "ledger": McpServerDefinition(
                            name="ledger",
                            url="http://ledger:8092/mcp/",
                            tools=("route_payment_intent",),
                            hidden_tools=("internal_debug_tool",),
                        )
                    },
                ),
            )
        )
        runtime = McpRuntime(catalog, client_factory=lambda _server, _url: client)

        tools = asyncio.run(runtime.discover_tools({}))

        self.assertEqual([tool.name for tool in tools], ["route_payment_intent"])
        self.assertEqual(runtime.health()["ledger"]["status"], "ok")

    def test_runtime_uses_skill_declared_url_without_environment_variable(self) -> None:
        client = FakeClient(
            [McpToolMetadata("chain_get_wallet_state", "Wallet state.", {"type": "object"})]
        )
        captured = []
        catalog = SkillCatalog(
            skills=(
                SkillDefinition(
                    name="chain-wallet",
                    description="",
                    mcp_tools={"chain": ("chain_get_wallet_state",)},
                    mcp_servers={
                        "chain": McpServerDefinition(
                            name="chain",
                            url="http://chain-mcp:8091/mcp/",
                            tools=("chain_get_wallet_state",),
                        )
                    },
                ),
            )
        )
        runtime = McpRuntime(
            catalog,
            client_factory=lambda server, url: captured.append((server, url)) or client,
        )

        tools = asyncio.run(runtime.discover_tools({}))

        self.assertEqual([tool.name for tool in tools], ["chain_get_wallet_state"])
        self.assertEqual(captured, [("chain", "http://chain-mcp:8091/mcp/")])


if __name__ == "__main__":
    unittest.main()
