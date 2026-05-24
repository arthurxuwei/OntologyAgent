import asyncio
import base64
import json
import os
import shutil
import uuid
from pathlib import Path
from unittest.mock import patch

import auth
import httpx
import jwt
import main
import services
import webhooks
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from fastapi.testclient import TestClient
from helpers import LedgerServiceTestCase


class TestWalletsOnrampState(LedgerServiceTestCase):

    def test_wallet_client_uses_circle_rest_status(self) -> None:
        async def handler(request):
            self.assertEqual(request.url.path, "/circle/wallets/status")
            self.assertEqual(request.url.params["walletAddress"], "0xabc")
            return httpx.Response(200, json={"balances": {"USDC": "1.23"}})

        client = main.LedgerWalletClient(
            wallet_http_url="http://circle.test",
            timeout_seconds=30,
            transport=httpx.MockTransport(handler),
        )

        status = asyncio.run(client.status(wallet_address="0xabc", circle_wallet_id=None))

        self.assertEqual(status["balances"]["USDC"], "1.23")

    def test_wallet_get_or_create_creates_zero_balance_ledger_account(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {
                    "circleWalletId": "circle-wallet-1",
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "accountType": "EOA",
                    "mode": "circle",
                    "binding": {
                        "agentName": request.agentName,
                        "agentId": request.agentId,
                        "email": request.email,
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "circleWalletId": "circle-wallet-1",
                        "circleWalletSetId": "circle-wallet-set",
                        "blockchain": "BASE-SEPOLIA",
                        "mode": "circle",
                        "accountType": "EOA",
                        "updatedAt": main.now_iso(),
                    },
                }

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post(
                "/ledger/wallets/get-or-create",
                json={
                    "agentName": "Research Agent",
                    "agentId": "agent_research",
                    "email": "agent@example.com",
                    "circleWalletId": "circle-wallet-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["wallet"]["circleWalletId"], "circle-wallet-1")
        self.assertEqual(payload["wallet"]["binding"]["agentId"], "agent_research")
        self.assertEqual(payload["account"]["agentId"], "agent_research")
        self.assertEqual(payload["account"]["agentName"], "Research Agent")
        self.assertEqual(payload["account"]["email"], "agent@example.com")
        self.assertEqual(payload["account"]["accountType"], "EOA")
        self.assertEqual(payload["account"]["availableAtomic"], "0")
        self.assertEqual(payload["account"]["lockedAtomic"], "0")

        state = self.ledger_domain_state("agent_research")
        self.assertEqual(len(state["accounts"]), 1)
        self.assertEqual(state["accounts"][0]["agentId"], "agent_research")
        self.assertEqual(state["accounts"][0]["agentName"], "Research Agent")
        self.assertEqual(state["accounts"][0]["email"], "agent@example.com")
        self.assertEqual(
            state["accounts"][0]["walletAddress"],
            "0x1111111111111111111111111111111111111111",
        )
        self.assertEqual(state["accounts"][0]["circleWalletId"], "circle-wallet-1")
        self.assertEqual(state["accounts"][0]["accountType"], "EOA")
        self.assertEqual(state["entries"], [])

    def test_wallet_get_or_create_rejects_missing_email_without_creating_account(self) -> None:
        class FakeWalletClient:
            called = False

            async def get_or_create(self, request):
                self.called = True
                return {
                    "circleWalletId": "circle-wallet-1",
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "accountType": "EOA",
                    "mode": "circle",
                }

        fake_client = FakeWalletClient()
        with patch.object(services, "get_ledger_wallet_client", return_value=fake_client):
            response = self.client.post(
                "/ledger/wallets/get-or-create",
                json={
                    "agentName": "X",
                    "agentId": "x",
                    "email": "   ",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "email is required")
        self.assertFalse(fake_client.called)
        state = self.ledger_domain_state()
        self.assertEqual(state["accounts"], [])

    def test_gateway_deposit_proxies_to_wallet_rest_client(self) -> None:
        class FakeWalletClient:
            async def gateway_deposit(self, request):
                self.request = request
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "mode": "gateway_deposit",
                    "gatewayBalance": {"availableAtomic": request.amountAtomic},
                }

        fake_client = FakeWalletClient()
        with patch.object(services, "get_ledger_wallet_client", return_value=fake_client):
            response = self.client.post(
                "/ledger/gateway/deposits",
                json={
                    "agentId": "agent_research",
                    "amountAtomic": "1000",
                    "refId": "deposit:test",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], "agent_research")
        self.assertEqual(payload["amountAtomic"], "1000")
        self.assertEqual(payload["mode"], "gateway_deposit")
        self.assertEqual(fake_client.request.refId, "deposit:test")

    def test_gateway_withdrawal_proxies_to_wallet_rest_client(self) -> None:
        class FakeWalletClient:
            async def gateway_withdraw(self, request):
                self.request = request
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "recipientAddress": request.recipientAddress,
                    "mode": "gateway_withdraw",
                    "mintTransactionHash": "0xmint",
                }

        fake_client = FakeWalletClient()
        with patch.object(services, "get_ledger_wallet_client", return_value=fake_client):
            response = self.client.post(
                "/ledger/gateway/withdrawals",
                json={
                    "agentId": "agent_research",
                    "amountAtomic": "1000",
                    "recipientAddress": "0x1111111111111111111111111111111111111111",
                    "refId": "withdraw:test",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], "agent_research")
        self.assertEqual(payload["amountAtomic"], "1000")
        self.assertEqual(payload["mode"], "gateway_withdraw")
        self.assertEqual(fake_client.request.refId, "withdraw:test")

    def test_ledger_state_includes_circle_usdc_balance_for_bound_accounts(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                assert circle_wallet_id == "circle-wallet-1"
                return {
                    "balances": {"USDC": "1.98"},
                    "gatewayBalance": {
                        "availableAtomic": "750000",
                        "totalAtomic": "1250000",
                        "withdrawableAtomic": "500000",
                        "withdrawingAtomic": "500000",
                        "pendingDepositsAtomic": "2250000",
                        "pendingBatchAtomic": "100000",
                        "formattedAvailable": "0.75",
                        "formattedTotal": "1.25",
                        "formattedWithdrawable": "0.5",
                        "formattedWithdrawing": "0.5",
                        "formattedPendingDeposits": "2.25",
                        "formattedPendingBatch": "0.1",
                    },
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_research",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            state = self.ledger_domain_state("agent_research")

        self.assertEqual(state["accounts"][0]["circleUsdcBalance"], "1.98")
        self.assertEqual(state["accounts"][0]["gatewayUsdcAvailable"], "0.75")
        self.assertEqual(state["accounts"][0]["gatewayUsdcTotal"], "1.25")
        self.assertEqual(state["accounts"][0]["gatewayUsdcWithdrawable"], "0.5")
        self.assertEqual(state["accounts"][0]["gatewayUsdcWithdrawing"], "0.5")
        self.assertEqual(state["accounts"][0]["gatewayUsdcPendingDeposits"], "2.25")
        self.assertEqual(state["accounts"][0]["gatewayPendingDepositsAtomic"], "2250000")
        self.assertEqual(state["accounts"][0]["gatewayUsdcPendingBatch"], "0.1")
        self.assertEqual(state["accounts"][0]["gatewayPendingBatchAtomic"], "100000")

    def test_domain_account_list_returns_accounts_without_agent_scope(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_owner",
            agent_name="Owner Agent",
            email="owner@example.com",
            wallet_address=None,
            circle_wallet_id=None,
        )
        store.credit(
            agent_id="agent_owner",
            amount_atomic="5000000",
            reason="owner funding",
            metadata={},
        )

        state = self.ledger_domain_state()

        self.assertEqual([account["agentId"] for account in state["accounts"]], ["agent_owner"])
        self.assertEqual([entry["agentId"] for entry in state["entries"]], ["agent_owner"])
        self.assertEqual(state["escrows"], [])
        self.assertEqual(state["onrampSessions"], [])
        self.assertEqual(state["onrampEvents"], [])
        self.assertEqual(state["chainRecords"], [])
        self.assertEqual(state["settlementRecords"], [])

    def test_admin_ledger_state_returns_full_state(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_owner",
            agent_name="Owner Agent",
            email="owner@example.com",
            wallet_address=None,
            circle_wallet_id=None,
        )
        store.credit(
            agent_id="agent_owner",
            amount_atomic="5000000",
            reason="owner funding",
            metadata={},
        )

        state = self.ledger_domain_state()

        self.assertEqual([account["agentId"] for account in state["accounts"]], ["agent_owner"])
        self.assertEqual([entry["agentId"] for entry in state["entries"]], ["agent_owner"])

    def test_ledger_state_can_be_scoped_to_agent_id(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_owner",
            agent_name="Owner Agent",
            email="owner@example.com",
            wallet_address=None,
            circle_wallet_id=None,
        )
        store.bind_account_wallet(
            agent_id="agent_counterparty",
            agent_name="Counterparty Agent",
            email="counterparty@example.com",
            wallet_address=None,
            circle_wallet_id=None,
        )
        store.bind_account_wallet(
            agent_id="agent_other",
            agent_name="Other Agent",
            email="other@example.com",
            wallet_address=None,
            circle_wallet_id=None,
        )
        store.credit(
            agent_id="agent_owner",
            amount_atomic="5000000",
            reason="owner funding",
            metadata={},
        )
        store.credit(
            agent_id="agent_other",
            amount_atomic="5000000",
            reason="other funding",
            metadata={},
        )
        store.create_escrow(
            buyer_agent_id="agent_owner",
            seller_agent_id="agent_counterparty",
            amount_atomic="1000000",
            task_id="owner_task",
            description=None,
            metadata={},
        )
        store.create_escrow(
            buyer_agent_id="agent_other",
            seller_agent_id="agent_counterparty",
            amount_atomic="1000000",
            task_id="other_task",
            description=None,
            metadata={},
        )

        state = self.ledger_domain_state("agent_owner")

        self.assertEqual(
            [account["agentId"] for account in state["accounts"]],
            ["agent_owner"],
        )
        self.assertEqual(
            {entry["agentId"] for entry in state["entries"]},
            {"agent_owner"},
        )
        self.assertEqual(
            [escrow["taskId"] for escrow in state["escrows"]],
            ["owner_task"],
        )

    def test_ledger_state_uses_circle_balance_as_agent_visible_available(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                assert circle_wallet_id == "circle-wallet-1"
                return {
                    "balances": {"USDC": "1.98"},
                    "gatewayBalance": {
                        "availableAtomic": "750000",
                        "totalAtomic": "1250000",
                        "withdrawableAtomic": "500000",
                        "withdrawingAtomic": "500000",
                        "pendingDepositsAtomic": "2250000",
                        "formattedAvailable": "0.75",
                        "formattedTotal": "1.25",
                        "formattedWithdrawable": "0.5",
                        "formattedWithdrawing": "0.5",
                        "formattedPendingDeposits": "2.25",
                    },
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_research",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            state = self.ledger_domain_state("agent_research")
            state_after_second_read = self.ledger_domain_state("agent_research")

        account = state["accounts"][0]
        self.assertEqual(account["circleUsdcBalance"], "1.98")
        self.assertEqual(account["gatewayUsdcAvailable"], "0.75")
        self.assertEqual(account["gatewayUsdcTotal"], "1.25")
        self.assertEqual(account["gatewayUsdcWithdrawable"], "0.5")
        self.assertEqual(account["gatewayUsdcWithdrawing"], "0.5")
        self.assertEqual(account["gatewayUsdcPendingDeposits"], "2.25")
        self.assertEqual(account["gatewayPendingDepositsAtomic"], "2250000")
        self.assertEqual(account["availableAtomic"], "1980000")
        self.assertNotIn("ledgerAvailableAtomic", account)
        self.assertEqual(account["balanceSource"], "circle")
        self.assertEqual(state["entries"], [])
        self.assertEqual(state_after_second_read["entries"], [])
        self.assertEqual(main.get_store().load().accounts[0].availableAtomic, "0")

    def test_ledger_state_treats_missing_circle_usdc_balance_as_zero(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                assert circle_wallet_id == "circle-wallet-1"
                return {
                    "balances": {"ETH-SEPOLIA": "0.01"},
                    "gatewayBalance": {
                        "totalAtomic": "1922000",
                        "formattedTotal": "1.922",
                    },
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_research",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )
        store.credit(
            agent_id="agent_research",
            amount_atomic="3000000",
            reason="stale ledger credit",
            metadata={},
        )

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            state = self.ledger_domain_state("agent_research")

        account = state["accounts"][0]
        self.assertEqual(account["circleUsdcBalance"], "0")
        self.assertEqual(account["availableAtomic"], "0")
        self.assertEqual(account["balanceSource"], "circle")
        self.assertEqual(account["gatewayUsdcTotal"], "1.922")

    def test_ledger_state_helper_uses_circle_balance_as_agent_visible_available(
        self,
    ) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                assert circle_wallet_id == "circle-wallet-1"
                return {
                    "balances": {"USDC": "1.98"},
                    "gatewayBalance": {
                        "availableAtomic": "750000",
                        "totalAtomic": "1250000",
                        "withdrawableAtomic": "500000",
                        "withdrawingAtomic": "500000",
                        "pendingDepositsAtomic": "2250000",
                        "formattedAvailable": "0.75",
                        "formattedTotal": "1.25",
                        "formattedWithdrawable": "0.5",
                        "formattedWithdrawing": "0.5",
                        "formattedPendingDeposits": "2.25",
                    },
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_research",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )
        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            state = asyncio.run(main.ledger_state_with_circle_balances())

        account = state["accounts"][0]
        self.assertEqual(account["circleUsdcBalance"], "1.98")
        self.assertEqual(account["gatewayUsdcAvailable"], "0.75")
        self.assertEqual(account["gatewayUsdcTotal"], "1.25")
        self.assertEqual(account["gatewayUsdcWithdrawable"], "0.5")
        self.assertEqual(account["gatewayUsdcWithdrawing"], "0.5")
        self.assertEqual(account["gatewayUsdcPendingDeposits"], "2.25")
        self.assertEqual(account["gatewayPendingDepositsAtomic"], "2250000")
        self.assertEqual(account["availableAtomic"], "1980000")
        self.assertNotIn("ledgerAvailableAtomic", account)
        self.assertEqual(account["balanceSource"], "circle")

    def test_wallet_get_or_create_requires_circle_binding_agent_id_to_match_request(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {
                    "binding": {
                        "agentId": "other_agent",
                    },
                }

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post(
                "/ledger/wallets/get-or-create",
                json={
                    "agentName": "Research Agent",
                    "agentId": "agent_research",
                    "email": "agent@example.com",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "circle wallet binding agentId mismatch")
        self.assertEqual(self.ledger_domain_state()["accounts"], [])

    def test_wallet_get_or_create_rejects_non_eoa_circle_wallets(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {
                    "circleWalletId": "circle-wallet-sca",
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "accountType": "SCA",
                    "mode": "circle",
                    "binding": {
                        "agentName": request.agentName,
                        "agentId": request.agentId,
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "circleWalletId": "circle-wallet-sca",
                        "circleWalletSetId": "circle-wallet-set",
                        "blockchain": "BASE-SEPOLIA",
                        "mode": "circle",
                        "accountType": "SCA",
                        "updatedAt": main.now_iso(),
                    },
                }

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post(
                "/ledger/wallets/get-or-create",
                json={
                    "agentName": "Research Agent",
                    "agentId": "agent_research",
                    "email": "agent@example.com",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "claim wallet must be an EOA Circle wallet")
        self.assertEqual(self.ledger_domain_state()["accounts"], [])

    def test_wallet_get_or_create_rest_route_creates_ledger_account(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {
                    "circleWalletId": "circle-wallet-1",
                    "walletAddress": "0x1111111111111111111111111111111111111111",
                    "accountType": "EOA",
                    "mode": "circle",
                    "binding": {
                        "agentName": request.agentName,
                        "agentId": request.agentId,
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "circleWalletId": "circle-wallet-1",
                        "circleWalletSetId": "circle-wallet-set",
                        "blockchain": "BASE-SEPOLIA",
                        "mode": "circle",
                        "accountType": "EOA",
                        "updatedAt": main.now_iso(),
                    },
                }

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post(
                "/ledger/wallets/get-or-create",
                json={
                    "agentName": "Research Agent",
                    "agentId": "agent_research",
                    "email": "agent@example.com",
                    "circleWalletId": "circle-wallet-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["account"]["agentId"], "agent_research")
        self.assertEqual(
            self.ledger_domain_state("agent_research")["accounts"][0]["agentId"],
            "agent_research",
        )

    def test_create_onramp_session_persists_coinbase_hosted_url(self) -> None:
        os.environ["COINBASE_ONRAMP_MOCK"] = "true"
        main.get_coinbase_onramp_client.cache_clear()

        response = self.client.post(
            "/onramp/sessions",
            json={
                "agentId": "agentA",
                "destinationAddress": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
                "paymentAmount": "10.00",
                "idempotencyKey": "fund-agentA-10",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], "agentA")
        self.assertEqual(payload["provider"], "coinbase")
        self.assertEqual(payload["status"], "created")
        self.assertEqual(payload["destinationNetwork"], "base")
        self.assertEqual(payload["purchaseCurrency"], "USDC")
        self.assertTrue(payload["onrampUrl"].startswith("https://pay.coinbase.com/buy/select-asset"))

        state = self.ledger_domain_state("agentA")
        self.assertEqual(len(state["onrampSessions"]), 1)
        self.assertEqual(state["onrampSessions"][0]["idempotencyKey"], "fund-agentA-10")

    def test_create_onramp_session_reuses_idempotency_key(self) -> None:
        os.environ["COINBASE_ONRAMP_MOCK"] = "true"
        main.get_coinbase_onramp_client.cache_clear()
        request = {
            "agentId": "agentA",
            "destinationAddress": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
            "paymentAmount": "10.00",
            "idempotencyKey": "fund-agentA-10",
        }

        first = self.client.post("/onramp/sessions", json=request).json()
        second = self.client.post("/onramp/sessions", json=request).json()

        self.assertEqual(first["sessionId"], second["sessionId"])
        self.assertEqual(
            len(self.ledger_domain_state("agentA")["onrampSessions"]),
            1,
        )

    def test_confirm_onramp_credits_ledger_once(self) -> None:
        os.environ["COINBASE_ONRAMP_MOCK"] = "true"
        main.get_coinbase_onramp_client.cache_clear()
        session = self.client.post(
            "/onramp/sessions",
            json={
                "agentId": "agentA",
                "destinationAddress": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
                "paymentAmount": "10.00",
                "idempotencyKey": "fund-agentA-10",
            },
        ).json()

        request = {
            "providerOrderId": "coinbase_order_123",
            "amountAtomic": "10000000",
            "txHash": "0xabc123",
        }
        first = self.client.post(
            f"/onramp/sessions/{session['sessionId']}/confirm",
            json=request,
        )
        second = self.client.post(
            f"/onramp/sessions/{session['sessionId']}/confirm",
            json=request,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["status"], "credited")
        self.assertEqual(second.json()["status"], "credited")
        state = self.ledger_domain_state("agentA")
        credit_entries = [
            entry for entry in state["entries"]
            if entry["reason"] == "coinbase_onramp_confirmed"
        ]
        self.assertEqual(len(credit_entries), 1)
        self.assertEqual(state["accounts"][0]["agentId"], "agentA")
        self.assertEqual(state["accounts"][0]["availableAtomic"], "10000000")
        self.assertEqual(
            credit_entries[0]["metadata"]["onrampSessionId"],
            session["sessionId"],
        )

    def test_confirm_onramp_rejects_non_positive_atomic_amount(self) -> None:
        os.environ["COINBASE_ONRAMP_MOCK"] = "true"
        main.get_coinbase_onramp_client.cache_clear()
        session = self.client.post(
            "/onramp/sessions",
            json={
                "agentId": "agentA",
                "destinationAddress": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
                "paymentAmount": "10.00",
                "idempotencyKey": "fund-agentA-10",
            },
        ).json()

        response = self.client.post(
            f"/onramp/sessions/{session['sessionId']}/confirm",
            json={"providerOrderId": "coinbase_order_123", "amountAtomic": "0"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "amountAtomic must be a positive integer string")
        state = self.ledger_domain_state("agentA")
        self.assertEqual(state["accounts"], [])

    def test_onramp_rest_route_creates_session(self) -> None:
        os.environ["COINBASE_ONRAMP_MOCK"] = "true"
        main.get_coinbase_onramp_client.cache_clear()
        response = self.client.post(
            "/onramp/sessions",
            json={
                "agentId": "agentA",
                "destinationAddress": "0x742d35Cc6634C0532925a3b844Bc454e4438f44e",
                "paymentAmount": "10.00",
                "idempotencyKey": "fund-agentA-10",
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["agentId"], "agentA")
        self.assertEqual(result["status"], "created")
        self.assertIn("onrampUrl", result)
