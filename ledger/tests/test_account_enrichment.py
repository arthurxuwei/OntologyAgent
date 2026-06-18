import asyncio
import unittest
from unittest.mock import patch

import services
from config import DEFAULT_ASSET


class _RecordingWalletClient:
    """Fake wallet client that records how many status() calls overlap."""

    def __init__(self, delay: float = 0.05) -> None:
        self.delay = delay
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0

    async def status(self, *, wallet_address, circle_wallet_id):
        self.calls += 1
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.in_flight -= 1
        return {"balances": {DEFAULT_ASSET: "1.5"}}


class AccountEnrichmentConcurrencyTest(unittest.TestCase):
    def _accounts(self, count: int) -> list[dict]:
        return [
            {"agentId": f"agent_{i}", "walletAddress": f"0x{i:040x}"}
            for i in range(count)
        ]

    def test_enrich_runs_concurrently_and_bounded(self) -> None:
        accounts = self._accounts(10)
        fake = _RecordingWalletClient()

        with patch.object(services, "get_ledger_wallet_client", return_value=fake):
            result = asyncio.run(services.enrich_accounts_with_circle_balances(accounts))

        # Every wallet-bearing account is still queried exactly once.
        self.assertEqual(fake.calls, 10)
        # Balances are applied to each account.
        self.assertTrue(all(a["circleUsdcBalance"] == "1.5" for a in result))
        # Serial enrichment would peak at 1 in-flight call; concurrency must exceed that.
        self.assertGreater(fake.max_in_flight, 1)
        # ...but stay bounded by the configured concurrency limit.
        self.assertLessEqual(fake.max_in_flight, services.CIRCLE_BALANCE_CONCURRENCY)

    def test_enrich_skips_accounts_without_wallet(self) -> None:
        accounts = [{"agentId": "no_wallet"}]
        fake = _RecordingWalletClient()

        with patch.object(services, "get_ledger_wallet_client", return_value=fake):
            result = asyncio.run(services.enrich_accounts_with_circle_balances(accounts))

        self.assertEqual(fake.calls, 0)
        self.assertNotIn("circleUsdcBalance", result[0])


if __name__ == "__main__":
    unittest.main()
