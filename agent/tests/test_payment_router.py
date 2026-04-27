import unittest

from payment_router import PaymentIntent, route_payment_intent


class PaymentRouterTests(unittest.TestCase):
    def test_routes_async_task_to_ledger_escrow(self) -> None:
        decision = route_payment_intent(
            PaymentIntent(
                purpose="commission research report",
                deliveryMode="async_task",
                requiresAcceptance=True,
                externalService=False,
            )
        )

        self.assertEqual(decision["method"], "ledger_escrow")
        self.assertEqual(
            decision["allowedTools"],
            [
                "agent_wallet_create_escrow",
                "agent_wallet_release_escrow",
                "agent_wallet_refund_escrow",
            ],
        )
        self.assertIn("asynchronous task", decision["reason"])

    def test_routes_immediate_external_api_to_x402(self) -> None:
        decision = route_payment_intent(
            PaymentIntent(
                purpose="call paid weather API",
                deliveryMode="immediate_api",
                requiresAcceptance=False,
                externalService=True,
                serviceUrl="https://api.example.com/weather",
            )
        )

        self.assertEqual(decision["method"], "x402")
        self.assertEqual(decision["allowedTools"], ["chain_x402_fetch"])
        self.assertIn("immediate paid HTTP", decision["reason"])

    def test_routes_withdrawal_to_chain_transfer(self) -> None:
        decision = route_payment_intent(
            PaymentIntent(
                purpose="withdraw funds to external wallet",
                deliveryMode="withdrawal",
                requiresAcceptance=False,
                externalService=True,
            )
        )

        self.assertEqual(decision["method"], "chain_transfer")
        self.assertEqual(
            decision["allowedTools"],
            ["chain_sign_transfer", "chain_submit_execution"],
        )

    def test_rejects_ambiguous_payment_for_clarification(self) -> None:
        decision = route_payment_intent(
            PaymentIntent(
                purpose="pay another agent",
                deliveryMode="unknown",
                requiresAcceptance=False,
                externalService=False,
            )
        )

        self.assertEqual(decision["method"], "needs_clarification")
        self.assertEqual(decision["allowedTools"], [])

    def test_external_async_work_needs_clarification(self) -> None:
        decision = route_payment_intent(
            PaymentIntent(
                purpose="external vendor will deliver later",
                deliveryMode="async_task",
                requiresAcceptance=True,
                externalService=True,
            )
        )

        self.assertEqual(decision["method"], "needs_clarification")
        self.assertIn("external asynchronous", decision["reason"])


if __name__ == "__main__":
    unittest.main()
