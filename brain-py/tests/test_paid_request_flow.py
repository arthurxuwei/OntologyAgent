import unittest

import httpx

from paid_request_flow import PaidRequestFlowError, request_with_payment_retry


class PaidRequestFlowTests(unittest.TestCase):
    def test_retries_after_402_and_attaches_payment_hash(self) -> None:
        seen_headers: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payment_tx_hash = request.headers.get("x-payment-tx-hash")
            if payment_tx_hash is None:
                return httpx.Response(
                    402,
                    json={"error": "payment_required"},
                )

            seen_headers.append(payment_tx_hash)
            return httpx.Response(
                200,
                json={"ok": True},
            )

        result = request_with_payment_retry(
            url="https://example.com/paid",
            method="POST",
            max_retries=1,
            timeout_seconds=5,
            transport=httpx.MockTransport(handler),
            send_payment=lambda attempt: f"0xpay{attempt}",
            body={"hello": "world"},
        )

        self.assertEqual(result["upstream"]["status"], 200)
        self.assertEqual(result["paymentTxHashes"], ["0xpay1"])
        self.assertEqual(seen_headers, ["0xpay1"])

    def test_raises_when_402_is_not_resolved(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                402,
                json={"error": "payment_required"},
            )

        with self.assertRaises(PaidRequestFlowError):
            request_with_payment_retry(
                url="https://example.com/paid",
                method="GET",
                max_retries=0,
                timeout_seconds=5,
                transport=httpx.MockTransport(handler),
                send_payment=lambda attempt: f"0xpay{attempt}",
            )


if __name__ == "__main__":
    unittest.main()
