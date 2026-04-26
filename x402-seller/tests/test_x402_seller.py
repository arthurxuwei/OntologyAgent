import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main
from x402_seller import X402SellerConfig, X402SellerService, encode_header


class X402SellerTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["X402_PAY_TO"] = "0x2222222222222222222222222222222222222222"
        os.environ["X402_FACILITATOR_URL"] = "http://facilitator.test"
        os.environ["X402_NETWORK"] = "eip155:84532"
        os.environ["X402_PRICE"] = "$0.01"
        main.get_x402_seller_service.cache_clear()

    def tearDown(self) -> None:
        main.get_x402_seller_service.cache_clear()

    def test_demo_resource_returns_standard_402_header(self) -> None:
        client = TestClient(main.app)

        response = client.get("/x402/demo-resource")

        self.assertEqual(response.status_code, 402)
        self.assertIn("PAYMENT-REQUIRED", response.headers)
        self.assertEqual(response.json()["x402Version"], 2)
        self.assertEqual(response.json()["accepts"][0]["network"], "eip155:84532")

    def test_demo_resource_returns_payment_response_on_success(self) -> None:
        service = X402SellerService(
            X402SellerConfig(
                pay_to="0x2222222222222222222222222222222222222222",
                facilitator_url="http://facilitator.test",
                price="$0.01",
            )
        )
        service.verify_payment = AsyncMock(return_value={"isValid": True})
        service.settle_payment = AsyncMock(
            return_value={
                "success": True,
                "transaction": "0xsettled",
                "network": "eip155:84532",
            }
        )
        client = TestClient(main.app)

        with patch.object(main, "get_x402_seller_service", return_value=service):
            response = client.get(
                "/x402/demo-resource",
                headers={
                    "PAYMENT-SIGNATURE": encode_header(
                        {
                            "x402Version": 2,
                            "accepted": {
                                "network": "eip155:84532",
                                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                                "amount": "10000",
                                "payTo": "0x2222222222222222222222222222222222222222",
                                "scheme": "exact",
                                "maxTimeoutSeconds": 300,
                                "extra": {"name": "USDC", "version": "2"},
                            },
                            "payload": {
                                "authorization": {
                                    "from": "0x1111111111111111111111111111111111111111",
                                }
                            },
                        }
                    )
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("PAYMENT-RESPONSE", response.headers)
        self.assertEqual(response.json()["ok"], True)

    def test_agent_service_resource_returns_standard_402_header(self) -> None:
        client = TestClient(main.app)

        response = client.get("/x402/agent-services/research-summary")

        self.assertEqual(response.status_code, 402)
        self.assertIn("PAYMENT-REQUIRED", response.headers)
        self.assertEqual(
            response.json()["accepts"][0]["payTo"],
            "0x2222222222222222222222222222222222222222",
        )
        self.assertEqual(
            response.json()["resource"]["url"],
            "http://testserver/x402/agent-services/research-summary",
        )

    def test_agent_service_returns_structured_result_on_success(self) -> None:
        service = X402SellerService(
            X402SellerConfig(
                pay_to="0x2222222222222222222222222222222222222222",
                facilitator_url="http://facilitator.test",
                price="$0.01",
            )
        )
        service.verify_payment = AsyncMock(return_value={"isValid": True})
        service.settle_payment = AsyncMock(
            return_value={
                "success": True,
                "transaction": "0xsettled",
                "network": "eip155:84532",
            }
        )
        client = TestClient(main.app)

        with patch.object(main, "get_x402_seller_service", return_value=service):
            response = client.get(
                "/x402/agent-services/research-summary",
                headers={
                    "PAYMENT-SIGNATURE": encode_header(
                        {
                            "x402Version": 2,
                            "accepted": {
                                "network": "eip155:84532",
                                "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                                "amount": "10000",
                                "payTo": "0x2222222222222222222222222222222222222222",
                                "scheme": "exact",
                                "maxTimeoutSeconds": 300,
                                "extra": {"name": "USDC", "version": "2"},
                            },
                            "payload": {
                                "authorization": {
                                    "from": "0x1111111111111111111111111111111111111111",
                                }
                            },
                        }
                    )
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("PAYMENT-RESPONSE", response.headers)
        self.assertEqual(response.json()["ok"], True)
        self.assertEqual(response.json()["service"], "research-summary")
        self.assertEqual(response.json()["settlement"]["transaction"], "0xsettled")


if __name__ == "__main__":
    unittest.main()
