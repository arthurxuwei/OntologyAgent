import asyncio
import tempfile
import unittest
from pathlib import Path
from typing import Optional
from unittest.mock import patch

from autonomy import AutonomyConfig, AutonomyController, AutonomyDecision, load_autonomy_config


def make_chain_state(balance_eth: str, spent_usdc_atomic: str = "0") -> dict[str, object]:
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
                "spentTodayUsdcAtomic": spent_usdc_atomic,
                "dailyLimitUsdcAtomic": "2000000",
            },
            "x402": {
                "network": "eip155:84532",
                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                "buyerSignerConfigured": True,
            },
        }
    }


def make_freqtrade_budget(realized: float = 0, unrealized: float = 0, open_trades: int = 0) -> dict[str, object]:
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


class AutonomyControllerTests(unittest.TestCase):
    def test_load_autonomy_config_falls_back_when_model_env_is_blank(self) -> None:
        config = load_autonomy_config(
            {
                "AUTONOMY_MODEL": "",
                "BRAIN_AGENT_MODEL": "gpt-4.1-mini",
            }
        )

        self.assertEqual(config.model_name, "gpt-4.1-mini")

    def test_tick_bootstraps_budget_syncs_dry_run_wallet_and_starts_bot(self) -> None:
        calls: list[tuple[str, dict[str, object]]] = []

        async def chain_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            self.assertEqual(tool_name, "chain_get_wallet_state")
            return make_chain_state("2.0")

        async def freqtrade_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
            calls.append((tool_name, arguments or {}))
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget()
            return {"result": {"ok": True}}

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                AutonomyConfig(
                    enabled=True,
                    interval_seconds=60,
                    state_path=str(Path(temp_dir) / "autonomy.json"),
                    x402_url="http://x402-seller:8000/x402/demo-resource",
                    x402_method="GET",
                    eth_price_usd=3000,
                    trading_allocation_ratio=0.5,
                    min_cash_reserve_ratio=0.25,
                    min_net_budget_ratio=0.6,
                    max_drawdown_ratio=0.15,
                    min_x402_interval_seconds=1800,
                    model_name="gpt-4o-mini",
                ),
                chain_tool,
                freqtrade_tool,
            )

            async def fake_decision(_self: AutonomyController, _context: dict[str, object]) -> AutonomyDecision:
                return AutonomyDecision(
                    action="start_trading",
                    reason="Budget is healthy; start the dry-run bot.",
                    riskLevel="low",
                    maxSpendAllowed=0,
                )

            with patch.object(AutonomyController, "_make_decision", fake_decision):
                result = asyncio.run(controller.tick())

            self.assertEqual(result["decision"]["action"], "start_trading")
            status = asyncio.run(controller.status())
            ledger = status["ledger"]
            self.assertTrue(ledger["botEnabled"])
            self.assertTrue(ledger["dryRunWalletSynced"])
            self.assertEqual(ledger["startingCapitalUsd"], 6000.0)
            self.assertEqual(ledger["allocatedToDryRunTrading"], 3000.0)
            self.assertIn(("sync_dry_run_wallet", {"dry_run_wallet": 3000.0}), calls)
            self.assertIn(("start_bot", {}), calls)

    def test_tick_holds_when_requested_action_is_not_allowed(self) -> None:
        async def chain_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
            self.assertEqual(tool_name, "chain_get_wallet_state")
            return make_chain_state("1.0", spent_usdc_atomic="2900000000")

        async def freqtrade_tool(tool_name: str, arguments: Optional[dict[str, object]] = None) -> dict[str, object]:
            if tool_name == "get_budget_snapshot":
                return make_freqtrade_budget(realized=-100, unrealized=-50, open_trades=1)
            return {"result": {"ok": True}}

        with tempfile.TemporaryDirectory() as temp_dir:
            controller = AutonomyController(
                AutonomyConfig(
                    enabled=True,
                    interval_seconds=60,
                    state_path=str(Path(temp_dir) / "autonomy.json"),
                    x402_url="http://x402-seller:8000/x402/demo-resource",
                    x402_method="GET",
                    eth_price_usd=3000,
                    trading_allocation_ratio=0.5,
                    min_cash_reserve_ratio=0.25,
                    min_net_budget_ratio=0.6,
                    max_drawdown_ratio=0.15,
                    min_x402_interval_seconds=1800,
                    model_name="gpt-4o-mini",
                ),
                chain_tool,
                freqtrade_tool,
            )

            async def fake_decision(_self: AutonomyController, _context: dict[str, object]) -> AutonomyDecision:
                return AutonomyDecision(
                    action="spend_x402",
                    reason="Try spending despite budget stress.",
                    riskLevel="high",
                    maxSpendAllowed=10,
                )

            with patch.object(AutonomyController, "_make_decision", fake_decision):
                result = asyncio.run(controller.tick())

            self.assertEqual(result["decision"]["action"], "hold")
            self.assertEqual(result["actionResult"]["action"], "hold")


if __name__ == "__main__":
    unittest.main()
