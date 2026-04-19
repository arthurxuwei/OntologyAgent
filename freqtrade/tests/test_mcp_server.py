import asyncio
import json
import sys
import tempfile
import textwrap
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from freqtrade import mcp_server


@contextmanager
def stub_freqtrade_strategy_module():
    original_module = sys.modules.get("freqtrade.strategy")
    strategy_module = types.ModuleType("freqtrade.strategy")
    strategy_module.IStrategy = type("IStrategy", (), {})
    sys.modules["freqtrade.strategy"] = strategy_module
    try:
        yield
    finally:
        if original_module is None:
            sys.modules.pop("freqtrade.strategy", None)
        else:
            sys.modules["freqtrade.strategy"] = original_module


class FreqtradeConfigAlignmentTests(unittest.TestCase):
    def test_default_config_aligns_with_eth_usdc(self) -> None:
        config_path = Path(__file__).resolve().parents[1] / "config" / "config.json"
        with config_path.open(encoding="utf-8") as config_file:
            config = json.load(config_file)

        self.assertEqual(config["stake_currency"], "USDC")
        self.assertEqual(config["exchange"]["pair_whitelist"], ["ETH/USDC"])


class EmitTradeIntentTests(unittest.TestCase):
    def test_emit_trade_intent_returns_normalized_v1_payload(self) -> None:
        result = asyncio.run(
            mcp_server.emit_trade_intent(
                pair="BTC/USDT",
                stake_amount=125.5,
                order_type="limit",
                price=64000.0,
                strategy="MomentumStrategy",
                max_slippage_bps=75,
                reason="portfolio_rebalance",
            )
        )

        self.assertEqual(result["summary"], "Trade intent prepared for BTC/USDT")
        self.assertTrue(result["intent"]["intentId"].startswith("intent-btc-usdt-125500-"))
        self.assertEqual(
            {key: value for key, value in result["intent"].items() if key != "intentId"},
            {
                "strategy": "MomentumStrategy",
                "pair": "BTC/USDT",
                "side": "long",
                "amount": 125.5,
                "amountType": "quote",
                "orderType": "limit",
                "limitPrice": 64000.0,
                "maxSlippageBps": 75,
                "reason": "portfolio_rebalance",
            },
        )

    def test_emit_trade_intent_generates_unique_ids_for_repeated_intents(self) -> None:
        first = asyncio.run(mcp_server.emit_trade_intent(pair="BTC/USDT", stake_amount=125.5))
        second = asyncio.run(mcp_server.emit_trade_intent(pair="BTC/USDT", stake_amount=125.5))

        self.assertNotEqual(first["intent"]["intentId"], second["intent"]["intentId"])

    def test_emit_trade_intent_rejects_short_side_in_v1(self) -> None:
        with self.assertRaisesRegex(
            mcp_server.FreqtradeRestError,
            "emit_trade_intent only supports long side in V1",
        ):
            asyncio.run(
                mcp_server.emit_trade_intent(
                    pair="BTC/USDT",
                    side="short",
                    stake_amount=10,
                )
            )

    def test_emit_trade_intent_rejects_non_positive_stake_amount(self) -> None:
        with self.assertRaisesRegex(
            mcp_server.FreqtradeRestError,
            "stake_amount must be greater than 0",
        ):
            asyncio.run(mcp_server.emit_trade_intent(pair="BTC/USDT", stake_amount=0))

    def test_emit_trade_intent_rejects_limit_order_without_price(self) -> None:
        with self.assertRaisesRegex(
            mcp_server.FreqtradeRestError,
            "limit orders require a price",
        ):
            asyncio.run(
                mcp_server.emit_trade_intent(
                    pair="BTC/USDT",
                    stake_amount=10,
                    order_type="limit",
                )
            )

    def test_emit_trade_intent_rejects_market_order_with_price(self) -> None:
        with self.assertRaisesRegex(
            mcp_server.FreqtradeRestError,
            "market orders do not accept a price",
        ):
            asyncio.run(
                mcp_server.emit_trade_intent(
                    pair="BTC/USDT",
                    stake_amount=10,
                    order_type="market",
                    price=64000.0,
                )
            )


class EvaluateTradeSignalTests(unittest.TestCase):
    @staticmethod
    def _pair_candles_payload(closes: list[float]) -> dict[str, object]:
        data = []
        for index, close in enumerate(closes):
            data.append(
                [
                    f"2026-04-19T00:{index:02d}:00Z",
                    close,
                    close,
                    close,
                    close,
                    1.0,
                ]
            )
        return {
            "columns": ["date", "open", "high", "low", "close", "volume"],
            "data": data,
            "length": len(data),
        }

    def test_evaluate_trade_signal_state_uses_real_default_simple_agent_strategy(self) -> None:
        strategy_path = Path(__file__).resolve().parents[1] / "strategies"
        candle_payload = self._pair_candles_payload([10] * 25 + [9, 8, 7, 6, 5, 20])

        with (
            stub_freqtrade_strategy_module(),
            patch.object(mcp_server, "FREQTRADE_STRATEGY_PATH", strategy_path),
            patch.object(
                mcp_server.rest_client,
                "get",
                new=AsyncMock(side_effect=[[], candle_payload]),
            ),
        ):
            state = asyncio.run(
                mcp_server._evaluate_trade_signal_state(
                    pair="ETH/USDC",
                    strategy="SimpleAgentStrategy",
                )
            )

        self.assertEqual(state["pair"], "ETH/USDC")
        self.assertEqual(state["strategy"], "SimpleAgentStrategy")
        self.assertEqual(state["timeframe"], "5m")
        self.assertFalse(state["hasOpenPosition"])
        self.assertTrue(state["entryTriggered"])
        self.assertFalse(state["exitTriggered"])

    def test_evaluate_trade_signal_state_respects_strategy_startup_candle_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            strategy_path = Path(temp_dir) / "LongHistoryStrategy.py"
            strategy_path.write_text(
                textwrap.dedent(
                    """
                    class LongHistoryStrategy:
                        timeframe = "5m"
                        startup_candle_count = 40

                        def populate_indicators(self, dataframe, metadata):
                            return dataframe

                        def populate_entry_trend(self, dataframe, metadata):
                            dataframe["enter_long"] = 0
                            dataframe.loc[dataframe.index[-1:], ["enter_long"]] = 1
                            return dataframe

                        def populate_exit_trend(self, dataframe, metadata):
                            dataframe["exit_long"] = 0
                            return dataframe
                    """
                ),
                encoding="utf-8",
            )

            with (
                patch.object(mcp_server, "FREQTRADE_STRATEGY_PATH", Path(temp_dir)),
                patch.object(
                    mcp_server.rest_client,
                    "get",
                    new=AsyncMock(
                        side_effect=[
                            [],
                            {
                                "columns": ["date", "open", "high", "low", "close", "volume"],
                                "data": [
                                    [f"2026-04-19T00:{index:02d}:00Z", 1.0, 1.0, 1.0, 1.0, 1.0]
                                    for index in range(41)
                                ],
                                "length": 41,
                            },
                        ]
                    ),
                ),
            ):
                state = asyncio.run(
                    mcp_server._evaluate_trade_signal_state(
                        pair="ETH/USDC",
                        strategy="LongHistoryStrategy",
                    )
                )

        self.assertTrue(state["entryTriggered"])
        self.assertFalse(state["exitTriggered"])

    def test_evaluate_trade_signal_state_raises_when_pair_candles_history_is_too_short(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            strategy_path = Path(temp_dir) / "LongHistoryStrategy.py"
            strategy_path.write_text(
                textwrap.dedent(
                    """
                    class LongHistoryStrategy:
                        timeframe = "5m"
                        startup_candle_count = 40

                        def populate_indicators(self, dataframe, metadata):
                            return dataframe

                        def populate_entry_trend(self, dataframe, metadata):
                            dataframe["enter_long"] = 0
                            return dataframe

                        def populate_exit_trend(self, dataframe, metadata):
                            dataframe["exit_long"] = 0
                            return dataframe
                    """
                ),
                encoding="utf-8",
            )

            with (
                patch.object(mcp_server, "FREQTRADE_STRATEGY_PATH", Path(temp_dir)),
                patch.object(
                    mcp_server.rest_client,
                    "get",
                    new=AsyncMock(
                        side_effect=[
                            [],
                            {
                                "columns": ["date", "open", "high", "low", "close", "volume"],
                                "data": [
                                    [f"2026-04-19T00:{index:02d}:00Z", 1.0, 1.0, 1.0, 1.0, 1.0]
                                    for index in range(10)
                                ],
                                "length": 10,
                            },
                        ]
                    ),
                ),
            ):
                with self.assertRaisesRegex(
                    mcp_server.FreqtradeRestError,
                    "pair_candles returned only 10 candles, need at least 40",
                ):
                    asyncio.run(
                        mcp_server._evaluate_trade_signal_state(
                            pair="ETH/USDC",
                            strategy="LongHistoryStrategy",
                        )
                    )

    def test_evaluate_trade_signal_state_uses_strategy_hook_and_open_position_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            strategy_path = Path(temp_dir) / "HookedStrategy.py"
            strategy_path.write_text(
                textwrap.dedent(
                    """
                    class HookedStrategy:
                        timeframe = "1h"

                        def evaluate_signal(self, pair, timeframe, has_open_position):
                            return {
                                "entryTriggered": pair == "ETH/USDC" and timeframe == "1h",
                                "exitTriggered": has_open_position,
                            }
                    """
                ),
                encoding="utf-8",
            )

            with (
                patch.object(mcp_server, "FREQTRADE_STRATEGY_PATH", Path(temp_dir)),
                patch.object(mcp_server.rest_client, "get", new=AsyncMock(return_value=[{"pair": "ETH/USDC"}])),
            ):
                state = asyncio.run(
                    mcp_server._evaluate_trade_signal_state(
                        pair="ETH/USDC",
                        strategy="HookedStrategy",
                    )
                )

        self.assertEqual(state["pair"], "ETH/USDC")
        self.assertEqual(state["strategy"], "HookedStrategy")
        self.assertEqual(state["timeframe"], "1h")
        self.assertTrue(state["hasOpenPosition"])
        self.assertTrue(state["entryTriggered"])
        self.assertTrue(state["exitTriggered"])

    def test_evaluate_trade_signal_state_uses_standard_populate_methods_when_hook_missing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            strategy_path = Path(temp_dir) / "PopulateStrategy.py"
            strategy_path.write_text(
                textwrap.dedent(
                    """
                    class PopulateStrategy:
                        timeframe = "15m"

                        def populate_indicators(self, dataframe, metadata):
                            dataframe["marker"] = 1
                            return dataframe

                        def populate_entry_trend(self, dataframe, metadata):
                            dataframe["enter_long"] = 1
                            return dataframe

                        def populate_exit_trend(self, dataframe, metadata):
                            dataframe["exit_long"] = 1
                            return dataframe
                    """
                ),
                encoding="utf-8",
            )

            with (
                patch.object(mcp_server, "FREQTRADE_STRATEGY_PATH", Path(temp_dir)),
                patch.object(
                    mcp_server.rest_client,
                    "get",
                    new=AsyncMock(
                        side_effect=[
                            [],
                            self._pair_candles_payload([1.0] * 30),
                        ]
                    ),
                ),
            ):
                state = asyncio.run(
                    mcp_server._evaluate_trade_signal_state(
                        pair="ETH/USDC",
                        strategy="PopulateStrategy",
                    )
                )

        self.assertEqual(state["strategy"], "PopulateStrategy")
        self.assertEqual(state["timeframe"], "15m")
        self.assertFalse(state["hasOpenPosition"])
        self.assertTrue(state["entryTriggered"])
        self.assertTrue(state["exitTriggered"])

    def test_evaluate_trade_signal_state_raises_for_missing_strategy_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch.object(mcp_server, "FREQTRADE_STRATEGY_PATH", Path(temp_dir)),
                patch.object(mcp_server.rest_client, "get", new=AsyncMock(return_value=[])),
            ):
                with self.assertRaisesRegex(
                    mcp_server.FreqtradeRestError,
                    "Strategy file not found for MissingStrategy",
                ):
                    asyncio.run(
                        mcp_server._evaluate_trade_signal_state(
                            pair="ETH/USDC",
                            strategy="MissingStrategy",
                        )
                    )

    def test_evaluate_trade_signal_state_raises_for_unsupported_v1_strategy_shape(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            strategy_path = Path(temp_dir) / "UnsupportedStrategy.py"
            strategy_path.write_text(
                textwrap.dedent(
                    """
                    class UnsupportedStrategy:
                        timeframe = "5m"
                    """
                ),
                encoding="utf-8",
            )

            with (
                patch.object(mcp_server, "FREQTRADE_STRATEGY_PATH", Path(temp_dir)),
                patch.object(mcp_server.rest_client, "get", new=AsyncMock(return_value=[])),
            ):
                with self.assertRaisesRegex(
                    mcp_server.FreqtradeRestError,
                    "UnsupportedStrategy cannot be evaluated in V1",
                ):
                    asyncio.run(
                        mcp_server._evaluate_trade_signal_state(
                            pair="ETH/USDC",
                            strategy="UnsupportedStrategy",
                        )
                    )

    def test_evaluate_trade_signal_returns_buy_when_entry_triggered_without_open_position(self) -> None:
        with patch.object(
            mcp_server,
            "_evaluate_trade_signal_state",
            new=AsyncMock(
                return_value={
                    "pair": "ETH/USDC",
                    "strategy": "MomentumStrategy",
                    "timeframe": "15m",
                    "hasOpenPosition": False,
                    "entryTriggered": True,
                    "exitTriggered": False,
                    "observedAt": "2026-04-19T00:00:00Z",
                }
            ),
        ):
            result = asyncio.run(
                mcp_server.evaluate_trade_signal(
                    pair="ETH/USDC",
                    strategy="MomentumStrategy",
                    timeframe="15m",
                )
            )

        self.assertEqual(result["signal"], "buy")
        self.assertEqual(result["confidence"], 0.7)
        self.assertEqual(
            result["reason"],
            "entry conditions satisfied on latest candle and no open position",
        )
        self.assertEqual(result["pair"], "ETH/USDC")
        self.assertFalse(result["hasOpenPosition"])
        self.assertTrue(result["entryTriggered"])
        self.assertFalse(result["exitTriggered"])

    def test_evaluate_trade_signal_prefers_normalized_values_from_state(self) -> None:
        with patch.object(
            mcp_server,
            "_evaluate_trade_signal_state",
            new=AsyncMock(
                return_value={
                    "pair": mcp_server.FREQTRADE_SIGNAL_DEFAULT_PAIR,
                    "strategy": "NormalizedStrategy",
                    "timeframe": "30m",
                    "hasOpenPosition": False,
                    "entryTriggered": False,
                    "exitTriggered": False,
                    "observedAt": "2026-04-19T00:00:00Z",
                }
            ),
        ):
            result = asyncio.run(
                mcp_server.evaluate_trade_signal(
                    pair=mcp_server.FREQTRADE_SIGNAL_DEFAULT_PAIR,
                    strategy="RequestedStrategy",
                    timeframe="15m",
                )
            )

        self.assertEqual(result["pair"], mcp_server.FREQTRADE_SIGNAL_DEFAULT_PAIR)
        self.assertEqual(result["strategy"], "NormalizedStrategy")
        self.assertEqual(result["timeframe"], "30m")
        self.assertEqual(result["observedAt"], "2026-04-19T00:00:00Z")

    def test_evaluate_trade_signal_uses_real_default_simple_agent_strategy_payload(self) -> None:
        strategy_path = Path(__file__).resolve().parents[1] / "strategies"
        candle_payload = self._pair_candles_payload([10] * 25 + [9, 8, 7, 6, 5, 20])

        with (
            stub_freqtrade_strategy_module(),
            patch.object(mcp_server, "FREQTRADE_STRATEGY_PATH", strategy_path),
            patch.object(
                mcp_server.rest_client,
                "get",
                new=AsyncMock(side_effect=[[], candle_payload]),
            ),
        ):
            result = asyncio.run(mcp_server.evaluate_trade_signal(pair="ETH/USDC"))

        self.assertEqual(result["pair"], "ETH/USDC")
        self.assertEqual(result["strategy"], "SimpleAgentStrategy")
        self.assertEqual(result["timeframe"], "5m")
        self.assertTrue(result["entryTriggered"])
        self.assertFalse(result["exitTriggered"])
        self.assertEqual(result["signal"], "buy")
        self.assertEqual(
            result["reason"],
            "entry conditions satisfied on latest candle and no open position",
        )

    def test_evaluate_trade_signal_uses_real_default_simple_agent_strategy_bearish_payload(
        self,
    ) -> None:
        strategy_path = Path(__file__).resolve().parents[1] / "strategies"
        candle_payload = self._pair_candles_payload([20] * 25 + [21, 22, 23, 24, 25, 5])

        with (
            stub_freqtrade_strategy_module(),
            patch.object(mcp_server, "FREQTRADE_STRATEGY_PATH", strategy_path),
            patch.object(
                mcp_server.rest_client,
                "get",
                new=AsyncMock(side_effect=[[{"pair": "ETH/USDC"}], candle_payload]),
            ),
        ):
            result = asyncio.run(mcp_server.evaluate_trade_signal(pair="ETH/USDC"))

        self.assertEqual(result["pair"], "ETH/USDC")
        self.assertEqual(result["strategy"], "SimpleAgentStrategy")
        self.assertEqual(result["timeframe"], "5m")
        self.assertTrue(result["hasOpenPosition"])
        self.assertFalse(result["entryTriggered"])
        self.assertTrue(result["exitTriggered"])
        self.assertEqual(result["signal"], "sell")
        self.assertEqual(
            result["reason"],
            "exit conditions satisfied while position is open",
        )

    def test_evaluate_trade_signal_state_raises_when_pair_candles_are_unavailable(self) -> None:
        strategy_path = Path(__file__).resolve().parents[1] / "strategies"

        with (
            stub_freqtrade_strategy_module(),
            patch.object(mcp_server, "FREQTRADE_STRATEGY_PATH", strategy_path),
            patch.object(
                mcp_server.rest_client,
                "get",
                new=AsyncMock(side_effect=[[], {"columns": [], "data": [], "length": 0}]),
            ),
        ):
            with self.assertRaisesRegex(
                mcp_server.FreqtradeRestError,
                "Market data unavailable for ETH/USDC on 5m: pair_candles returned no data",
            ):
                asyncio.run(
                    mcp_server._evaluate_trade_signal_state(
                        pair="ETH/USDC",
                        strategy="SimpleAgentStrategy",
                    )
                )

    def test_evaluate_trade_signal_returns_sell_when_exit_triggered_with_open_position(self) -> None:
        with patch.object(
            mcp_server,
            "_evaluate_trade_signal_state",
            new=AsyncMock(
                return_value={
                    "pair": "ETH/USDC",
                    "strategy": None,
                    "timeframe": "5m",
                    "hasOpenPosition": True,
                    "entryTriggered": False,
                    "exitTriggered": True,
                    "observedAt": "2026-04-19T00:00:00Z",
                }
            ),
        ):
            result = asyncio.run(mcp_server.evaluate_trade_signal(pair="ETH/USDC"))

        self.assertEqual(result["signal"], "sell")
        self.assertEqual(result["confidence"], 0.7)
        self.assertEqual(
            result["reason"],
            "exit conditions satisfied while position is open",
        )
        self.assertTrue(result["hasOpenPosition"])
        self.assertFalse(result["entryTriggered"])
        self.assertTrue(result["exitTriggered"])

    def test_evaluate_trade_signal_returns_hold_when_no_actionable_signal_exists(self) -> None:
        with patch.object(
            mcp_server,
            "_evaluate_trade_signal_state",
            new=AsyncMock(
                return_value={
                    "pair": "ETH/USDC",
                    "strategy": None,
                    "timeframe": "5m",
                    "hasOpenPosition": False,
                    "entryTriggered": False,
                    "exitTriggered": False,
                    "observedAt": "2026-04-19T00:00:00Z",
                }
            ),
        ):
            result = asyncio.run(mcp_server.evaluate_trade_signal(pair="ETH/USDC"))

        self.assertEqual(result["signal"], "hold")
        self.assertEqual(result["confidence"], 0.5)
        self.assertEqual(
            result["reason"],
            "no actionable entry or exit signal on latest candle",
        )
        self.assertFalse(result["hasOpenPosition"])
        self.assertFalse(result["entryTriggered"])
        self.assertFalse(result["exitTriggered"])

    def test_evaluate_trade_signal_rejects_unsupported_pairs_in_v1(self) -> None:
        with self.assertRaisesRegex(
            mcp_server.FreqtradeRestError,
            f"evaluate_trade_signal only supports {mcp_server.FREQTRADE_SIGNAL_DEFAULT_PAIR} in V1",
        ):
            asyncio.run(mcp_server.evaluate_trade_signal(pair="BTC/USDT"))


if __name__ == "__main__":
    unittest.main()
