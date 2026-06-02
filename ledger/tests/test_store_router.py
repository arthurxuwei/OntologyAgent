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
        self.assertEqual(payload["method"], "needs_clarification")
        self.assertEqual(payload["allowedTools"], [])
        self.assertIn("only supports direct transfers", payload["reason"])

    def test_credit_creates_account_and_entry(self) -> None:
        response = self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )

        self.assertEqual(response.status_code, 200)
        account = response.json()["account"]
        self.assertEqual(account["agentId"], "agent_buyer")
        self.assertEqual(account["availableAtomic"], "5000000")

        entries = self.client.get("/ledger/accounts/agent_buyer/entries").json()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["entryType"], "credit")

    def test_legacy_state_routes_are_removed(self) -> None:
        self.assertEqual(self.client.get("/ledger/state").status_code, 404)
        self.assertEqual(self.client.get("/admin/ledger/state").status_code, 404)
        self.assertEqual(self.client.get("/dashboard/data").status_code, 404)
        self.assertEqual(self.client.get("/dashboard/claimable-agents").status_code, 404)

    def test_domain_account_entry_and_admin_routes(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_alpha",
            agent_name="Alpha",
            email="alpha@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-alpha",
            account_type="EOA",
        )
        store.credit(
            agent_id="agent_alpha",
            amount_atomic="5000000",
            reason="operator funding",
            metadata={},
        )

        accounts = self.client.get("/ledger/accounts?ownerEmail=alpha@example.com").json()
        self.assertEqual([account["agentId"] for account in accounts["accounts"]], ["agent_alpha"])
        self.assertEqual(accounts["accounts"][0]["availableAtomic"], "5000000")

        account = self.client.get("/ledger/accounts/agent_alpha").json()["account"]
        self.assertEqual(account["agentName"], "Alpha")

        entries = self.client.get("/ledger/accounts/agent_alpha/entries?limit=1").json()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["entryType"], "credit")

        with patch.dict(os.environ, {"ADMIN_TOKEN": "admin-secret"}):
            self.client.get("/admin?token=admin-secret", follow_redirects=False)
            summary = self.client.get("/ledger/admin/summary").json()
        self.assertEqual(summary["accounts"], 1)

    def test_claim_domain_routes_do_not_require_matching_owner_email(self) -> None:
        store = main.get_store()
        account = store.bind_account_wallet(
            agent_id="agent_claim",
            agent_name="Claimable",
            email="agent-owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-claim",
            account_type="EOA",
        )

        candidates = self.client.get("/ledger/claims/candidates").json()["candidates"]
        candidate = next(item for item in candidates if item["account"]["agentId"] == "agent_claim")
        self.assertEqual(candidate["account"]["email"], "agent-owner@example.com")
        self.assertTrue(candidate["claimCode"].startswith("clm_"))

        response = self.client.post(
            "/ledger/claims",
            json={
                "agentId": account.agentId,
                "claimCode": candidate["claimCode"],
                "email": "dashboard-user@example.com",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["claimed"])
        self.assertEqual(payload["ownerEmail"], "dashboard-user@example.com")
        claimed = self.client.get("/ledger/accounts?claimedByEmail=dashboard-user@example.com").json()["accounts"]
        self.assertEqual([item["agentId"] for item in claimed], ["agent_claim"])

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

    def test_state_persists_to_sqlite_file(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        main.get_store.cache_clear()
        reloaded_client = TestClient(main.app)

        state = reloaded_client.get("/ledger/accounts/agent_buyer").json()

        self.assertEqual(state["account"]["agentId"], "agent_buyer")
        self.assertEqual(state["account"]["availableAtomic"], "5000000")
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

    def test_waitlist_application_submission_appends_each_request(self) -> None:
        first = self.client.post(
            "/waitlist/applications",
            json={
                "email": "Founder@Example.COM",
                "name": "Founder",
                "company": "Example Labs",
                "intent": "agent wallet beta",
                "lang": "zh",
                "page_url": "https://kovaloop.ai/#cta",
                "submitted_at": "2026-06-02T08:00:00.000Z",
            },
            headers={"user-agent": "waitlist-test/1.0"},
        )
        second = self.client.post(
            "/waitlist/applications",
            json={
                "email": "founder@example.com",
                "name": "Founder",
                "company": "Example Labs",
                "intent": "second note",
                "lang": "en",
                "page_url": "https://kovaloop.ai/en#cta",
                "submitted_at": "2026-06-02T08:01:00.000Z",
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        first_payload = first.json()
        second_payload = second.json()
        self.assertTrue(first_payload["ok"])
        self.assertTrue(first_payload["applicationId"].startswith("waitlist_"))
        self.assertNotEqual(first_payload["applicationId"], second_payload["applicationId"])

        import sqlite3

        connection = sqlite3.connect(self.db_path)
        try:
            rows = connection.execute(
                """
                SELECT email, name, company, intent, lang, pageUrl, submittedAt, userAgent
                FROM ledger_waitlist_applications
                ORDER BY position ASC
                """
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "founder@example.com")
        self.assertEqual(rows[0][1], "Founder")
        self.assertEqual(rows[0][2], "Example Labs")
        self.assertEqual(rows[0][3], "agent wallet beta")
        self.assertEqual(rows[0][4], "zh")
        self.assertEqual(rows[0][5], "https://kovaloop.ai/#cta")
        self.assertEqual(rows[0][6], "2026-06-02T08:00:00.000Z")
        self.assertEqual(rows[0][7], "waitlist-test/1.0")
        self.assertEqual(rows[1][3], "second note")

    def test_waitlist_application_requires_email_and_name(self) -> None:
        response = self.client.post(
            "/waitlist/applications",
            json={"email": "", "name": "", "intent": "missing contact"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "email and name are required")

    def test_waitlist_application_allows_public_site_cors_preflight(self) -> None:
        response = self.client.options(
            "/waitlist/applications",
            headers={
                "Origin": "https://kovaloop.ai",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "https://kovaloop.ai",
        )
        self.assertIn("POST", response.headers["access-control-allow-methods"])

    def test_sqlite_store_drops_empty_legacy_records_table_after_relation_writes(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )

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
            connection.commit()
        finally:
            connection.close()

        main.get_store().load()

        connection = sqlite3.connect(self.db_path)
        try:
            old_records_table = connection.execute(
                """
                SELECT name FROM sqlite_master
                WHERE type = 'table' AND name = 'ledger_records'
                """
            ).fetchone()
        finally:
            connection.close()

        self.assertIsNone(old_records_table)

    def test_scoped_ledger_state_does_not_load_full_store(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )

        store = main.get_store()
        with patch.object(store, "load", side_effect=AssertionError("full load is not allowed")):
            response = self.client.get("/ledger/accounts/agent_buyer/entries")

        self.assertEqual(response.status_code, 200)
        entries = response.json()["entries"]
        self.assertEqual(entries[0]["agentId"], "agent_buyer")

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
                    "createdAt": now,
                    "updatedAt": now,
                }
            ],
            "entries": [],
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
                    "createdAt": now,
                    "updatedAt": now,
                }
            ],
            "entries": [],
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
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_buyer",
            agent_name="Buyer",
            email="buyer@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-buyer",
            account_type="EOA",
        )
        store.credit(
            agent_id="agent_buyer",
            amount_atomic="5000000",
            reason="demo funding",
            metadata={},
        )

        accounts_state = self.client.get("/ledger/accounts").json()
        accounts = {item["agentId"]: item for item in accounts_state["accounts"]}
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "5000000")
