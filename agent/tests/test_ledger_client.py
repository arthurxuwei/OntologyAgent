import asyncio
import unittest

import httpx

from ledger_client import LedgerClient, LedgerClientError


class LedgerClientTests(unittest.TestCase):
    def test_get_state_calls_ledger_state_endpoint(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "GET")
            self.assertEqual(request.url.path, "/ledger/state")
            return httpx.Response(200, json={"accounts": [], "entries": [], "escrows": []})

        client = LedgerClient(
            "http://ledger:8092",
            transport=httpx.MockTransport(handler),
        )

        result = asyncio.run(client.get_state())

        self.assertEqual(result["accounts"], [])

    def test_credit_posts_amount_and_reason(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/ledger/accounts/agent_buyer/credit")
            self.assertEqual(
                request.read().decode(),
                '{"amountAtomic":"5000000","reason":"demo funding"}',
            )
            return httpx.Response(
                200,
                json={"account": {"agentId": "agent_buyer"}, "entry": {"entryId": "entry_1"}},
            )

        client = LedgerClient(
            "http://ledger:8092/",
            transport=httpx.MockTransport(handler),
        )

        result = asyncio.run(
            client.credit_balance(
                "agent_buyer",
                amount_atomic="5000000",
                reason="demo funding",
            )
        )

        self.assertEqual(result["account"]["agentId"], "agent_buyer")

    def test_create_escrow_posts_buyer_seller_and_amount(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.method, "POST")
            self.assertEqual(request.url.path, "/ledger/escrows")
            self.assertEqual(
                request.read().decode(),
                (
                    '{"buyerAgentId":"agent_buyer","sellerAgentId":"agent_seller",'
                    '"amountAtomic":"3000000","taskId":"task_123",'
                    '"description":"Research task"}'
                ),
            )
            return httpx.Response(200, json={"escrow": {"escrowId": "escrow_1"}})

        client = LedgerClient(
            "http://ledger:8092",
            transport=httpx.MockTransport(handler),
        )

        result = asyncio.run(
            client.create_escrow(
                buyer_agent_id="agent_buyer",
                seller_agent_id="agent_seller",
                amount_atomic="3000000",
                task_id="task_123",
                description="Research task",
            )
        )

        self.assertEqual(result["escrow"]["escrowId"], "escrow_1")

    def test_release_and_refund_post_to_escrow_action_endpoints(self) -> None:
        paths: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            return httpx.Response(200, json={"escrow": {"escrowId": "escrow_1"}})

        client = LedgerClient(
            "http://ledger:8092",
            transport=httpx.MockTransport(handler),
        )

        asyncio.run(client.release_escrow("escrow_1"))
        asyncio.run(client.refund_escrow("escrow_1"))

        self.assertEqual(
            paths,
            [
                "/ledger/escrows/escrow_1/release",
                "/ledger/escrows/escrow_1/refund",
            ],
        )

    def test_raises_structured_error_for_ledger_failure(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, json={"detail": "insufficient available balance"})

        client = LedgerClient(
            "http://ledger:8092",
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaisesRegex(
            LedgerClientError,
            "Ledger request failed: 400 insufficient available balance",
        ):
            asyncio.run(
                client.create_escrow(
                    buyer_agent_id="agent_buyer",
                    seller_agent_id="agent_seller",
                    amount_atomic="3000000",
                )
            )


if __name__ == "__main__":
    unittest.main()
