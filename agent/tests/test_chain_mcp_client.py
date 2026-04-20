import asyncio
import unittest
from unittest.mock import patch

from chain_mcp_client import ChainMcpClient
import main


class ChainMcpClientTests(unittest.TestCase):
    def test_list_tools_uses_injected_lister(self) -> None:
        async def tool_lister() -> list[str]:
            return ["chain_sign_transfer", "chain_submit_execution"]

        client = ChainMcpClient(
            "http://chain-mcp:8091/mcp",
            tool_lister=tool_lister,
        )

        result = asyncio.run(client.list_tools())

        self.assertEqual(result, ["chain_sign_transfer", "chain_submit_execution"])

    def test_call_tool_uses_injected_caller(self) -> None:
        async def tool_caller(name: str, args: dict[str, object]) -> dict[str, object]:
            return {"name": name, "args": args, "isError": False}

        client = ChainMcpClient(
            "http://chain-mcp:8091/mcp",
            tool_caller=tool_caller,
        )

        result = asyncio.run(
            client.call_tool(
                "chain_submit_execution",
                {"to": "0x1111111111111111111111111111111111111111", "valueEth": "0.001"},
            )
        )

        self.assertEqual(result["name"], "chain_submit_execution")
        self.assertEqual(result["args"]["valueEth"], "0.001")


class FakeChainMcpClient:
    async def list_tools(self) -> list[str]:
        return [
            "chain_get_wallet_state",
            "chain_sign_transfer",
            "chain_submit_execution",
            "chain_submit_user_operation",
            "chain_x402_fetch",
        ]

    async def call_tool(self, _tool_name: str, _arguments: dict[str, object]) -> dict[str, object]:
        return {"ok": True, "isError": False}


class ChainToolDiscoveryTests(unittest.TestCase):
    def test_discover_chain_tools_registers_known_aliases(self) -> None:
        with patch.object(main, "get_chain_mcp_client", return_value=FakeChainMcpClient()):
            tools = main.discover_chain_tools()

        tool_names = sorted(tool.name for tool in tools)
        self.assertEqual(
            tool_names,
            [
                "chain_get_wallet_state",
                "chain_sign_transfer",
                "chain_submit_execution",
                "chain_submit_user_operation",
                "chain_x402_fetch",
            ],
        )


if __name__ == "__main__":
    unittest.main()
