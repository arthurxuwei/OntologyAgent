import asyncio
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

import main
from x402_seller import (
    X402SellerConfig,
    X402SellerService,
    decode_header,
    encode_header,
)


class X402SellerTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["X402_PAY_TO"] = "0x2222222222222222222222222222222222222222"
        os.environ["X402_FACILITATOR_URL"] = "http://facilitator.test"
        os.environ["X402_NETWORK"] = "eip155:84532"
        os.environ["X402_PRICE"] = "$0.01"
        os.environ["X402_GATEWAY_VERIFYING_CONTRACT"] = (
            "0x0077777d7EBA4688BDeF3E311b846F25870A19B9"
        )
        os.environ.pop("CHAIN_PROFILE", None)
        os.environ.pop("X402_USDC_ASSET_ADDRESS", None)
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
        self.assertNotIn("settlement", response.json())
        self.assertEqual(
            decode_header(response.headers["PAYMENT-RESPONSE"])["transaction"],
            "0xsettled",
        )

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
        payment_response = decode_header(response.headers["PAYMENT-RESPONSE"])
        self.assertEqual(payment_response["success"], True)
        self.assertEqual(payment_response["transaction"], "0xsettled")
        self.assertEqual(
            response.json()["settlement"]["transaction"],
            payment_response["transaction"],
        )

    def test_circle_nanopayment_resource_returns_gateway_accept_option(self) -> None:
        client = TestClient(main.app)

        response = client.get("/x402/agent-services/research-summary/nanopayments")

        self.assertEqual(response.status_code, 402)
        accepts = response.json()["accepts"]
        self.assertEqual(len(accepts), 2)
        gateway_accept = next(
            item for item in accepts if item["extra"]["name"] == "GatewayWalletBatched"
        )
        self.assertEqual(gateway_accept["scheme"], "exact")
        self.assertEqual(gateway_accept["network"], "eip155:84532")
        self.assertEqual(gateway_accept["maxTimeoutSeconds"], 605400)
        self.assertEqual(
            gateway_accept["extra"]["verifyingContract"],
            os.environ["X402_GATEWAY_VERIFYING_CONTRACT"],
        )

    def test_circle_nanopayment_resource_uses_circle_gateway_default_contract(self) -> None:
        os.environ.pop("X402_GATEWAY_VERIFYING_CONTRACT", None)
        main.get_x402_seller_service.cache_clear()
        client = TestClient(main.app)

        response = client.get("/x402/agent-services/research-summary/nanopayments")

        self.assertEqual(response.status_code, 402)
        gateway_accept = next(
            item
            for item in response.json()["accepts"]
            if item["extra"]["name"] == "GatewayWalletBatched"
        )
        self.assertEqual(
            gateway_accept["extra"]["verifyingContract"],
            "0x0077777d7EBA4688BDeF3E311b846F25870A19B9",
        )

    def test_circle_nanopayment_resource_uses_base_mainnet_gateway_default_contract(self) -> None:
        os.environ["X402_NETWORK"] = "eip155:8453"
        os.environ.pop("X402_GATEWAY_VERIFYING_CONTRACT", None)
        main.get_x402_seller_service.cache_clear()
        client = TestClient(main.app)

        response = client.get("/x402/agent-services/research-summary/nanopayments")

        self.assertEqual(response.status_code, 402)
        gateway_accept = next(
            item
            for item in response.json()["accepts"]
            if item["extra"]["name"] == "GatewayWalletBatched"
        )
        self.assertEqual(gateway_accept["network"], "eip155:8453")
        self.assertEqual(
            gateway_accept["asset"],
            "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        )
        self.assertEqual(
            gateway_accept["extra"]["verifyingContract"],
            "0x77777777Dcc4d5A8B6E418Fd04D8997ef11000eE",
        )

    def test_circle_nanopayment_resource_follows_base_mainnet_profile_defaults(self) -> None:
        os.environ["CHAIN_PROFILE"] = "base-mainnet"
        os.environ["X402_NETWORK"] = ""
        os.environ["X402_USDC_ASSET_ADDRESS"] = ""
        os.environ["X402_FACILITATOR_URL"] = ""
        os.environ.pop("X402_GATEWAY_VERIFYING_CONTRACT", None)
        main.get_x402_seller_service.cache_clear()
        client = TestClient(main.app)

        response = client.get("/x402/agent-services/research-summary/nanopayments")

        self.assertEqual(response.status_code, 402)
        gateway_accept = next(
            item
            for item in response.json()["accepts"]
            if item["extra"]["name"] == "GatewayWalletBatched"
        )
        self.assertEqual(gateway_accept["network"], "eip155:8453")
        self.assertEqual(
            gateway_accept["asset"],
            "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        )
        self.assertEqual(
            gateway_accept["extra"]["verifyingContract"],
            "0x77777777Dcc4d5A8B6E418Fd04D8997ef11000eE",
        )

    def test_circle_nanopayment_resource_uses_selected_gateway_accept_entry(self) -> None:
        requests_by_path = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            requests_by_path[request.url.path] = json.loads(request.content.decode("utf-8"))
            if request.url.path == "/v1/x402/verify":
                return httpx.Response(200, json={"isValid": True}, request=request)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "transaction": "0xgatewaysettled",
                    "network": "eip155:84532",
                },
                request=request,
            )

        service = X402SellerService(
            X402SellerConfig(
                pay_to="0x2222222222222222222222222222222222222222",
                facilitator_url="http://facilitator.test",
                price="$0.01",
                gateway_verifying_contract=os.environ["X402_GATEWAY_VERIFYING_CONTRACT"],
            ),
            transport=httpx.MockTransport(handler),
        )
        client = TestClient(main.app)

        with patch.object(main, "get_x402_seller_service", return_value=service):
            response = client.get(
                "/x402/agent-services/research-summary/nanopayments",
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
                                "maxTimeoutSeconds": 605400,
                                "extra": {
                                    "name": "GatewayWalletBatched",
                                    "version": "1",
                                    "verifyingContract": os.environ[
                                        "X402_GATEWAY_VERIFYING_CONTRACT"
                                    ],
                                },
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
        self.assertEqual(response.json()["service"], "research-summary-nanopayments")
        self.assertEqual(
            requests_by_path["/v1/x402/verify"]["paymentRequirements"]["extra"]["name"],
            "GatewayWalletBatched",
        )
        self.assertEqual(
            requests_by_path["/v1/x402/settle"]["paymentRequirements"]["extra"][
                "verifyingContract"
            ],
            os.environ["X402_GATEWAY_VERIFYING_CONTRACT"],
        )
        self.assertEqual(
            decode_header(response.headers["PAYMENT-RESPONSE"])["transaction"],
            "0xgatewaysettled",
        )

    def test_facilitator_http_error_preserves_structured_detail(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                502,
                json={
                    "errorReason": "INVALID_SIGNATURE",
                    "errorMessage": "bad gateway signature",
                },
                request=request,
            )

        service = X402SellerService(
            X402SellerConfig(
                pay_to="0x2222222222222222222222222222222222222222",
                facilitator_url="http://facilitator.test",
                price="$0.01",
                gateway_verifying_contract=os.environ["X402_GATEWAY_VERIFYING_CONTRACT"],
            ),
            transport=httpx.MockTransport(handler),
        )
        client = TestClient(main.app)

        with patch.object(main, "get_x402_seller_service", return_value=service):
            response = client.get(
                "/x402/agent-services/research-summary/nanopayments",
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
                                "maxTimeoutSeconds": 605400,
                                "extra": {
                                    "name": "GatewayWalletBatched",
                                    "version": "1",
                                    "verifyingContract": os.environ[
                                        "X402_GATEWAY_VERIFYING_CONTRACT"
                                    ],
                                },
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

        self.assertEqual(response.status_code, 502)
        detail = response.json()["detail"]
        self.assertEqual(detail["message"], "Facilitator request failed")
        self.assertEqual(detail["statusCode"], 502)
        self.assertEqual(detail["payload"]["errorReason"], "INVALID_SIGNATURE")
        self.assertEqual(detail["payload"]["errorMessage"], "bad gateway signature")

    def test_verify_payment_follows_facilitator_redirects(self) -> None:
        requests_seen = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests_seen.append(str(request.url))
            if len(requests_seen) == 1:
                return httpx.Response(
                    308,
                    headers={"location": "http://facilitator.test/verify/"},
                    text="Redirecting...\n",
                    request=request,
                )

            return httpx.Response(
                200,
                json={"isValid": True},
                request=request,
            )

        service = X402SellerService(
            X402SellerConfig(
                pay_to="0x2222222222222222222222222222222222222222",
                facilitator_url="http://facilitator.test",
                price="$0.01",
            ),
            transport=httpx.MockTransport(handler),
        )

        result = asyncio.run(
            service.verify_payment(
                {"x402Version": 2},
                {
                    "accepts": [
                        {
                            "scheme": "exact",
                            "network": "eip155:84532",
                            "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
                            "amount": "10000",
                            "payTo": "0x2222222222222222222222222222222222222222",
                            "maxTimeoutSeconds": 300,
                            "extra": {"name": "USDC", "version": "2"},
                        }
                    ]
                },
            )
        )

        self.assertEqual(result["isValid"], True)
        self.assertEqual(
            requests_seen,
            [
                "http://facilitator.test/verify",
                "http://facilitator.test/verify/",
            ],
        )


if __name__ == "__main__":
    unittest.main()
