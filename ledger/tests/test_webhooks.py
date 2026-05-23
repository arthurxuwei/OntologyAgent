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


class TestWebhooks(LedgerServiceTestCase):
    def test_configured_usdc_asset_address_follows_base_mainnet_profile(self) -> None:
        with patch.dict(os.environ, {"CHAIN_PROFILE": "base-mainnet"}, clear=False):
            os.environ.pop("X402_USDC_ASSET_ADDRESS", None)
            self.assertEqual(
                webhooks.configured_usdc_asset_address(),
                "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            )

    def test_circle_wallet_webhook_sweeps_inbound_usdc_to_gateway_once(self) -> None:
        main.get_store().bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        class FakeWalletClient:
            def __init__(self) -> None:
                self.requests = []

            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "2.23"}}

            async def gateway_deposit(self, request):
                self.requests.append(request)
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "mode": "gateway_deposit",
                    "gatewayBalance": {"availableAtomic": request.amountAtomic},
                }

        fake_client = FakeWalletClient()
        payload = {
            "subscriptionId": "subscription-1",
            "notificationId": "notification-1",
            "notificationType": "transactions.inbound",
            "notification": {
                "id": "tx-inbound-1",
                "state": "COMPLETE",
                "transactionType": "INBOUND",
                "walletId": "circle-wallet-1",
                "destinationAddress": "0x1111111111111111111111111111111111111111",
                "amounts": ["1.23"],
                "tokenSymbol": "USDC",
                "contractAddress": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            },
            "timestamp": "2026-05-21T06:00:00Z",
            "version": 2,
        }

        with patch.object(services, "get_ledger_wallet_client", return_value=fake_client):
            first = self.client.post("/circle/webhooks/wallets", json=payload)
            second = self.client.post("/circle/webhooks/wallets", json=payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "processed")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "duplicate")
        self.assertEqual(len(fake_client.requests), 1)
        self.assertEqual(fake_client.requests[0].agentId, "agent_research")
        self.assertEqual(fake_client.requests[0].amountAtomic, "2230000")
        self.assertEqual(fake_client.requests[0].refId, "circle-webhook:notification-1")

        state = self.client.get("/ledger/state?agentId=agent_research").json()
        self.assertEqual(len(state["circleWebhookEvents"]), 1)
        self.assertEqual(state["circleWebhookEvents"][0]["status"], "processed")
        self.assertEqual(state["circleWebhookEvents"][0]["transactionId"], "tx-inbound-1")

    def test_circle_wallet_webhook_keeps_inbound_pending_when_gas_topup_is_pending(self) -> None:
        main.get_store().bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        class FakeWalletClient:
            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "2.23"}}

            async def gateway_deposit(self, request):
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "mode": "gas_topup_pending",
                    "gasTopUp": {
                        "intentId": "gas-intent-1",
                        "transactionId": "gas-topup-tx",
                    },
                    "pendingGatewayDeposit": {
                        "agentId": request.agentId,
                        "amountAtomic": request.amountAtomic,
                        "refId": request.refId,
                    },
                }

        payload = {
            "subscriptionId": "subscription-1",
            "notificationId": "notification-gas-pending",
            "notificationType": "transactions.inbound",
            "notification": {
                "id": "tx-inbound-gas-pending",
                "state": "COMPLETE",
                "transactionType": "INBOUND",
                "walletId": "circle-wallet-1",
                "destinationAddress": "0x1111111111111111111111111111111111111111",
                "amounts": ["2.23"],
                "tokenSymbol": "USDC",
                "contractAddress": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
            },
            "timestamp": "2026-05-21T06:00:00Z",
            "version": 2,
        }

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post("/circle/webhooks/wallets", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "pending")
        state = self.client.get("/ledger/state?agentId=agent_research").json()
        self.assertEqual(len(state["entries"]), 1)
        self.assertEqual(state["entries"][0]["entryType"], "pending_inbound")
        self.assertEqual(state["entries"][0]["metadata"]["dashboardStatus"], "pending_inbound_chain")
        self.assertEqual(state["circleWebhookEvents"][0]["status"], "received")
        self.assertEqual(
            state["circleWebhookEvents"][0]["gatewayDepositResult"]["mode"],
            "gas_topup_pending",
        )

    def test_circle_wallet_webhook_routes_gas_topup_completion_to_circle(self) -> None:
        class FakeWalletClient:
            def __init__(self) -> None:
                self.webhook_payloads = []

            async def gas_topup_webhook(self, payload):
                self.webhook_payloads.append(payload)
                return {
                    "matched": True,
                    "resumeRequired": True,
                    "intent": {
                        "intentId": "gas-intent-1",
                        "targetAgentId": "agent_research",
                        "originalGatewayDeposit": {
                            "agentId": "agent_research",
                            "amountAtomic": "2230000",
                            "refId": "circle-webhook:notification-gas-pending",
                        },
                    },
                }

        fake_client = FakeWalletClient()
        scheduled = []
        payload = {
            "subscriptionId": "subscription-1",
            "notificationId": "notification-gas-complete",
            "notificationType": "transactions.outbound",
            "notification": {
                "id": "gas-topup-tx",
                "state": "COMPLETE",
                "transactionType": "OUTBOUND",
                "walletId": "circle-seed",
                "destinationAddress": "0x1111111111111111111111111111111111111111",
                "amounts": ["0.000099"],
                "tokenSymbol": "ETH",
            },
            "timestamp": "2026-05-21T06:01:00Z",
            "version": 2,
        }

        with patch.object(services, "get_ledger_wallet_client", return_value=fake_client), patch.object(
            webhooks,
            "schedule_gas_topup_resume",
            side_effect=lambda intent: scheduled.append(intent),
        ):
            response = self.client.post("/circle/webhooks/wallets", json=payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "processed")
        self.assertEqual(response.json()["reason"], "gas_topup_resume_queued")
        self.assertEqual(len(fake_client.webhook_payloads), 1)
        self.assertEqual(scheduled[0]["intentId"], "gas-intent-1")

    def test_gas_topup_resume_success_credits_original_pending_inbound(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )
        _account, pending_entry = store.record_dashboard_event(
            entry_type="pending_inbound",
            agent_id="agent_research",
            reason="external top-up detected",
            metadata={
                "dashboardStatus": "pending_inbound_chain",
                "amountAtomic": "2230000",
                "counterparty": "External wallet",
                "gatewayStage": "gateway_crediting",
                "txHash": "tx-inbound-gas-pending",
                "network": "Base",
                "circleTransactionId": "tx-inbound-gas-pending",
                "notificationId": "notification-gas-pending",
                "gatewayRefId": "circle-webhook:notification-gas-pending",
            },
        )

        credited = webhooks.credit_gateway_deposit_resumed_from_gas_topup(
            {
                "intentId": "gas-intent-1",
                "targetAgentId": "agent_research",
                "originalGatewayDeposit": {
                    "agentId": "agent_research",
                    "amountAtomic": "2230000",
                    "refId": "circle-webhook:notification-gas-pending",
                },
            },
            {
                "mode": "gateway_deposit",
                "depositTransactionId": "deposit-tx",
                "gatewayBalance": {"availableAtomic": "2230000"},
            },
        )
        duplicate = webhooks.credit_gateway_deposit_resumed_from_gas_topup(
            {
                "intentId": "gas-intent-1",
                "targetAgentId": "agent_research",
                "originalGatewayDeposit": {
                    "agentId": "agent_research",
                    "amountAtomic": "2230000",
                    "refId": "circle-webhook:notification-gas-pending",
                },
            },
            {
                "mode": "gateway_deposit",
                "depositTransactionId": "deposit-tx",
            },
        )

        self.assertIsNotNone(credited)
        self.assertEqual(credited.entryType, "credit")
        self.assertEqual(credited.availableDeltaAtomic, "2230000")
        self.assertEqual(credited.metadata["linkedEntryId"], pending_entry.entryId)
        self.assertEqual(credited.metadata["depositTransactionId"], "deposit-tx")
        self.assertEqual(duplicate.entryId, credited.entryId)
        state = store.load()
        self.assertEqual(len([entry for entry in state.entries if entry.entryType == "credit"]), 1)
        self.assertEqual(state.accounts[0].availableAtomic, "2230000")

    def test_circle_wallet_webhook_sweeps_confirmed_inbound_usdc_to_gateway(self) -> None:
        main.get_store().bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        class FakeWalletClient:
            def __init__(self) -> None:
                self.requests = []

            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "2.01"}}

            async def gateway_deposit(self, request):
                self.requests.append(request)
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "mode": "gateway_deposit",
                }

        fake_client = FakeWalletClient()
        with patch.object(services, "get_ledger_wallet_client", return_value=fake_client):
            response = self.client.post(
                "/circle/webhooks/wallets",
                json={
                    "subscriptionId": "subscription-1",
                    "notificationId": "notification-confirmed",
                    "notificationType": "transactions.inbound",
                    "notification": {
                        "id": "tx-inbound-confirmed",
                        "state": "CONFIRMED",
                        "transactionType": "INBOUND",
                        "walletId": "circle-wallet-1",
                        "destinationAddress": "0x1111111111111111111111111111111111111111",
                        "amounts": ["1"],
                        "tokenId": "bdf128b4-827b-5267-8f9e-243694989b5f",
                    },
                    "timestamp": "2026-05-21T06:00:00Z",
                    "version": 2,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "processed")
        self.assertEqual(len(fake_client.requests), 1)
        self.assertEqual(fake_client.requests[0].amountAtomic, "2010000")

    def test_circle_wallet_webhook_sweeps_one_usdc_minimum(self) -> None:
        main.get_store().bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        class FakeWalletClient:
            def __init__(self) -> None:
                self.requests = []

            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "1.00"}}

            async def gateway_deposit(self, request):
                self.requests.append(request)
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "mode": "gateway_deposit",
                }

        fake_client = FakeWalletClient()
        with patch.object(services, "get_ledger_wallet_client", return_value=fake_client):
            response = self.client.post(
                "/circle/webhooks/wallets",
                json={
                    "subscriptionId": "subscription-1",
                    "notificationId": "notification-one-usdc",
                    "notificationType": "transactions.inbound",
                    "notification": {
                        "id": "tx-inbound-one-usdc",
                        "state": "CONFIRMED",
                        "transactionType": "INBOUND",
                        "walletId": "circle-wallet-1",
                        "destinationAddress": "0x1111111111111111111111111111111111111111",
                        "amounts": ["1"],
                        "tokenSymbol": "USDC",
                    },
                    "timestamp": "2026-05-21T06:00:00Z",
                    "version": 2,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "processed")
        self.assertEqual(len(fake_client.requests), 1)
        self.assertEqual(fake_client.requests[0].amountAtomic, "1000000")

    def test_circle_wallet_webhook_credits_current_inbound_amount_when_sweeping_larger_wallet_balance(self) -> None:
        main.get_store().bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        class FakeWalletClient:
            def __init__(self) -> None:
                self.requests = []

            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "3.00"}}

            async def gateway_deposit(self, request):
                self.requests.append(request)
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "mode": "gateway_deposit",
                    "gatewayBalance": {"availableAtomic": "1922000"},
                }

        fake_client = FakeWalletClient()
        with patch.object(services, "get_ledger_wallet_client", return_value=fake_client):
            response = self.client.post(
                "/circle/webhooks/wallets",
                json={
                    "subscriptionId": "subscription-1",
                    "notificationId": "notification-current-amount",
                    "notificationType": "transactions.inbound",
                    "notification": {
                        "id": "tx-inbound-current-amount",
                        "state": "CONFIRMED",
                        "transactionType": "INBOUND",
                        "walletId": "circle-wallet-1",
                        "destinationAddress": "0x1111111111111111111111111111111111111111",
                        "amounts": ["1"],
                        "tokenSymbol": "USDC",
                    },
                    "timestamp": "2026-05-21T06:00:00Z",
                    "version": 2,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "processed")
        self.assertEqual(fake_client.requests[0].amountAtomic, "3000000")
        entries = main.get_store().load().entries
        self.assertEqual(entries[1].entryType, "credit")
        self.assertEqual(entries[1].availableDeltaAtomic, "1000000")
        self.assertEqual(entries[1].metadata["amountAtomic"], "1000000")

    def test_circle_wallet_webhook_credits_pending_inbounds_covered_by_batch_sweep(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )
        for suffix in ("one", "two"):
            store.record_dashboard_event(
                entry_type="pending_inbound",
                agent_id="agent_research",
                reason="external top-up detected",
                metadata={
                    "dashboardStatus": "pending_inbound_chain",
                    "amountAtomic": "1000000",
                    "counterparty": "External wallet",
                    "gatewayStage": "gateway_crediting",
                    "txHash": f"tx-inbound-{suffix}",
                    "network": "Base",
                    "circleTransactionId": f"tx-inbound-{suffix}",
                    "notificationId": f"notification-{suffix}",
                    "gatewayRefId": f"circle-webhook:notification-{suffix}",
                },
            )

        class FakeWalletClient:
            def __init__(self) -> None:
                self.requests = []

            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "3.00"}}

            async def gateway_deposit(self, request):
                self.requests.append(request)
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "mode": "gateway_deposit",
                    "gatewayBalance": {"availableAtomic": request.amountAtomic},
                }

        fake_client = FakeWalletClient()
        with patch.object(services, "get_ledger_wallet_client", return_value=fake_client):
            response = self.client.post(
                "/circle/webhooks/wallets",
                json={
                    "subscriptionId": "subscription-1",
                    "notificationId": "notification-three",
                    "notificationType": "transactions.inbound",
                    "notification": {
                        "id": "tx-inbound-three",
                        "state": "CONFIRMED",
                        "transactionType": "INBOUND",
                        "walletId": "circle-wallet-1",
                        "destinationAddress": "0x1111111111111111111111111111111111111111",
                        "amounts": ["1"],
                        "tokenSymbol": "USDC",
                    },
                    "timestamp": "2026-05-21T06:00:00Z",
                    "version": 2,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "processed")
        self.assertEqual(len(fake_client.requests), 1)
        self.assertEqual(fake_client.requests[0].amountAtomic, "3000000")

        entries = store.load().entries
        credits = [entry for entry in entries if entry.entryType == "credit"]
        self.assertEqual([entry.availableDeltaAtomic for entry in credits], ["1000000"] * 3)
        self.assertEqual(
            [entry.metadata["circleTransactionId"] for entry in credits],
            ["tx-inbound-one", "tx-inbound-two", "tx-inbound-three"],
        )
        self.assertEqual(
            {entry.metadata["linkedEntryId"] for entry in credits},
            {entry.entryId for entry in entries if entry.entryType == "pending_inbound"},
        )

    def test_circle_wallet_webhook_replay_with_new_notification_for_processed_transaction_is_duplicate(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )
        store.credit(
            agent_id="agent_research",
            amount_atomic="1000000",
            reason="Gateway Wallet credited",
            metadata={
                "dashboardStatus": "credited",
                "amountAtomic": "1000000",
                "circleTransactionId": "tx-inbound-replayed",
                "notificationId": "notification-original",
            },
        )

        class FakeWalletClient:
            async def status(self, *, wallet_address, circle_wallet_id):
                raise AssertionError("processed transaction replay must not refetch wallet status")

            async def gateway_deposit(self, request):
                raise AssertionError("processed transaction replay must not call gateway_deposit")

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post(
                "/circle/webhooks/wallets",
                json={
                    "subscriptionId": "subscription-1",
                    "notificationId": "notification-replay",
                    "notificationType": "transactions.inbound",
                    "notification": {
                        "id": "tx-inbound-replayed",
                        "state": "COMPLETE",
                        "transactionType": "INBOUND",
                        "walletId": "circle-wallet-1",
                        "destinationAddress": "0x1111111111111111111111111111111111111111",
                        "amounts": ["1"],
                        "tokenSymbol": "USDC",
                    },
                    "timestamp": "2026-05-21T06:00:00Z",
                    "version": 2,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "duplicate")
        self.assertEqual(len(main.get_store().load().entries), 1)

    def test_circle_wallet_webhook_sweeps_sub_one_usdc_wallet_balance(self) -> None:
        main.get_store().bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        class FakeWalletClient:
            def __init__(self) -> None:
                self.deposits = []

            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "0.5"}}

            async def gateway_deposit(self, request):
                self.deposits.append(request)
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "mode": "gateway_deposit",
                }

        fake_client = FakeWalletClient()
        with patch.object(services, "get_ledger_wallet_client", return_value=fake_client):
            response = self.client.post(
                "/circle/webhooks/wallets",
                json={
                    "subscriptionId": "subscription-1",
                    "notificationId": "notification-threshold",
                    "notificationType": "transactions.inbound",
                    "notification": {
                        "id": "tx-inbound-threshold",
                        "state": "CONFIRMED",
                        "transactionType": "INBOUND",
                        "walletId": "circle-wallet-1",
                        "destinationAddress": "0x1111111111111111111111111111111111111111",
                        "amounts": ["0.5"],
                        "tokenSymbol": "USDC",
                    },
                    "timestamp": "2026-05-21T06:00:00Z",
                    "version": 2,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "processed")
        self.assertEqual(len(fake_client.deposits), 1)
        self.assertEqual(fake_client.deposits[0].amountAtomic, "500000")
        entries = main.get_store().load().entries
        self.assertEqual(entries[0].entryType, "pending_inbound")
        self.assertEqual(entries[1].entryType, "credit")

    def test_circle_wallet_webhook_skips_inbound_before_completion(self) -> None:
        main.get_store().bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        class FakeWalletClient:
            async def gateway_deposit(self, request):
                raise AssertionError("pending inbound transaction must not be swept")

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post(
                "/circle/webhooks/wallets",
                json={
                    "subscriptionId": "subscription-1",
                    "notificationId": "notification-pending",
                    "notificationType": "transactions.inbound",
                    "notification": {
                        "id": "tx-inbound-pending",
                        "state": "PENDING",
                        "transactionType": "INBOUND",
                        "walletId": "circle-wallet-1",
                        "amounts": ["1.23"],
                        "tokenSymbol": "USDC",
                    },
                    "timestamp": "2026-05-21T06:00:00Z",
                    "version": 2,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "skipped")
        state = self.client.get("/ledger/state?agentId=agent_research").json()
        self.assertEqual(state["circleWebhookEvents"][0]["status"], "skipped")

    def test_circle_wallet_webhook_accepts_valid_circle_signature(self) -> None:
        os.environ["CIRCLE_WEBHOOK_VERIFY_SIGNATURE"] = "true"
        os.environ["CIRCLE_API_KEY"] = "test-circle-api-key"
        main.get_store().bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key_der = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        body = json.dumps(
            {
                "subscriptionId": "subscription-1",
                "notificationId": "notification-signed",
                "notificationType": "transactions.inbound",
                "notification": {
                    "id": "tx-inbound-signed",
                    "state": "COMPLETE",
                    "transactionType": "INBOUND",
                    "walletId": "circle-wallet-1",
                    "amounts": ["0.5"],
                    "tokenSymbol": "USDC",
                },
                "timestamp": "2026-05-21T06:00:00Z",
                "version": 2,
            },
            separators=(",", ":"),
        )
        signature = private_key.sign(body.encode("utf-8"), ec.ECDSA(hashes.SHA256()))

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "data": {
                        "id": "public-key-1",
                        "algorithm": "ECDSA_SHA_256",
                        "publicKey": base64.b64encode(public_key_der).decode("ascii"),
                    }
                }

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                pass

            async def get(self, url, headers):
                self.url = url
                self.headers = headers
                return FakeResponse()

        class FakeWalletClient:
            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "2.50"}}

            async def gateway_deposit(self, request):
                return {
                    "agentId": request.agentId,
                    "amountAtomic": request.amountAtomic,
                    "mode": "gateway_deposit",
                }

        with (
            patch.object(webhooks.httpx, "AsyncClient", FakeAsyncClient),
            patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()),
        ):
            response = self.client.post(
                "/circle/webhooks/wallets",
                content=body,
                headers={
                    "content-type": "application/json",
                    "x-circle-key-id": "public-key-1",
                    "x-circle-signature": base64.b64encode(signature).decode("ascii"),
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "processed")
