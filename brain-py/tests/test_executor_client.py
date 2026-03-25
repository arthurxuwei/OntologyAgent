import unittest

import httpx

from executor_client import ExecutorClient, ExecutorClientError


class ExecutorClientTests(unittest.TestCase):
    def test_sign_transfer_posts_to_new_transfer_endpoint(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["json"] = request.content.decode()
            return httpx.Response(
                200,
                json={
                    "ok": True,
                    "result": {
                        "transfer": {
                            "txHash": "0xabc",
                        }
                    },
                    "meta": {"requestId": "test"},
                },
            )

        client = ExecutorClient(
            base_url="http://executor-ts:3000",
            timeout_seconds=5,
            transport=httpx.MockTransport(handler),
        )

        result = client.sign_transfer(
            to="0x000000000000000000000000000000000000dEaD",
            amount_eth="0.01",
        )

        self.assertEqual(seen["path"], "/transfers/sign")
        self.assertEqual(result["transfer"]["txHash"], "0xabc")

    def test_submit_execution_raises_on_http_failure(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(400, text="bad request")

        client = ExecutorClient(
            base_url="http://executor-ts:3000",
            timeout_seconds=5,
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(ExecutorClientError):
            client.submit_execution(
                to="0x1111111111111111111111111111111111111111",
                value_eth="0.001",
            )


if __name__ == "__main__":
    unittest.main()
