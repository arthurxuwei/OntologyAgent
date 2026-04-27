import asyncio
import unittest
from unittest.mock import patch

import main
from langchain_core.tools import StructuredTool


async def _fake_tool() -> dict[str, object]:
    return {"ok": True}


def _make_test_tool(name: str) -> StructuredTool:
    return StructuredTool.from_function(name=name, description=name, coroutine=_fake_tool)


class _FakeToolClient:
    def __init__(self, tools: list[str]) -> None:
        self._tools = tools

    async def list_tools(self) -> list[str]:
        return self._tools


class _FakeLedgerClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def get_state(self) -> dict[str, object]:
        self.calls.append(("get_state", {}))
        return {"accounts": [], "entries": [], "escrows": []}

    async def credit_balance(
        self, agent_id: str, *, amount_atomic: str, reason: str | None = None
    ) -> dict[str, object]:
        self.calls.append(
            (
                "credit_balance",
                {
                    "agent_id": agent_id,
                    "amount_atomic": amount_atomic,
                    "reason": reason,
                },
            )
        )
        return {"account": {"agentId": agent_id}}

    async def create_escrow(
        self,
        *,
        buyer_agent_id: str,
        seller_agent_id: str,
        amount_atomic: str,
        task_id: str | None = None,
        description: str | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            (
                "create_escrow",
                {
                    "buyer_agent_id": buyer_agent_id,
                    "seller_agent_id": seller_agent_id,
                    "amount_atomic": amount_atomic,
                    "task_id": task_id,
                    "description": description,
                },
            )
        )
        return {"escrow": {"escrowId": "escrow_1"}}

    async def release_escrow(self, escrow_id: str) -> dict[str, object]:
        self.calls.append(("release_escrow", {"escrow_id": escrow_id}))
        return {"escrow": {"escrowId": escrow_id, "status": "released"}}

    async def refund_escrow(self, escrow_id: str) -> dict[str, object]:
        self.calls.append(("refund_escrow", {"escrow_id": escrow_id}))
        return {"escrow": {"escrowId": escrow_id, "status": "refunded"}}


class MainToolRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        main.clear_discovered_tool_cache()
        main.get_agent_graph.cache_clear()
        main.get_ledger_client.cache_clear()

    def test_build_tools_includes_wealth_management_tools(self) -> None:
        with patch.object(main, "_load_discovered_chain_tools", return_value=[]), patch.object(
            main, "_load_discovered_freqtrade_tools", return_value=[]
        ):
            tools = main.build_tools()

        tool_names = [tool.name for tool in tools]
        self.assertIn("get_wealth_status", tool_names)
        self.assertIn("start_wealth_agent", tool_names)
        self.assertIn("stop_wealth_agent", tool_names)
        self.assertIn("run_wealth_tick", tool_names)
        self.assertIn("update_wealth_config", tool_names)

    def test_build_tools_includes_agent_wallet_ledger_tools(self) -> None:
        with patch.object(main, "_load_discovered_chain_tools", return_value=[]), patch.object(
            main, "_load_discovered_freqtrade_tools", return_value=[]
        ):
            tools = main.build_tools()

        tool_names = {tool.name for tool in tools}
        self.assertIn("agent_wallet_get_ledger_state", tool_names)
        self.assertIn("agent_wallet_credit_balance", tool_names)
        self.assertIn("agent_wallet_create_escrow", tool_names)
        self.assertIn("agent_wallet_release_escrow", tool_names)
        self.assertIn("agent_wallet_refund_escrow", tool_names)

    def test_build_tools_includes_payment_router_tool(self) -> None:
        with patch.object(main, "_load_discovered_chain_tools", return_value=[]), patch.object(
            main, "_load_discovered_freqtrade_tools", return_value=[]
        ):
            tools = main.build_tools()

        tool_names = {tool.name for tool in tools}
        self.assertIn("route_payment_intent", tool_names)

    def test_route_payment_intent_tool_returns_router_decision(self) -> None:
        result = asyncio.run(
            main.route_payment_intent_tool(
                purpose="commission research report",
                deliveryMode="async_task",
                requiresAcceptance=True,
                externalService=False,
            )
        )

        self.assertEqual(result["method"], "ledger_escrow")
        self.assertEqual(
            result["allowedTools"],
            [
                "agent_wallet_create_escrow",
                "agent_wallet_release_escrow",
                "agent_wallet_refund_escrow",
            ],
        )

    def test_agent_wallet_ledger_tools_call_ledger_client(self) -> None:
        client = _FakeLedgerClient()

        async def run_tools() -> None:
            with patch.object(main, "get_ledger_client", return_value=client):
                self.assertEqual(
                    await main.agent_wallet_get_ledger_state_tool(),
                    {"accounts": [], "entries": [], "escrows": []},
                )
                await main.agent_wallet_credit_balance_tool(
                    agentId="agent_buyer",
                    amountAtomic="5000000",
                    reason="demo funding",
                )
                await main.agent_wallet_create_escrow_tool(
                    buyerAgentId="agent_buyer",
                    sellerAgentId="agent_seller",
                    amountAtomic="3000000",
                    taskId="task_123",
                    description="Research task",
                )
                await main.agent_wallet_release_escrow_tool(escrowId="escrow_1")
                await main.agent_wallet_refund_escrow_tool(escrowId="escrow_2")

        asyncio.run(run_tools())

        self.assertEqual(
            client.calls,
            [
                ("get_state", {}),
                (
                    "credit_balance",
                    {
                        "agent_id": "agent_buyer",
                        "amount_atomic": "5000000",
                        "reason": "demo funding",
                    },
                ),
                (
                    "create_escrow",
                    {
                        "buyer_agent_id": "agent_buyer",
                        "seller_agent_id": "agent_seller",
                        "amount_atomic": "3000000",
                        "task_id": "task_123",
                        "description": "Research task",
                    },
                ),
                ("release_escrow", {"escrow_id": "escrow_1"}),
                ("refund_escrow", {"escrow_id": "escrow_2"}),
            ],
        )

    def test_build_tools_hides_trade_intent_bridge_without_discovery_support(self) -> None:
        with patch.object(
            main,
            "get_chain_mcp_client",
            return_value=_FakeToolClient(["chain_get_transaction_receipt"]),
        ), patch.object(
            main,
            "get_freqtrade_mcp_client",
            return_value=_FakeToolClient(["get_trading_status"]),
        ):
            main.clear_discovered_tool_cache()
            tools = main.build_tools()

        tool_names = {tool.name for tool in tools}
        self.assertNotIn("execute_freqtrade_trade_intent", tool_names)

    def test_build_tools_exposes_trade_intent_bridge_when_both_backends_advertise_it(
        self,
    ) -> None:
        with patch.object(
            main,
            "get_chain_mcp_client",
            return_value=_FakeToolClient(
                ["chain_execute_trade_intent", "chain_get_transaction_receipt"]
            ),
        ), patch.object(
            main,
            "get_freqtrade_mcp_client",
            return_value=_FakeToolClient(["get_trading_status", "emit_trade_intent"]),
        ):
            main.clear_discovered_tool_cache()
            tools = main.build_tools()

        tool_names = {tool.name for tool in tools}
        self.assertIn("execute_freqtrade_trade_intent", tool_names)

    def test_build_tools_hides_trade_intent_bridge_without_chain_execute_trade_intent(
        self,
    ) -> None:
        with patch.object(
            main,
            "get_chain_mcp_client",
            return_value=_FakeToolClient(["chain_get_transaction_receipt"]),
        ), patch.object(
            main,
            "get_freqtrade_mcp_client",
            return_value=_FakeToolClient(["get_trading_status", "emit_trade_intent"]),
        ):
            main.clear_discovered_tool_cache()
            tools = main.build_tools()

        tool_names = {tool.name for tool in tools}
        self.assertNotIn("execute_freqtrade_trade_intent", tool_names)

    def test_build_tools_hides_trade_intent_bridge_without_emit_trade_intent(
        self,
    ) -> None:
        with patch.object(
            main,
            "get_chain_mcp_client",
            return_value=_FakeToolClient(
                ["chain_execute_trade_intent", "chain_get_transaction_receipt"]
            ),
        ), patch.object(
            main,
            "get_freqtrade_mcp_client",
            return_value=_FakeToolClient(["get_trading_status"]),
        ):
            main.clear_discovered_tool_cache()
            tools = main.build_tools()

        tool_names = {tool.name for tool in tools}
        self.assertNotIn("execute_freqtrade_trade_intent", tool_names)

    def test_build_tools_exposes_discovered_freqtrade_signal_tool(self) -> None:
        with patch.object(main, "_load_discovered_chain_tools", return_value=[]), patch.object(
            main,
            "_load_discovered_freqtrade_tools",
            return_value=[_make_test_tool("evaluate_trade_signal")],
        ):
            tools = main.build_tools()

        tool_names = {tool.name for tool in tools}
        self.assertIn("evaluate_trade_signal", tool_names)

    def test_build_tools_keeps_independent_freqtrade_tools_when_chain_discovery_fails(
        self,
    ) -> None:
        with patch.object(
            main,
            "get_freqtrade_mcp_client",
            return_value=_FakeToolClient(["get_trading_status", "evaluate_trade_signal"]),
        ), patch.object(
            main,
            "get_chain_mcp_client",
            side_effect=RuntimeError("chain discovery failed"),
        ):
            main.clear_discovered_tool_cache()
            tools = main.build_tools()

        tool_names = {tool.name for tool in tools}
        self.assertIn("evaluate_trade_signal", tool_names)
        self.assertNotIn("execute_freqtrade_trade_intent", tool_names)

    def test_build_tools_uses_discovery_results_instead_of_static_registries(self) -> None:
        with patch.object(
            main,
            "_load_discovered_chain_tools",
            return_value=[_make_test_tool("chain_only_discovered")],
        ), patch.object(
            main,
            "_load_discovered_freqtrade_tools",
            return_value=[_make_test_tool("freqtrade_only_discovered")],
        ):
            tools = main.build_tools()

        tool_names = {tool.name for tool in tools}
        self.assertIn("chain_only_discovered", tool_names)
        self.assertIn("freqtrade_only_discovered", tool_names)
        self.assertNotIn("chain_submit_execution", tool_names)
        self.assertNotIn("force_enter_trade", tool_names)

    def test_build_tools_uses_cached_discovery_inside_running_loop(self) -> None:
        main.set_discovered_tool_cache(
            chain_tools=[_make_test_tool("chain_cached")],
            freqtrade_tools=[_make_test_tool("freqtrade_cached")],
        )

        async def build_inside_running_loop() -> set[str]:
            with patch.object(
                main,
                "discover_chain_tools",
                side_effect=AssertionError("should not sync discover chain tools"),
            ), patch.object(
                main,
                "discover_freqtrade_tools",
                side_effect=AssertionError("should not sync discover freqtrade tools"),
            ):
                return {tool.name for tool in main.build_tools()}

        tool_names = asyncio.run(build_inside_running_loop())

        self.assertIn("chain_cached", tool_names)
        self.assertIn("freqtrade_cached", tool_names)

    def test_refresh_cache_feeds_get_agent_graph_bridge_gating(self) -> None:
        class FakeChainClient:
            def __init__(self, tools: list[str]) -> None:
                self._tools = tools

            async def list_tools(self) -> list[str]:
                return self._tools

        class FakeFreqtradeClient:
            def __init__(self, tools: list[str]) -> None:
                self._tools = tools

            async def list_tools(self) -> list[str]:
                return self._tools

        def graph_tool_names(
            freqtrade_tools: list[str], chain_tools: list[str]
        ) -> set[str]:
            captured: dict[str, object] = {}

            with (
                patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False),
                patch.object(
                    main,
                    "get_chain_mcp_client",
                    return_value=FakeChainClient(chain_tools),
                ),
                patch.object(
                    main,
                    "get_freqtrade_mcp_client",
                    return_value=FakeFreqtradeClient(freqtrade_tools),
                ),
                patch.object(main, "discover_chain_tools", side_effect=AssertionError("unexpected sync chain discovery")),
                patch.object(main, "discover_freqtrade_tools", side_effect=AssertionError("unexpected sync freqtrade discovery")),
                patch.object(main, "ChatOpenAI", return_value=object()),
                patch.object(
                    main,
                    "create_react_agent",
                    side_effect=lambda *, model, tools, prompt, streamed_tool_execution=True: captured.update(
                        {"tool_names": {tool.name for tool in tools}}
                    )
                    or object(),
                ),
            ):
                main.clear_discovered_tool_cache()
                main.get_agent_graph.cache_clear()
                asyncio.run(main.refresh_discovered_tool_cache())
                main.get_agent_graph()

            return captured["tool_names"]  # type: ignore[return-value]

        tool_names_with_bridge = graph_tool_names(
            ["get_trading_status", "emit_trade_intent"],
            ["chain_get_transaction_receipt", "chain_execute_trade_intent"],
        )
        self.assertIn("execute_freqtrade_trade_intent", tool_names_with_bridge)
        self.assertIn("chain_get_transaction_receipt", tool_names_with_bridge)

        tool_names_without_bridge = graph_tool_names(
            ["get_trading_status"],
            ["chain_get_transaction_receipt", "chain_execute_trade_intent"],
        )
        self.assertNotIn("execute_freqtrade_trade_intent", tool_names_without_bridge)
        self.assertIn("chain_get_transaction_receipt", tool_names_without_bridge)

        tool_names_without_chain_support = graph_tool_names(
            ["get_trading_status", "emit_trade_intent"],
            ["chain_get_transaction_receipt"],
        )
        self.assertNotIn("execute_freqtrade_trade_intent", tool_names_without_chain_support)
        self.assertIn("chain_get_transaction_receipt", tool_names_without_chain_support)

    def test_streamed_tool_execution_is_disabled_for_packyapi(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OPENAI_BASE_URL": "https://www.packyapi.com/v1",
            },
            clear=True,
        ):
            self.assertFalse(main._provider_supports_streamed_tool_execution())

    def test_provider_supports_streamed_tool_execution_for_openai_base_url(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OPENAI_BASE_URL": "https://api.openai.com/v1",
            },
            clear=True,
        ):
            self.assertTrue(main._provider_supports_streamed_tool_execution())

    def test_provider_supports_streamed_tool_execution_uses_openai_endpoint_alias(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "OPENAI_ENDPOINT": "https://gateway.packyapi.com/custom/v1",
            },
            clear=True,
        ):
            self.assertFalse(main._provider_supports_streamed_tool_execution())

    def test_streamed_tool_execution_defaults_to_enabled_without_known_provider_gate(
        self,
    ) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertTrue(main._provider_supports_streamed_tool_execution())


if __name__ == "__main__":
    unittest.main()
