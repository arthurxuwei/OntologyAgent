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


class TestStoreRouter(LedgerServiceTestCase):
    def test_ledger_state_loads_legacy_record_result_fields(self) -> None:
        now = main.now_iso()
        legacy_transport_url_key = "chain" + "M" + "cpUrl"
        legacy_state = {
            "accounts": [],
            "entries": [],
            "escrows": [],
            "onrampSessions": [],
            "onrampEvents": [],
            "legacySummary": {"ignored": True},
            "chainRecords": [
                {
                    "recordId": "chain_legacy",
                    "eventType": "credit",
                    "status": "submitted",
                    "chainTool": "chain_submit_execution",
                    legacy_transport_url_key: "http://chain.test/legacy/",
                    "recorderAddress": "0x000000000000000000000000000000000000dEaD",
                    "toolResult": {"txHash": "0xchain"},
                    "legacyExtra": "ignored",
                    "createdAt": now,
                    "updatedAt": now,
                },
                {
                    "recordId": "chain_missing_url",
                    "eventType": "credit",
                    "status": "failed",
                    "recorderAddress": "0x000000000000000000000000000000000000dEaD",
                    "createdAt": now,
                    "updatedAt": now,
                }
            ],
            "settlementRecords": [
                {
                    "recordId": "settle_legacy",
                    "eventType": "withdrawal",
                    "status": "submitted",
                    "settlementTool": "agent_wallet_withdraw",
                    legacy_transport_url_key: "http://circle.test/legacy/",
                    "fromAgentId": "agent_sender",
                    "amountAtomic": "1000000",
                    "toolResult": {"transactionHash": "0xsettle"},
                    "legacyExtra": "ignored",
                    "createdAt": now,
                    "updatedAt": now,
                },
                {
                    "recordId": "settle_missing_url",
                    "eventType": "withdrawal",
                    "status": "failed",
                    "fromAgentId": "agent_sender",
                    "amountAtomic": "1000000",
                    "createdAt": now,
                    "updatedAt": now,
                }
            ],
        }
        Path(self.state_path).write_text(json.dumps(legacy_state), encoding="utf-8")

        state = main.get_store().load()

        self.assertEqual(state.chainRecords[0].chainHttpUrl, "http://chain.test/legacy/")
        self.assertEqual(state.chainRecords[1].chainHttpUrl, main.DEFAULT_CHAIN_HTTP_URL)
        self.assertEqual(state.chainRecords[0].actionResult, {"txHash": "0xchain"})
        self.assertEqual(
            state.settlementRecords[0].settlementHttpUrl,
            "http://circle.test/legacy/",
        )
        self.assertEqual(
            state.settlementRecords[1].settlementHttpUrl,
            main.DEFAULT_SETTLEMENT_HTTP_URL,
        )
        self.assertEqual(
            state.settlementRecords[0].actionResult,
            {"transactionHash": "0xsettle"},
        )

    def test_route_payment_intent_is_served_by_rest(self) -> None:
        response = self.client.post(
            "/ledger/payment/route",
            json={
                "purpose": "buy async service",
                "deliveryMode": "async_task",
                "requiresAcceptance": True,
                "externalService": False,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["method"], "ledger_escrow")
        self.assertEqual(
            payload["allowedTools"],
            [
                "agent_wallet_create_escrow",
                "agent_wallet_release_escrow",
                "agent_wallet_refund_escrow",
            ],
        )

    def test_credit_creates_account_and_entry(self) -> None:
        response = self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )

        self.assertEqual(response.status_code, 200)
        account = response.json()["account"]
        self.assertEqual(account["agentId"], "agent_buyer")
        self.assertEqual(account["availableAtomic"], "5000000")
        self.assertEqual(account["lockedAtomic"], "0")

        state = self.client.get("/ledger/state?agentId=agent_buyer").json()
        self.assertEqual(len(state["entries"]), 1)
        self.assertEqual(state["entries"][0]["entryType"], "credit")

    def test_rejects_non_integer_amounts(self) -> None:
        response = self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "1.5", "reason": "bad funding"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "amountAtomic must be a positive integer string",
        )

    def test_credit_rejects_zero_amount(self) -> None:
        response = self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "0", "reason": "bad funding"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "amountAtomic must be a positive integer string",
        )

    def test_missing_escrow_returns_not_found(self) -> None:
        response = self.client.post("/ledger/escrows/escrow_missing/release")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["detail"], "escrow not found")

    def test_state_persists_to_sqlite_file(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        main.get_store.cache_clear()
        reloaded_client = TestClient(main.app)

        state = reloaded_client.get("/ledger/state?agentId=agent_buyer").json()

        self.assertEqual(state["accounts"][0]["agentId"], "agent_buyer")
        self.assertEqual(state["accounts"][0]["availableAtomic"], "5000000")
        self.assertTrue(Path(self.db_path).exists())

    def test_sqlite_store_imports_existing_json_once(self) -> None:
        now = main.now_iso()
        legacy_state = {
            "accounts": [
                {
                    "agentId": "agent_legacy",
                    "asset": "USDC",
                    "availableAtomic": "7000000",
                    "lockedAtomic": "0",
                    "createdAt": now,
                    "updatedAt": now,
                }
            ],
            "entries": [],
            "escrows": [],
            "onrampSessions": [],
            "onrampEvents": [],
            "circleWebhookEvents": [],
            "chainRecords": [],
            "settlementRecords": [],
        }
        Path(self.state_path).write_text(json.dumps(legacy_state), encoding="utf-8")

        state = main.get_store().load()
        self.assertEqual(state.accounts[0].agentId, "agent_legacy")
        self.assertEqual(state.accounts[0].availableAtomic, "7000000")

        main.get_store().credit(
            agent_id="agent_new",
            amount_atomic="1000000",
            reason="new funding",
            metadata={},
        )
        Path(self.state_path).write_text(json.dumps({"accounts": []}), encoding="utf-8")
        main.get_store.cache_clear()

        reloaded = main.get_store().load()
        self.assertEqual(
            {account.agentId for account in reloaded.accounts},
            {"agent_legacy", "agent_new"},
        )

    def test_sqlite_store_imports_existing_json_when_database_file_is_empty(self) -> None:
        now = main.now_iso()
        legacy_state = {
            "accounts": [
                {
                    "agentId": "agent_legacy_empty_db",
                    "asset": "USDC",
                    "availableAtomic": "3000000",
                    "lockedAtomic": "0",
                    "createdAt": now,
                    "updatedAt": now,
                }
            ],
            "entries": [],
            "escrows": [],
            "onrampSessions": [],
            "onrampEvents": [],
            "circleWebhookEvents": [],
            "chainRecords": [],
            "settlementRecords": [],
        }
        Path(self.state_path).write_text(json.dumps(legacy_state), encoding="utf-8")
        Path(self.db_path).touch()

        state = main.get_store().load()

        self.assertEqual(state.accounts[0].agentId, "agent_legacy_empty_db")
        self.assertEqual(state.accounts[0].availableAtomic, "3000000")

    def test_route_payment_intent_is_served_by_ledger_rest(self) -> None:
        response = self.client.post(
            "/ledger/payment/route",
            json={
                "purpose": "paid api",
                "deliveryMode": "immediate_api",
                "requiresAcceptance": False,
                "externalService": True,
                "serviceUrl": "https://seller.example/x402",
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["method"], "x402")
        self.assertEqual(result["allowedTools"], ["chain_x402_fetch"])

    def test_route_payment_intent_supports_funding_onramp(self) -> None:
        response = self.client.post(
            "/ledger/payment/route",
            json={
                "purpose": "fund agent wallet",
                "deliveryMode": "funding",
                "requiresAcceptance": False,
                "externalService": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["method"], "onramp")
        self.assertEqual(result["allowedTools"], ["agent_wallet_create_onramp_session"])

    def test_route_payment_intent_supports_direct_agent_transfer(self) -> None:
        response = self.client.post(
            "/ledger/payment/route",
            json={
                "purpose": "pay another agent now",
                "deliveryMode": "agent_transfer",
                "requiresAcceptance": False,
                "externalService": False,
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["method"], "gateway_nanopayment")
        self.assertEqual(result["allowedTools"], ["agent_wallet_transfer"])

    def test_route_payment_intent_supports_withdrawal(self) -> None:
        response = self.client.post(
            "/ledger/payment/route",
            json={
                "purpose": "withdraw USDC to an external wallet",
                "deliveryMode": "withdrawal",
                "requiresAcceptance": False,
                "externalService": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["method"], "gateway_withdrawal")
        self.assertEqual(result["allowedTools"], ["agent_wallet_settle_ledger_transfer"])

    def test_ledger_rest_routes_operate_on_local_store(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        escrow = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
                "taskId": "task_123",
                "description": "Research task",
            },
        ).json()["escrow"]
        released = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release").json()[
            "escrow"
        ]
        state = self.client.get("/admin/ledger/state").json()

        self.assertEqual(released["status"], "released")
        accounts = {item["agentId"]: item for item in state["accounts"]}
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "2000000")
        self.assertEqual(accounts["agent_buyer"]["lockedAtomic"], "0")
        self.assertEqual(accounts["agent_seller"]["availableAtomic"], "3000000")

        self.client.post(
            "/ledger/accounts/agent_refund_buyer/credit",
            json={"amountAtomic": "4000000", "reason": "demo funding"},
        )
        refund_escrow = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_refund_buyer",
                "sellerAgentId": "agent_refund_seller",
                "amountAtomic": "1000000",
            },
        ).json()["escrow"]
        refunded = self.client.post(
            f"/ledger/escrows/{refund_escrow['escrowId']}/refund"
        ).json()["escrow"]

        self.assertEqual(refunded["status"], "refunded")
