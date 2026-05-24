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

    def test_sqlite_store_writes_records_to_relation_tables(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )

        import sqlite3

        connection = sqlite3.connect(self.db_path)
        try:
            account = connection.execute(
                "SELECT agentId, availableAtomic FROM ledger_accounts"
            ).fetchone()
            entry = connection.execute(
                "SELECT agentId, entryType, metadata FROM ledger_entries"
            ).fetchone()
            old_records_table = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'ledger_records'
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(account, ("agent_buyer", "5000000"))
        self.assertEqual(entry[0], "agent_buyer")
        self.assertEqual(entry[1], "credit")
        self.assertEqual(json.loads(entry[2]), {})
        self.assertIsNone(old_records_table)

    def test_scoped_ledger_state_does_not_load_full_store(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )

        store = main.get_store()
        with patch.object(store, "load", side_effect=AssertionError("full load is not allowed")):
            response = self.client.get("/ledger/state?agentId=agent_buyer")

        self.assertEqual(response.status_code, 200)
        state = response.json()
        self.assertEqual(state["accounts"][0]["agentId"], "agent_buyer")
        self.assertEqual(state["entries"][0]["agentId"], "agent_buyer")

    def test_wallet_and_webhook_lookup_do_not_load_full_store(self) -> None:
        store = main.get_store()
        account = store.bind_account_wallet(
            agent_id="agent_lookup",
            agent_name="Lookup Agent",
            email="lookup@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-lookup",
            account_type="EOA",
        )
        event = main.circle_webhook_event_record(
            notification_id="notification-lookup",
            notification_type="transactions.inbound",
            status="processed",
            payload={"notification": {"id": "tx-lookup"}},
            transaction_id="tx-lookup",
            agent_id=account.agentId,
            wallet_address=account.walletAddress,
            circle_wallet_id=account.circleWalletId,
            reason="test",
        )
        store.save_circle_webhook_event(event)

        with patch.object(store, "load", side_effect=AssertionError("full load is not allowed")):
            found_account = store.find_account_by_wallet(
                wallet_address=account.walletAddress,
                circle_wallet_id=None,
            )
            found_event = store.get_circle_webhook_event("notification-lookup")

        self.assertEqual(found_account.agentId, "agent_lookup")
        self.assertEqual(found_event.transactionId, "tx-lookup")

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

    def test_sqlite_store_migrates_legacy_payload_records_to_relation_tables(self) -> None:
        now = main.now_iso()

        import sqlite3

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                CREATE TABLE ledger_records (
                    collection TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (collection, record_id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO ledger_records(collection, record_id, position, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "accounts",
                    "USDC:agent_payload",
                    0,
                    json.dumps(
                        {
                            "agentId": "agent_payload",
                            "asset": "USDC",
                            "availableAtomic": "9000000",
                            "lockedAtomic": "0",
                            "createdAt": now,
                            "updatedAt": now,
                        }
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO ledger_records(collection, record_id, position, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "entries",
                    "entry_payload",
                    0,
                    json.dumps(
                        {
                            "entryId": "entry_payload",
                            "entryType": "credit",
                            "agentId": "agent_payload",
                            "asset": "USDC",
                            "availableDeltaAtomic": "9000000",
                            "lockedDeltaAtomic": "0",
                            "reason": "legacy funding",
                            "metadata": {"source": "legacy-records"},
                            "createdAt": now,
                        }
                    ),
                ),
            )
            connection.execute(
                """
                INSERT INTO ledger_records(collection, record_id, position, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "circle_webhook_events",
                    "notification_payload",
                    0,
                    json.dumps(
                        {
                            "notificationId": "notification_payload",
                            "notificationType": "transactions.inbound",
                            "status": "processed",
                            "transactionId": "tx_payload",
                            "gatewayDepositResult": {"mode": "gateway_deposit"},
                            "rawPayload": {"legacy": True},
                            "createdAt": now,
                            "updatedAt": now,
                        }
                    ),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        state = main.get_store().load()

        self.assertEqual(state.accounts[0].agentId, "agent_payload")
        self.assertEqual(state.accounts[0].availableAtomic, "9000000")
        self.assertEqual(state.entries[0].metadata, {"source": "legacy-records"})
        self.assertEqual(
            state.circleWebhookEvents[0].gatewayDepositResult,
            {"mode": "gateway_deposit"},
        )
        connection = sqlite3.connect(self.db_path)
        try:
            account = connection.execute(
                "SELECT agentId, availableAtomic FROM ledger_accounts"
            ).fetchone()
            entry = connection.execute(
                "SELECT metadata FROM ledger_entries WHERE entryId = 'entry_payload'"
            ).fetchone()
            circle_event = connection.execute(
                """
                SELECT gatewayDepositResult, rawPayload
                FROM ledger_circle_webhook_events
                WHERE notificationId = 'notification_payload'
                """
            ).fetchone()
            old_records_table = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'ledger_records'
                """
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(account, ("agent_payload", "9000000"))
        self.assertEqual(json.loads(entry[0]), {"source": "legacy-records"})
        self.assertEqual(json.loads(circle_event[0]), {"mode": "gateway_deposit"})
        self.assertEqual(json.loads(circle_event[1]), {"legacy": True})
        self.assertIsNone(old_records_table)

    def test_health_migrates_legacy_payload_records_to_relation_tables(self) -> None:
        now = main.now_iso()

        import sqlite3

        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                """
                CREATE TABLE ledger_records (
                    collection TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (collection, record_id)
                )
                """
            )
            connection.execute(
                """
                INSERT INTO ledger_records(collection, record_id, position, payload)
                VALUES (?, ?, ?, ?)
                """,
                (
                    "accounts",
                    "USDC:agent_health_migrate",
                    0,
                    json.dumps(
                        {
                            "agentId": "agent_health_migrate",
                            "asset": "USDC",
                            "availableAtomic": "4000000",
                            "lockedAtomic": "0",
                            "createdAt": now,
                            "updatedAt": now,
                        }
                    ),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        connection = sqlite3.connect(self.db_path)
        try:
            migrated = connection.execute(
                """
                SELECT agentId, availableAtomic
                FROM ledger_accounts
                WHERE agentId = 'agent_health_migrate'
                """
            ).fetchone()
            old_records_table = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'ledger_records'
                """
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(migrated, ("agent_health_migrate", "4000000"))
        self.assertIsNone(old_records_table)

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
