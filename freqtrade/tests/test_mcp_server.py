import asyncio
import unittest

from freqtrade import mcp_server


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


if __name__ == "__main__":
    unittest.main()
