import asyncio
import unittest
from unittest.mock import patch

from freqtrade_mcp_client import FreqtradeMcpClient
import main


class FreqtradeMcpClientTests(unittest.TestCase):
    def test_list_tools_uses_injected_lister(self) -> None:
        async def tool_lister() -> list[str]:
            return ["get_trading_status", "start_bot"]

        client = FreqtradeMcpClient(
            "http://freqtrade:8090/mcp",
            tool_lister=tool_lister,
        )

        result = asyncio.run(client.list_tools())

        self.assertEqual(result, ["get_trading_status", "start_bot"])

    def test_call_tool_uses_injected_caller(self) -> None:
        async def tool_caller(name: str, args: dict[str, object]) -> dict[str, object]:
            return {"name": name, "args": args}

        client = FreqtradeMcpClient(
            "http://freqtrade:8090/mcp",
            tool_caller=tool_caller,
        )

        result = asyncio.run(client.call_tool("force_enter_trade", {"pair": "BTC/USDT"}))

        self.assertEqual(result["name"], "force_enter_trade")
        self.assertEqual(result["args"]["pair"], "BTC/USDT")


class FakeFreqtradeMcpClient:
    async def list_tools(self) -> list[str]:
        return [
            "get_trading_status",
            "list_strategies",
            "evaluate_trade_signal",
            "get_open_trades",
            "get_closed_trades",
            "get_performance_summary",
            "get_budget_snapshot",
            "sync_dry_run_wallet",
            "start_bot",
            "stop_bot",
            "pause_trading",
            "resume_trading",
            "emit_trade_intent",
            "force_enter_trade",
            "force_exit_trade",
        ]

    async def call_tool(self, _tool_name: str, _arguments: dict[str, object]) -> dict[str, object]:
        return {"ok": True}


class FakeChainMcpClient:
    async def list_tools(self) -> list[str]:
        return ["chain_execute_trade_intent"]


class FreqtradeToolDiscoveryTests(unittest.TestCase):
    def test_discover_freqtrade_tools_registers_known_aliases(self) -> None:
        with patch.object(
            main,
            "get_freqtrade_mcp_client",
            return_value=FakeFreqtradeMcpClient(),
        ), patch.object(
            main,
            "get_chain_mcp_client",
            return_value=FakeChainMcpClient(),
        ):
            tools = main.discover_freqtrade_tools()

        tool_names = sorted(tool.name for tool in tools)
        self.assertEqual(
            tool_names,
            [
                "evaluate_trade_signal",
                "execute_freqtrade_trade_intent",
                "force_enter_trade",
                "force_exit_trade",
                "get_budget_snapshot",
                "get_closed_trades",
                "get_open_trades",
                "get_performance_summary",
                "get_trading_status",
                "list_strategies",
                "pause_trading",
                "resume_trading",
                "start_bot",
                "stop_bot",
                "sync_dry_run_wallet",
            ],
        )

    def test_discover_freqtrade_tools_keeps_independent_tools_when_chain_discovery_fails(
        self,
    ) -> None:
        with patch.object(
            main,
            "get_freqtrade_mcp_client",
            return_value=FakeFreqtradeMcpClient(),
        ), patch.object(
            main,
            "get_chain_mcp_client",
            side_effect=RuntimeError("chain unavailable"),
        ):
            tools = main.discover_freqtrade_tools()

        tool_names = {tool.name for tool in tools}
        self.assertIn("evaluate_trade_signal", tool_names)
        self.assertNotIn("execute_freqtrade_trade_intent", tool_names)


if __name__ == "__main__":
    unittest.main()
