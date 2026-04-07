import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import autonomy
from autonomy import (
    AutonomyConfig,
    AutonomyController,
    GuardDecision,
    GuardLedger,
    _extract_json_object,
    load_autonomy_config,
)
from autonomy_models import RuntimeExecutionRecord, RuntimeIntent


def make_chain_state(balance_eth: str) -> dict[str, object]:
    return {
        "result": {
            "wallet": {
                "address": "0x1111111111111111111111111111111111111111",
                "signerConfigured": True,
                "balanceWei": "0",
                "balanceEth": balance_eth,
                "mockChain": False,
            },
            "chain": {
                "blockNumber": 1,
                "rpcUrl": "https://base-sepolia-rpc.publicnode.com",
                "chainId": 84532,
                "expectedChainId": 84532,
                "mockChain": False,
            },
            "policy": {
                "dayKey": "2026-04-04",
                "spentTodayWei": "0",
                "dailyLimitWei": "2000000000000000000",
                "spentTodayUsdcAtomic": "0",
                "dailyLimitUsdcAtomic": "2000000",
            },
            "x402": {
                "network": "eip155:84532",
                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                "buyerSignerConfigured": True,
            },
        }
    }


def make_freqtrade_budget(
    realized: float = 0, unrealized: float = 0, open_trades: int = 0
) -> dict[str, object]:
    return {
        "result": {
            "dryRun": True,
            "dryRunWallet": 1000.0,
            "stakeCurrency": "USDT",
            "stakeAmount": 100.0,
            "maxOpenTrades": 3,
            "activeStrategy": "SimpleAgentStrategy",
            "initialState": "stopped",
            "openTradeCount": open_trades,
            "realizedPnl": realized,
            "unrealizedPnl": unrealized,
        }
    }


def make_config(state_path: str) -> AutonomyConfig:
    return AutonomyConfig(
        enabled=True,
        interval_seconds=60,
        state_path=state_path,
        eth_price_usd=3000,
        min_wallet_balance_usd=250,
        stop_trading_balance_usd=150,
        force_exit_balance_usd=75,
        max_drawdown_ratio=0.15,
        model_name="gpt-4o-mini",
    )


class AutonomyControllerTests(unittest.TestCase):
    def test_run_chain_execution_returns_failed_record_for_chain_error(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("1.0")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_freqtrade_budget()

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )
            intent = RuntimeIntent(
                intentId="intent-chain-submit_execution",
                intentType="chain",
                action="chain_submit_execution",
                parameters={"operation": "rebalance"},
            )

            async def raising_workflow(
                _tool: autonomy.ToolInvoker,
                _intent: RuntimeIntent,
            ) -> RuntimeExecutionRecord:
                raise RuntimeError("confirmation timeout")

            with patch.object(autonomy, "execute_chain_workflow", raising_workflow):
                execution = asyncio.run(controller._run_chain_execution(intent))

            self.assertEqual(
                execution.executionId, "exec-intent-chain-submit_execution"
            )
            self.assertEqual(execution.intentId, intent.intentId)
            self.assertEqual(execution.intentType, "chain")
            self.assertEqual(execution.stage, "failed")
            self.assertEqual(execution.status, "failed")
            self.assertEqual(execution.failureCode, "chain_confirmation_timeout")
            self.assertEqual(execution.failureMessage, "confirmation timeout")

    def test_run_trade_execution_returns_failed_record_for_trade_error(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("1.0")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_freqtrade_budget()

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )
            intent = RuntimeIntent(
                intentId="intent-trade-force_exit_all",
                intentType="trade",
                action="force_exit_all",
            )

            async def raising_workflow(
                _tool: autonomy.ToolInvoker,
                _intent: RuntimeIntent,
            ) -> RuntimeExecutionRecord:
                raise RuntimeError("order rejected")

            with patch.object(autonomy, "execute_trade_workflow", raising_workflow):
                execution = asyncio.run(controller._run_trade_execution(intent))

            self.assertEqual(execution.executionId, "exec-intent-trade-force_exit_all")
            self.assertEqual(execution.intentId, intent.intentId)
            self.assertEqual(execution.intentType, "trade")
            self.assertEqual(execution.stage, "failed")
            self.assertEqual(execution.status, "failed")
            self.assertEqual(execution.failureCode, "trade_order_rejected")
            self.assertEqual(execution.failureMessage, "order rejected")

    def test_runtime_ledger_defaults_include_execution_tracking(self) -> None:
        ledger = GuardLedger()

        self.assertEqual(ledger.activeIntents, [])
        self.assertEqual(ledger.activeExecutions, [])
        self.assertEqual(ledger.executionHistory, [])
        self.assertEqual(ledger.circuitBreaker.state, "closed")

    def test_runtime_ledger_round_trips_with_shared_stage_values(self) -> None:
        execution = RuntimeExecutionRecord(
            executionId="exec-1",
            intentId="intent-1",
            intentType="trade",
            stage="executing",
        )

        ledger = GuardLedger.model_validate(
            {
                "activeExecutions": [execution.model_dump()],
                "executionHistory": [execution.model_dump()],
            }
        )

        self.assertEqual(ledger.activeExecutions[0].stage, "executing")
        self.assertEqual(ledger.executionHistory[0].stage, "executing")

    def test_extract_json_object_supports_code_fences(self) -> None:
        payload = _extract_json_object(
            """```json
            {"action":"hold","reason":"ok","riskLevel":"low","recommendedFundingUsd":0}
            ```"""
        )

        self.assertEqual(payload["action"], "hold")

    def test_load_autonomy_config_falls_back_when_model_env_is_blank(self) -> None:
        config = load_autonomy_config(
            {
                "AUTONOMY_MODEL": "",
                "BRAIN_AGENT_MODEL": "gpt-4.1-mini",
            }
        )

        self.assertEqual(config.model_name, "gpt-4.1-mini")

    def test_controller_can_be_started_manually_even_when_autostart_is_disabled(
        self,
    ) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("1.0")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_freqtrade_budget()

        with tempfile.TemporaryDirectory() as temp_dir:
            config = make_config(str(Path(temp_dir) / "autonomy.json"))
            config = AutonomyConfig(**{**config.__dict__, "enabled": False})
            controller = AutonomyController(config, chain_tool, freqtrade_tool)

            initial_status = asyncio.run(controller.status())
            self.assertFalse(initial_status["enabled"])
            self.assertFalse(initial_status["autostartConfigured"])

            asyncio.run(controller.start(force=True))
            running_status = asyncio.run(controller.status())
            self.assertTrue(running_status["enabled"])

            asyncio.run(controller.stop(disable=True))

    def test_tick_bootstraps_guard_state_without_syncing_dry_run_wallet(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            self.assertEqual(tool_name, "chain_get_wallet_state")
            return make_chain_state("2.0")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            self.assertEqual(tool_name, "get_budget_snapshot")
            return make_freqtrade_budget(realized=20, unrealized=30, open_trades=1)

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            async def fake_decision(
                _self: AutonomyController, _context: dict[str, object]
            ) -> GuardDecision:
                return GuardDecision(
                    action="hold",
                    reason="Wallet is healthy; keep monitoring.",
                    riskLevel="low",
                    recommendedFundingUsd=0,
                )

            with patch.object(AutonomyController, "_make_decision", fake_decision):
                result = asyncio.run(controller.tick())

            self.assertEqual(result["decision"]["action"], "hold")
            status = asyncio.run(controller.status())
            ledger = status["ledger"]
            self.assertEqual(ledger["startingCapitalUsd"], 6000.0)
            self.assertEqual(ledger["currentWalletBalanceUsd"], 6000.0)
            self.assertEqual(ledger["netWorthEstimate"], 6050.0)
            self.assertEqual(ledger["healthStatus"], "healthy")
            self.assertIsNone(ledger["lastFundingRecommendation"])
            self.assertNotIn(("sync_dry_run_wallet", {"dry_run_wallet": 3000.0}), calls)

    def test_tick_force_exits_when_guard_reaches_critical_state(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            return make_chain_state("0.01")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget(
                    realized=-40, unrealized=-20, open_trades=2
                )
            if tool_name == "force_exit_trade":
                return {"result": {"ok": True}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            async def fake_decision(
                _self: AutonomyController, _context: dict[str, object]
            ) -> GuardDecision:
                return GuardDecision(
                    action="force_exit_all",
                    reason="Wallet balance is critically low.",
                    riskLevel="high",
                    recommendedFundingUsd=220,
                )

            with patch.object(AutonomyController, "_make_decision", fake_decision):
                result = asyncio.run(controller.tick())

            self.assertEqual(result["decision"]["action"], "force_exit_all")
            self.assertIn(
                ("force_exit_trade", {"trade_id": "all", "order_type": "market"}), calls
            )
            status = asyncio.run(controller.status())
            ledger = status["ledger"]
            self.assertEqual(ledger["healthStatus"], "critical")
            self.assertEqual(ledger["lastProtectiveAction"]["action"], "force_exit_all")
            self.assertFalse(ledger["botEnabled"])

    def test_tick_builds_normalized_runtime_observation(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("2.0")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_freqtrade_budget(realized=20, unrealized=30, open_trades=1)

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            chain_state = make_chain_state("2.0")["result"]
            freqtrade_budget = make_freqtrade_budget(
                realized=20, unrealized=30, open_trades=1
            )["result"]
            controller._bootstrap_if_needed(chain_state)

            observation = controller._build_runtime_observation(
                chain_state,
                freqtrade_budget,
            )

            self.assertEqual(observation["trading"]["openTradeCount"], 1)
            self.assertEqual(observation["chain"]["wallet"]["balanceEth"], "2.0")
            self.assertEqual(observation["budget"]["startingCapitalUsd"], 6000.0)

    def test_plan_trade_intent_uses_open_trade_risk_signal(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("0.01")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_freqtrade_budget(realized=-40, unrealized=-20, open_trades=2)

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            chain_state = make_chain_state("0.01")["result"]
            freqtrade_budget = make_freqtrade_budget(
                realized=-40, unrealized=-20, open_trades=2
            )["result"]
            controller._bootstrap_if_needed(chain_state)

            observation = controller._build_runtime_observation(
                chain_state,
                freqtrade_budget,
            )

            intent = controller._plan_intent(observation)

            self.assertIsInstance(intent, RuntimeIntent)
            self.assertEqual(intent.intentType, "trade")
            self.assertEqual(intent.action, "force_exit_all")
            self.assertEqual(intent.stage, "planned")

    def test_tick_persists_runtime_observation_and_planned_intent(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("0.01")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget(
                    realized=-40, unrealized=-20, open_trades=2
                )
            if tool_name == "force_exit_trade":
                return {"result": {"ok": True}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            async def fake_decision(
                _self: AutonomyController, _context: dict[str, object]
            ) -> GuardDecision:
                return GuardDecision(
                    action="force_exit_all",
                    reason="Wallet balance is critically low.",
                    riskLevel="high",
                    recommendedFundingUsd=220,
                )

            with patch.object(AutonomyController, "_make_decision", fake_decision):
                asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(
                ledger["latestObservation"]["trading"]["openTradeCount"], 2
            )
            self.assertEqual(
                ledger["latestObservation"]["chain"]["wallet"]["balanceEth"], "0.01"
            )
            self.assertEqual(ledger["activeIntents"][0]["intentType"], "trade")
            self.assertEqual(ledger["activeIntents"][0]["action"], "force_exit_all")
            self.assertEqual(ledger["activeIntents"][0]["stage"], "planned")

    def test_tick_uses_planned_force_exit_intent_over_llm_hold(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            return make_chain_state("0.01")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget(
                    realized=-40, unrealized=-20, open_trades=2
                )
            if tool_name == "force_exit_trade":
                return {"result": {"ok": True}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            async def fake_decision(
                _self: AutonomyController, _context: dict[str, object]
            ) -> GuardDecision:
                return GuardDecision(
                    action="hold",
                    reason="LLM would otherwise hold.",
                    riskLevel="low",
                    recommendedFundingUsd=0,
                )

            with patch.object(AutonomyController, "_make_decision", fake_decision):
                result = asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(result["decision"]["action"], "force_exit_all")
            self.assertEqual(ledger["activeIntents"][0]["action"], "force_exit_all")
            self.assertIn(
                ("force_exit_trade", {"trade_id": "all", "order_type": "market"}),
                calls,
            )

    def test_tick_uses_planned_stop_trading_intent_over_llm_hold(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            return make_chain_state("0.04")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget(realized=0, unrealized=0, open_trades=1)
            if tool_name == "stop_bot":
                return {"result": {"ok": True}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            async def fake_decision(
                _self: AutonomyController, _context: dict[str, object]
            ) -> GuardDecision:
                return GuardDecision(
                    action="hold",
                    reason="LLM would otherwise hold.",
                    riskLevel="low",
                    recommendedFundingUsd=0,
                )

            with patch.object(AutonomyController, "_make_decision", fake_decision):
                result = asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(result["decision"]["action"], "stop_trading")
            self.assertEqual(ledger["activeIntents"][0]["action"], "stop_trading")
            self.assertIn(("stop_bot", {}), calls)

    def test_tick_uses_planned_stop_trading_when_bot_enabled_without_open_trades(
        self,
    ) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            return make_chain_state("0.04")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget(realized=0, unrealized=0, open_trades=0)
            if tool_name == "stop_bot":
                return {"result": {"ok": True}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )
            controller._bootstrap_if_needed(make_chain_state("0.04")["result"])
            controller._state.botEnabled = True

            async def fake_decision(
                _self: AutonomyController, _context: dict[str, object]
            ) -> GuardDecision:
                return GuardDecision(
                    action="hold",
                    reason="LLM would otherwise hold.",
                    riskLevel="low",
                    recommendedFundingUsd=0,
                )

            with patch.object(AutonomyController, "_make_decision", fake_decision):
                result = asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(result["decision"]["action"], "stop_trading")
            self.assertEqual(ledger["activeIntents"][0]["action"], "stop_trading")
            self.assertIn(("stop_bot", {}), calls)
            self.assertNotIn(
                ("force_exit_trade", {"trade_id": "all", "order_type": "market"}),
                calls,
            )

    def test_tick_prefers_stop_trading_over_force_exit_without_open_trades(
        self,
    ) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            return make_chain_state("0.01")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget(realized=0, unrealized=0, open_trades=0)
            if tool_name == "stop_bot":
                return {"result": {"ok": True}}
            if tool_name == "force_exit_trade":
                return {"result": {"ok": True}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )
            controller._bootstrap_if_needed(make_chain_state("0.01")["result"])
            controller._state.botEnabled = True

            async def fake_decision(
                _self: AutonomyController, _context: dict[str, object]
            ) -> GuardDecision:
                return GuardDecision(
                    action="hold",
                    reason="LLM would otherwise hold.",
                    riskLevel="low",
                    recommendedFundingUsd=0,
                )

            with patch.object(AutonomyController, "_make_decision", fake_decision):
                result = asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(result["decision"]["action"], "stop_trading")
            self.assertEqual(ledger["activeIntents"][0]["action"], "stop_trading")
            self.assertIn(("stop_bot", {}), calls)
            self.assertNotIn(
                ("force_exit_trade", {"trade_id": "all", "order_type": "market"}),
                calls,
            )

    def test_tick_stop_trading_persists_disabled_bot_state(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            return make_chain_state("0.04")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget(realized=0, unrealized=0, open_trades=1)
            if tool_name == "stop_bot":
                return {"result": {"ok": True}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            async def fake_decision(
                _self: AutonomyController, _context: dict[str, object]
            ) -> GuardDecision:
                return GuardDecision(
                    action="hold",
                    reason="LLM would otherwise hold.",
                    riskLevel="low",
                    recommendedFundingUsd=0,
                )

            with patch.object(AutonomyController, "_make_decision", fake_decision):
                asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(ledger["activeIntents"][0]["action"], "stop_trading")
            self.assertFalse(ledger["botEnabled"])
            self.assertIn(("stop_bot", {}), calls)

    def test_tick_recommends_funding_when_balance_is_low(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("0.05")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget(realized=0, unrealized=0, open_trades=0)
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            async def fake_decision(
                _self: AutonomyController, _context: dict[str, object]
            ) -> GuardDecision:
                return GuardDecision(
                    action="hold",
                    reason="LLM would otherwise hold.",
                    riskLevel="low",
                    recommendedFundingUsd=0,
                )

            with patch.object(AutonomyController, "_make_decision", fake_decision):
                result = asyncio.run(controller.tick())

            self.assertEqual(result["decision"]["action"], "request_funding")
            status = asyncio.run(controller.status())
            ledger = status["ledger"]
            self.assertEqual(ledger["healthStatus"], "watch")
            self.assertEqual(ledger["activeIntents"][0]["action"], "request_funding")
            self.assertEqual(
                ledger["activeExecutions"][0]["intentId"],
                "intent-chain-request_funding",
            )
            self.assertEqual(ledger["activeExecutions"][0]["status"], "active")
            self.assertEqual(
                ledger["lastFundingRecommendation"]["recommendedFundingUsd"], 100
            )

    def test_tick_routes_request_funding_through_chain_execution_helper(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("0.05")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget(realized=0, unrealized=0, open_trades=0)
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            async def fake_run_chain_execution(
                _self: AutonomyController,
                intent: RuntimeIntent,
            ) -> RuntimeExecutionRecord:
                return RuntimeExecutionRecord(
                    executionId="exec-request-funding-test",
                    intentId=intent.intentId,
                    intentType=intent.intentType,
                    stage="executing",
                    status="active",
                )

            with (
                patch.object(
                    AutonomyController,
                    "_run_chain_execution",
                    fake_run_chain_execution,
                ),
                patch.object(
                    AutonomyController,
                    "_execute_decision",
                    side_effect=AssertionError(
                        "tick should route request_funding through _run_chain_execution"
                    ),
                ),
            ):
                result = asyncio.run(controller.tick())

            self.assertEqual(result["decision"]["action"], "request_funding")
            self.assertEqual(
                result["execution"]["executionId"], "exec-request-funding-test"
            )

    def test_tick_routes_real_chain_intent_through_chain_workflow(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "chain_get_wallet_state":
                return make_chain_state("1.0")
            if tool_name == "chain_submit_execution":
                return {"result": {"settlement": {"txHash": "0xchain123"}}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget(realized=0, unrealized=0, open_trades=0)
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            intent = RuntimeIntent(
                intentId="intent-chain-submit_execution",
                intentType="chain",
                action="chain_submit_execution",
                parameters={"operation": "rebalance"},
                reason="Execute a reconciled chain action.",
            )

            with (
                patch.object(
                    AutonomyController,
                    "_plan_intent",
                    return_value=intent,
                ),
                patch.object(
                    AutonomyController,
                    "_make_decision",
                    side_effect=AssertionError(
                        "tick should use the planned chain intent"
                    ),
                ),
            ):
                result = asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(result["decision"]["action"], "chain_submit_execution")
            self.assertEqual(result["execution"]["stage"], "reconciled")
            self.assertEqual(result["execution"]["status"], "completed")
            self.assertEqual(result["execution"]["externalId"], "0xchain123")
            self.assertEqual(ledger["activeExecutions"], [])
            self.assertEqual(ledger["executionHistory"][0]["stage"], "reconciled")
            self.assertEqual(ledger["executionHistory"][0]["externalId"], "0xchain123")
            self.assertIn(("chain_submit_execution", {"operation": "rebalance"}), calls)

    def test_decision_from_intent_supports_whitelisted_chain_action(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("1.0")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_freqtrade_budget()

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )
            intent = RuntimeIntent(
                intentId="intent-chain-sign_transfer",
                intentType="chain",
                action="chain_sign_transfer",
                parameters={"to": "0x1111111111111111111111111111111111111111"},
                reason="Sign a transfer.",
            )

            decision = controller._decision_from_intent(intent)

            self.assertEqual(decision.action, "chain_sign_transfer")
            self.assertEqual(decision.reason, "Sign a transfer.")

    def test_policy_allows_local_request_funding_on_mainnet(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("1.0")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_freqtrade_budget()

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )
            observation = {
                "chain": {
                    "chain": {"chainId": 1, "mockChain": False},
                },
                "trading": {"dryRun": True},
            }
            intent = RuntimeIntent(
                intentId="intent-1",
                intentType="chain",
                action="request_funding",
            )

            decision = controller._apply_policy(intent, observation)

            self.assertEqual(decision["decision"], "allow")

    def test_policy_denies_chain_action_outside_mock_or_testnet(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("1.0")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_freqtrade_budget()

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )
            observation = {
                "chain": {
                    "chain": {"chainId": 1, "mockChain": False},
                },
                "trading": {"dryRun": True},
            }
            intent = RuntimeIntent(
                intentId="intent-1",
                intentType="chain",
                action="chain_submit_execution",
                parameters={"operation": "rebalance"},
            )

            decision = controller._apply_policy(intent, observation)

            self.assertEqual(decision["decision"], "deny")

    def test_tick_allows_request_funding_recommendation_on_mainnet(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            state = make_chain_state("0.05")
            state["result"]["chain"]["chainId"] = 1
            return state

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_freqtrade_budget()

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            async def fake_run_chain_execution(
                _self: AutonomyController,
                intent: RuntimeIntent,
            ) -> RuntimeExecutionRecord:
                return RuntimeExecutionRecord(
                    executionId="exec-request-funding-mainnet",
                    intentId=intent.intentId,
                    intentType=intent.intentType,
                    stage="executing",
                    status="active",
                )

            with (
                patch.object(
                    AutonomyController,
                    "_run_chain_execution",
                    fake_run_chain_execution,
                ),
                patch.object(
                    AutonomyController,
                    "_execute_decision",
                    side_effect=AssertionError(
                        "tick should keep request_funding on the local closed loop"
                    ),
                ),
            ):
                result = asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(result["policy"]["decision"], "allow")
            self.assertEqual(result["decision"]["action"], "request_funding")
            self.assertEqual(
                result["execution"]["executionId"], "exec-request-funding-mainnet"
            )
            self.assertEqual(ledger["activeIntents"][0]["action"], "request_funding")
            self.assertEqual(ledger["currentWalletBalanceUsd"], 150.0)
            self.assertEqual(ledger["healthStatus"], "watch")
            self.assertEqual(ledger["lastDecision"]["action"], "request_funding")

    def test_tick_denies_real_chain_intent_on_mainnet_before_execution(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            state = make_chain_state("1.0")
            state["result"]["chain"]["chainId"] = 1
            return state

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_freqtrade_budget()

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )
            intent = RuntimeIntent(
                intentId="intent-chain-submit_execution",
                intentType="chain",
                action="chain_submit_execution",
                parameters={"operation": "rebalance"},
                reason="Execute a reconciled chain action.",
            )

            with (
                patch.object(AutonomyController, "_plan_intent", return_value=intent),
                patch.object(
                    AutonomyController,
                    "_make_decision",
                    side_effect=AssertionError(
                        "tick should use the planned chain intent"
                    ),
                ),
                patch.object(
                    AutonomyController,
                    "_run_chain_execution",
                    side_effect=AssertionError(
                        "tick should deny the chain intent before execution"
                    ),
                ),
                patch.object(
                    AutonomyController,
                    "_execute_decision",
                    side_effect=AssertionError(
                        "tick should not execute a denied chain intent"
                    ),
                ),
            ):
                result = asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(result["policy"]["decision"], "deny")
            self.assertEqual(result["intent"]["action"], "chain_submit_execution")
            self.assertEqual(
                set(result["decision"]),
                {"action", "reason", "riskLevel", "recommendedFundingUsd"},
            )
            self.assertEqual(result["decision"]["action"], result["intent"]["action"])
            self.assertEqual(result["decision"]["reason"], result["intent"]["reason"])
            self.assertEqual(result["decision"]["riskLevel"], "medium")
            self.assertEqual(result["decision"]["recommendedFundingUsd"], 0.0)
            self.assertEqual(
                ledger["activeIntents"][0]["action"], "chain_submit_execution"
            )
            self.assertEqual(ledger["lastDecision"]["action"], "hold")

    def test_tick_reuses_existing_active_execution_for_same_intent(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("0.05")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_freqtrade_budget(open_trades=0)

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )
            first_result = asyncio.run(controller.tick())
            first_ledger = asyncio.run(controller.status())["ledger"]

            with patch.object(
                AutonomyController,
                "_execute_decision",
                side_effect=AssertionError(
                    "tick should reuse active execution instead of executing again"
                ),
            ):
                result = asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(first_result["decision"]["action"], "request_funding")
            self.assertEqual(
                first_ledger["activeExecutions"][0]["intentId"],
                "intent-chain-request_funding",
            )
            self.assertEqual(
                result["execution"]["intentId"],
                first_ledger["activeExecutions"][0]["intentId"],
            )
            self.assertEqual(result["actionResult"]["action"], "reuse_execution")
            self.assertEqual(
                result["intent"]["intentId"], "intent-chain-request_funding"
            )
            self.assertEqual(ledger["currentWalletBalanceUsd"], 150.0)
            self.assertEqual(ledger["healthStatus"], "watch")
            self.assertEqual(ledger["lastDecision"]["action"], "request_funding")

    def test_tick_records_completed_trade_execution_in_history(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            return make_chain_state("0.04")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget(open_trades=1)
            if tool_name == "stop_bot":
                return {"result": {"ok": True}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            result = asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(result["decision"]["action"], "stop_trading")
            self.assertEqual(ledger["activeExecutions"], [])
            self.assertEqual(
                ledger["executionHistory"][0]["intentId"], "intent-trade-stop_trading"
            )
            self.assertEqual(ledger["executionHistory"][0]["status"], "completed")
            self.assertIn(("stop_bot", {}), calls)

    def test_tick_force_exit_uses_trade_workflow_record(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        budget_calls = 0

        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            return make_chain_state("0.01")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            nonlocal budget_calls
            calls.append((tool_name, arguments or {}))
            if tool_name == "get_budget_snapshot":
                budget_calls += 1
                if budget_calls == 1:
                    return make_freqtrade_budget(
                        realized=-40, unrealized=-20, open_trades=2
                    )
                return make_freqtrade_budget(
                    realized=-40, unrealized=-20, open_trades=0
                )
            if tool_name == "force_exit_trade":
                return {"result": {"trade_id": "all", "ok": True}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            result = asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(result["decision"]["action"], "force_exit_all")
            self.assertEqual(result["execution"]["stage"], "reconciled")
            self.assertEqual(result["execution"]["status"], "completed")
            self.assertEqual(result["execution"]["externalId"], "all")
            self.assertEqual(ledger["executionHistory"][0]["stage"], "reconciled")
            self.assertEqual(ledger["executionHistory"][0]["externalId"], "all")
            self.assertEqual(
                calls,
                [
                    ("chain_get_wallet_state", {}),
                    ("get_budget_snapshot", {}),
                    ("force_exit_trade", {"trade_id": "all", "order_type": "market"}),
                    ("get_budget_snapshot", {}),
                ],
            )

    def test_tick_persists_active_execution_history_and_latest_observation(
        self,
    ) -> None:
        calls: list[tuple[str, dict[str, object]]] = []
        budget_calls = 0

        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            return make_chain_state("0.01")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            nonlocal budget_calls
            calls.append((tool_name, arguments or {}))
            if tool_name == "get_budget_snapshot":
                budget_calls += 1
                if budget_calls == 1:
                    return make_freqtrade_budget(
                        realized=-40, unrealized=-20, open_trades=2
                    )
                return make_freqtrade_budget(
                    realized=-40, unrealized=-20, open_trades=0
                )
            if tool_name == "force_exit_trade":
                return {"result": {"trade_id": "all", "ok": True}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            result = asyncio.run(controller.tick())
            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(result["execution"]["stage"], "reconciled")
            self.assertEqual(
                ledger["latestObservation"]["chain"]["wallet"]["balanceEth"], "0.01"
            )
            self.assertEqual(
                ledger["latestObservation"]["trading"]["openTradeCount"], 2
            )
            self.assertEqual(
                ledger["executionHistory"][0]["intentId"],
                "intent-trade-force_exit_all",
            )
            self.assertEqual(ledger["executionHistory"][0]["stage"], "reconciled")
            self.assertEqual(
                calls,
                [
                    ("chain_get_wallet_state", {}),
                    ("get_budget_snapshot", {}),
                    ("force_exit_trade", {"trade_id": "all", "order_type": "market"}),
                    ("get_budget_snapshot", {}),
                ],
            )

    def test_tick_opens_circuit_breaker_after_repeated_failures(self) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("0.01")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget(
                    realized=-40, unrealized=-20, open_trades=2
                )
            if tool_name == "force_exit_trade":
                return {"result": {"trade_id": "all", "ok": True}}
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            async def raising_workflow(
                _tool: autonomy.ToolInvoker,
                _intent: RuntimeIntent,
            ) -> RuntimeExecutionRecord:
                raise RuntimeError("order rejected")

            with patch.object(autonomy, "execute_trade_workflow", raising_workflow):
                first = asyncio.run(controller.tick())
                second = asyncio.run(controller.tick())
                third = asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(first["execution"]["status"], "failed")
            self.assertEqual(second["execution"]["status"], "failed")
            self.assertEqual(third["execution"]["status"], "failed")
            self.assertEqual(
                ledger["failureCounts"]["intent-trade-force_exit_all"],
                3,
            )
            self.assertEqual(ledger["circuitBreaker"]["state"], "open")

    def test_tick_blocks_execution_when_circuit_breaker_is_open(self) -> None:
        budget_calls = iter(
            [
                make_freqtrade_budget(realized=-40, unrealized=-20, open_trades=2),
                make_freqtrade_budget(realized=-40, unrealized=-20, open_trades=2),
                make_freqtrade_budget(realized=-40, unrealized=-20, open_trades=2),
                make_freqtrade_budget(realized=-55, unrealized=-25, open_trades=1),
            ]
        )
        workflow_calls = 0

        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("0.01")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            if tool_name == "get_budget_snapshot":
                return next(budget_calls)
            raise AssertionError(f"unexpected tool call: {tool_name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            async def raising_workflow(
                _tool: autonomy.ToolInvoker,
                _intent: RuntimeIntent,
            ) -> RuntimeExecutionRecord:
                nonlocal workflow_calls
                workflow_calls += 1
                raise RuntimeError("order rejected")

            with patch.object(autonomy, "execute_trade_workflow", raising_workflow):
                asyncio.run(controller.tick())
                asyncio.run(controller.tick())
                asyncio.run(controller.tick())
                blocked = asyncio.run(controller.tick())

            ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(workflow_calls, 3)
            self.assertEqual(blocked["policy"]["decision"], "trip_circuit")
            self.assertEqual(
                set(blocked["decision"]),
                {"action", "reason", "riskLevel", "recommendedFundingUsd"},
            )
            self.assertEqual(blocked["decision"]["action"], blocked["intent"]["action"])
            self.assertEqual(blocked["decision"]["reason"], blocked["intent"]["reason"])
            self.assertEqual(blocked["decision"]["riskLevel"], "high")
            self.assertEqual(blocked["decision"]["recommendedFundingUsd"], 0.0)
            self.assertEqual(blocked["actionResult"]["action"], "policy_denied")
            self.assertNotIn("execution", blocked)
            self.assertEqual(
                ledger["failureCounts"]["intent-trade-force_exit_all"],
                3,
            )
            self.assertEqual(ledger["tickCount"], 4)
            self.assertEqual(
                ledger["latestObservation"]["trading"]["openTradeCount"], 1
            )
            self.assertEqual(ledger["lastDecision"]["policyDecision"], "trip_circuit")
            self.assertEqual(ledger["circuitBreaker"]["state"], "open")

    def test_tick_closes_funding_execution_when_balance_recovers(self) -> None:
        chain_balances = iter(["0.05", "1.0", "0.05"])

        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state(next(chain_balances))

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_freqtrade_budget(open_trades=0)

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )

            async def fake_decision(
                _self: AutonomyController, _context: dict[str, object]
            ) -> GuardDecision:
                return GuardDecision(
                    action="hold",
                    reason="Recovered wallet does not need funding.",
                    riskLevel="low",
                    recommendedFundingUsd=0,
                )

            with patch.object(AutonomyController, "_make_decision", fake_decision):
                first_result = asyncio.run(controller.tick())
                first_ledger = asyncio.run(controller.status())["ledger"]

                second_result = asyncio.run(controller.tick())
                second_ledger = asyncio.run(controller.status())["ledger"]

                third_result = asyncio.run(controller.tick())
                third_ledger = asyncio.run(controller.status())["ledger"]

            self.assertEqual(first_result["decision"]["action"], "request_funding")
            self.assertEqual(first_ledger["activeExecutions"][0]["status"], "active")
            self.assertEqual(second_result["decision"]["action"], "hold")
            self.assertEqual(second_ledger["activeExecutions"], [])
            self.assertEqual(
                second_ledger["executionHistory"][0]["intentId"],
                "intent-chain-request_funding",
            )
            self.assertEqual(second_ledger["executionHistory"][0]["stage"], "closed")
            self.assertEqual(third_result["decision"]["action"], "request_funding")
            self.assertEqual(
                third_ledger["activeExecutions"][0]["intentId"],
                "intent-chain-request_funding",
            )
            self.assertNotEqual(
                third_ledger["activeExecutions"][0]["executionId"],
                first_ledger["activeExecutions"][0]["executionId"],
            )

    def test_find_active_execution_returns_existing_active_execution_by_intent_id(
        self,
    ) -> None:
        async def chain_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_chain_state("1.0")

        async def freqtrade_tool(
            tool_name: str, arguments: Optional[dict[str, object]] = None
        ) -> dict[str, object]:
            return make_freqtrade_budget()

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                make_config(str(Path(temp_dir) / "autonomy.json")),
                chain_tool,
                freqtrade_tool,
            )
            existing_execution = RuntimeExecutionRecord(
                executionId="exec-1",
                intentId="intent-1",
                intentType="trade",
                stage="executing",
            )
            controller._state.activeExecutions = [existing_execution]

            execution = controller._find_active_execution("intent-1")

            self.assertIs(execution, existing_execution)


if __name__ == "__main__":
    unittest.main()
