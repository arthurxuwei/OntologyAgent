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
from config import LEDGER_DASHBOARD_ASSETS_PATH
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from fastapi.testclient import TestClient
from helpers import LedgerServiceTestCase


class TestDashboardClaims(LedgerServiceTestCase):
    def dashboard_source(self, html: str) -> str:
        source = html
        if LEDGER_DASHBOARD_ASSETS_PATH.exists():
            source += "\n" + "\n".join(
                path.read_text()
                for path in sorted(LEDGER_DASHBOARD_ASSETS_PATH.rglob("*"))
                if path.is_file()
            )
        return source

    def test_dashboard_supports_claim_code_deep_link_without_auto_login_or_auto_claim(self) -> None:
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        source = self.dashboard_source(response.text)
        self.assertIn("function readClaimToken(params)", source)
        self.assertIn("params.get('claimCode') || params.get('claimcode') || params.get('claim_code') || params.get('code') || ''", source)
        self.assertIn("params.get('agentId')", source)
        self.assertIn("githubLoginHref", source)
        self.assertIn("returnTo=${encodeURIComponent(returnTo)}", source)
        self.assertNotIn("window.location.href = `/auth/github/login?returnTo=", source)
        self.assertNotIn("function DeepLinkClaimRunner()", source)
        self.assertNotIn("<DeepLinkClaimRunner />", source)
        self.assertIn("fetch('/ledger/claims/candidates'", source)
        self.assertNotIn("claimable-agents?claimed=${claimed}", source)
        self.assertIn(
            "const shouldOpenDeepLinkClaim = !!(claimToken && deepLinkAgentId && !claimedAgents.includes(deepLinkAgentId));",
            source,
        )
        self.assertIn("const [addAgentOpen, setAddAgentOpen] = React.useState(() => shouldOpenDeepLinkClaim);", source)
        self.assertIn("(mode === 'initial' || mode === 'add') && claimToken", source)
        self.assertIn("<DashboardRouter />", source)

    def test_dashboard_data_returns_email_scoped_ledger_accounts(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_alpha",
            agent_name="Alpha Research",
            email="Owner@Example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-alpha",
        )
        store.bind_account_wallet(
            agent_id="agent_beta",
            agent_name="Beta Research",
            email="other@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle-beta",
        )
        store.credit(
            agent_id="agent_alpha",
            amount_atomic="2500000",
            reason="operator funding",
            metadata={},
        )
        store.create_escrow(
            buyer_agent_id="agent_alpha",
            seller_agent_id="agent_beta",
            amount_atomic="500000",
            task_id="task_123",
            description="Research task",
            metadata={},
        )
        store.transfer_between_agents(
            from_agent_id="agent_alpha",
            to_agent_id="agent_beta",
            amount_atomic="250000",
            reason="remark should not render",
            metadata={
                "fromEmail": "owner@example.com",
                "toEmail": "other@example.com",
            },
            transfer_id="transfer_123",
            settlement_record_id=None,
        )

        payload = main.build_dashboard_data(
            main.get_store().load().model_dump(),
            owner_email="owner@example.com",
        )
        self.assertEqual(payload["source"], "ledger")
        self.assertEqual(payload["defaultAgentId"], "agent_alpha")
        self.assertEqual(set(payload["agents"].keys()), {"agent_alpha"})
        alpha = payload["agents"]["agent_alpha"]
        self.assertEqual(alpha["agent"]["name"], "Alpha Research")
        self.assertEqual(alpha["agent"]["ownerEmail"], "owner@example.com")
        self.assertEqual(
            alpha["agent"]["fullWalletAddress"],
            "0x1111111111111111111111111111111111111111",
        )
        self.assertEqual(alpha["balance"]["available"], 2.0)
        self.assertEqual(alpha["balance"]["locked"], 0.5)
        self.assertEqual(alpha["balance"]["lifetimeIn"], 2.5)
        self.assertEqual(alpha["balance"]["lifetimeOut"], 0.75)
        self.assertEqual(alpha["transactions"][0]["counterparty"], "other@example.com")
        self.assertNotEqual(alpha["transactions"][0]["counterparty"], "agent_beta")
        self.assertNotEqual(alpha["transactions"][0]["counterparty"], "remark should not render")
        self.assertEqual(alpha["transactions"][0]["status"], "released")

    def test_ledger_portfolio_endpoint_returns_claimed_domain_dashboard_state(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_alpha",
            agent_name="Alpha Research",
            email="agent-owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-alpha",
        )
        store.bind_account_wallet(
            agent_id="agent_beta",
            agent_name="Beta Research",
            email="other@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle-beta",
        )
        store.claim_dashboard_account(
            agent_id="agent_alpha",
            email="agent-owner@example.com",
            dashboard_email="dashboard-user@example.com",
        )
        store.credit(
            agent_id="agent_alpha",
            amount_atomic="250000",
            reason="operator funding",
            metadata={"dashboardStatus": "credited"},
        )

        response = self.client.get("/ledger/portfolio?ownerEmail=dashboard-user@example.com")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "ledger")
        self.assertEqual(payload["defaultAgentId"], "agent_alpha")
        self.assertEqual(set(payload["agents"].keys()), {"agent_alpha"})
        alpha = payload["agents"]["agent_alpha"]
        self.assertEqual(alpha["agent"]["name"], "Alpha Research")
        self.assertEqual(alpha["balance"]["lifetimeIn"], 0.25)
        self.assertEqual(alpha["transactions"][0]["status"], "credited")

    def test_dashboard_transaction_exposes_pending_settlement_and_gas_metadata(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="receiver",
            agent_name="Receiver Agent",
            email="receiver@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-receiver",
        )
        store.record_dashboard_event(
            entry_type="pending_settlement",
            agent_id="receiver",
            reason="nanopayment pending",
            metadata={
                "dashboardStatus": "pending_settle",
                "amountAtomic": "1000",
                "counterpartyEmail": "payer@example.com",
            },
        )
        store.record_dashboard_event(
            entry_type="withdrawal_submitted",
            agent_id="receiver",
            reason="withdrawal submitted",
            metadata={
                "dashboardStatus": "withdraw_submitted",
                "amountAtomic": "1000000",
                "gasFeeAtomic": "3000",
                "netAmountAtomic": "997000",
                "destinationAddress": "0x2222222222222222222222222222222222222222",
                "network": "Base",
                "txHash": "0xsubmitted",
            },
        )

        state = main.build_dashboard_data(
            main.get_store().load().model_dump(),
            owner_email="receiver@example.com",
        )

        data = state["agents"]["receiver"]
        self.assertEqual(data["balance"]["pendingSettlement"], 0.001)
        statuses = [tx["status"] for tx in data["transactions"]]
        self.assertIn("pending_settle", statuses)
        self.assertIn("withdraw_submitted", statuses)
        submitted = next(tx for tx in data["transactions"] if tx["status"] == "withdraw_submitted")
        self.assertEqual(submitted["direction"], "out")
        self.assertEqual(submitted["role"], "withdrawal")
        self.assertEqual(submitted["amountAtomic"], "1000000")
        self.assertEqual(submitted["gasFeeAtomic"], "3000")
        self.assertEqual(submitted["netAmountAtomic"], "997000")
        self.assertEqual(submitted["txHash"], "0xsubmitted")

    def test_dashboard_clears_settling_when_gateway_pending_batch_is_empty(self) -> None:
        state = main.build_dashboard_data(
            {
                "accounts": [
                    {
                        "agentId": "receiver",
                        "agentName": "Receiver Agent",
                        "email": "receiver@example.com",
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "availableAtomic": "1000000",
                        "lockedAtomic": "0",
                        "gatewayPendingBatchAtomic": "0",
                    }
                ],
                "entries": [
                    {
                        "entryId": "entry_settled_batch",
                        "entryType": "agent_transfer",
                        "agentId": "receiver",
                        "availableDeltaAtomic": "222000",
                        "lockedDeltaAtomic": "0",
                        "metadata": {
                            "dashboardStatus": "pending_settle",
                            "gatewayPendingBatchAtomic": "222000",
                            "gatewayStage": "pending_batch",
                            "transactionState": "SETTLED",
                            "txHash": "0xsettled",
                        },
                        "createdAt": main.now_iso(),
                    }
                ],
                "escrows": [],
            },
            owner_email="receiver@example.com",
        )

        data = state["agents"]["receiver"]
        self.assertEqual(data["balance"]["pendingSettlementAtomic"], "0")
        self.assertEqual(data["transactions"][0]["status"], "released")

    def test_dashboard_counts_only_current_gateway_pending_batch_as_settling(self) -> None:
        state = main.build_dashboard_data(
            {
                "accounts": [
                    {
                        "agentId": "receiver",
                        "agentName": "Receiver Agent",
                        "email": "receiver@example.com",
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "availableAtomic": "1000000",
                        "lockedAtomic": "0",
                        "gatewayPendingBatchAtomic": "500000",
                    }
                ],
                "entries": [
                    {
                        "entryId": "entry_old_settled_batch",
                        "entryType": "agent_transfer",
                        "agentId": "receiver",
                        "availableDeltaAtomic": "222000",
                        "lockedDeltaAtomic": "0",
                        "metadata": {
                            "dashboardStatus": "pending_settle",
                            "gatewayPendingBatchAtomic": "222000",
                            "gatewayStage": "pending_batch",
                            "transactionState": "SETTLED",
                            "txHash": "0xold",
                        },
                        "createdAt": "2026-05-22T07:24:34+00:00",
                    },
                    {
                        "entryId": "entry_current_settled_batch",
                        "entryType": "agent_transfer",
                        "agentId": "receiver",
                        "availableDeltaAtomic": "500000",
                        "lockedDeltaAtomic": "0",
                        "metadata": {
                            "dashboardStatus": "pending_settle",
                            "gatewayStage": "pending_batch",
                            "transactionState": "SETTLED",
                            "txHash": "0xcurrent",
                        },
                        "createdAt": "2026-05-22T14:17:27+00:00",
                    },
                    {
                        "entryId": "entry_outgoing_waiting_on_peer_batch",
                        "entryType": "agent_transfer",
                        "agentId": "receiver",
                        "availableDeltaAtomic": "-1100000",
                        "lockedDeltaAtomic": "0",
                        "metadata": {
                            "dashboardStatus": "pending_settle",
                            "gatewayStage": "pending_batch",
                            "transactionState": "SETTLED",
                            "txHash": "0xoutgoing",
                        },
                        "createdAt": "2026-05-22T14:21:48+00:00",
                    },
                ],
                "escrows": [],
            },
            owner_email="receiver@example.com",
        )

        data = state["agents"]["receiver"]
        self.assertEqual(data["balance"]["pendingSettlementAtomic"], "500000")
        txs_by_id = {tx["id"]: tx for tx in data["transactions"]}
        self.assertEqual(txs_by_id["entry_current_settled_batch"]["status"], "pending_settle")
        self.assertEqual(txs_by_id["entry_old_settled_batch"]["status"], "released")
        self.assertEqual(txs_by_id["entry_outgoing_waiting_on_peer_batch"]["status"], "released")

    def test_dashboard_withdrawal_lifecycle_rows_are_outgoing_withdrawals(self) -> None:
        submitted = main.dashboard_transaction(
            {
                "entryId": "entry_withdrawal_submitted",
                "entryType": "withdrawal_submitted",
                "agentId": "receiver",
                "availableDeltaAtomic": "0",
                "lockedDeltaAtomic": "0",
                "reason": "withdrawal submitted",
                "metadata": {
                    "dashboardStatus": "withdraw_submitted",
                    "amountAtomic": "1250000",
                    "counterparty": "External · 0x222222...222222",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                },
                "createdAt": main.now_iso(),
            },
            {},
        )
        failed = main.dashboard_transaction(
            {
                "entryId": "entry_withdrawal_failed",
                "entryType": "withdrawal_submitted",
                "agentId": "receiver",
                "availableDeltaAtomic": "0",
                "lockedDeltaAtomic": "0",
                "reason": "Circle withdrawal failed",
                "metadata": {
                    "dashboardStatus": "failed",
                    "amountAtomic": "1250000",
                    "counterparty": "External · 0x222222...222222",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "failureReason": "Circle withdrawal failed",
                },
                "createdAt": main.now_iso(),
            },
            {},
        )

        for tx in (submitted, failed):
            self.assertEqual(tx["direction"], "out")
            self.assertEqual(tx["role"], "withdrawal")
            self.assertEqual(tx["amountAtomic"], "1250000")
            self.assertEqual(tx["counterparty"], "External · 0x222222...222222")

    def test_dashboard_credited_gateway_rows_are_deposits(self) -> None:
        tx = main.dashboard_transaction(
            {
                "entryId": "entry_gateway_credit",
                "entryType": "credit",
                "agentId": "receiver",
                "availableDeltaAtomic": "2500000",
                "lockedDeltaAtomic": "0",
                "reason": "Gateway Wallet credited",
                "metadata": {
                    "dashboardStatus": "credited",
                    "amountAtomic": "2500000",
                    "counterparty": "External wallet",
                },
                "createdAt": main.now_iso(),
            },
            {},
        )

        self.assertEqual(tx["direction"], "in")
        self.assertEqual(tx["role"], "deposit")
        self.assertEqual(tx["status"], "credited")
        self.assertEqual(tx["amountAtomic"], "2500000")

    def test_dashboard_hides_pending_inbound_after_gateway_credit(self) -> None:
        state = main.build_dashboard_data(
            {
                "accounts": [
                    {
                        "agentId": "agent_topup",
                        "agentName": "Topup Agent",
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "availableAtomic": "1000000",
                    }
                ],
                "entries": [
                    {
                        "entryId": "entry_pending",
                        "entryType": "pending_inbound",
                        "agentId": "agent_topup",
                        "availableDeltaAtomic": "0",
                        "lockedDeltaAtomic": "0",
                        "createdAt": "2026-05-22T08:50:22+00:00",
                        "metadata": {
                            "dashboardStatus": "pending_inbound_chain",
                            "amountAtomic": "1000000",
                            "circleTransactionId": "circle-tx-1",
                        },
                    },
                    {
                        "entryId": "entry_credit",
                        "entryType": "credit",
                        "agentId": "agent_topup",
                        "availableDeltaAtomic": "1000000",
                        "lockedDeltaAtomic": "0",
                        "createdAt": "2026-05-22T08:50:56+00:00",
                        "metadata": {
                            "dashboardStatus": "credited",
                            "amountAtomic": "1000000",
                            "circleTransactionId": "circle-tx-1",
                            "linkedEntryId": "entry_pending",
                        },
                    },
                ],
                "escrows": [],
            }
        )

        txs = state["agents"]["agent_topup"]["transactions"]

        self.assertEqual([tx["id"] for tx in txs], ["entry_credit"])
        self.assertEqual(txs[0]["status"], "credited")

    def test_dashboard_keeps_gateway_deposit_crediting_while_circle_deposit_is_pending(self) -> None:
        state = main.build_dashboard_data(
            {
                "accounts": [
                    {
                        "agentId": "agent_topup",
                        "agentName": "Topup Agent",
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "circleUsdcBalance": "0",
                        "gatewayUsdcTotal": "16.202",
                        "gatewayTotalAtomic": "16202000",
                        "gatewayUsdcPendingDeposits": "2.1",
                        "gatewayPendingDepositsAtomic": "2100000",
                        "availableAtomic": "0",
                    }
                ],
                "entries": [
                    {
                        "entryId": "entry_pending",
                        "entryType": "pending_inbound",
                        "agentId": "agent_topup",
                        "availableDeltaAtomic": "0",
                        "lockedDeltaAtomic": "0",
                        "createdAt": "2026-05-22T15:08:31+00:00",
                        "metadata": {
                            "dashboardStatus": "pending_inbound_chain",
                            "amountAtomic": "2100000",
                            "circleTransactionId": "circle-tx-pending",
                        },
                    },
                    {
                        "entryId": "entry_credit",
                        "entryType": "credit",
                        "agentId": "agent_topup",
                        "availableDeltaAtomic": "2100000",
                        "lockedDeltaAtomic": "0",
                        "createdAt": "2026-05-22T15:09:05+00:00",
                        "metadata": {
                            "dashboardStatus": "credited",
                            "amountAtomic": "2100000",
                            "circleTransactionId": "circle-tx-pending",
                            "linkedEntryId": "entry_pending",
                        },
                    },
                ],
                "escrows": [],
            }
        )

        agent = state["agents"]["agent_topup"]
        txs = agent["transactions"]

        self.assertEqual(agent["balance"]["available"], 14.102)
        self.assertEqual([tx["id"] for tx in txs], ["entry_credit"])
        self.assertEqual(txs[0]["status"], "pending_inbound_chain")
        self.assertEqual(txs[0]["role"], "deposit")
        self.assertEqual(txs[0]["amountAtomic"], "2100000")

    def test_dashboard_withdrawal_status_ignores_blank_or_non_string_dashboard_status(self) -> None:
        for dashboard_status in ("", "   ", None, False):
            with self.subTest(dashboard_status=dashboard_status):
                tx = main.dashboard_transaction(
                    {
                        "entryId": "entry_withdrawal",
                        "entryType": "withdrawal",
                        "agentId": "receiver",
                        "availableDeltaAtomic": "-1000000",
                        "metadata": {"dashboardStatus": dashboard_status},
                        "createdAt": main.now_iso(),
                    },
                    {},
                )

                self.assertEqual(tx["status"], "withdrawn")

    def test_wallet_webhook_records_pending_inbound_before_gateway_credit(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "2.5"}}

            async def gateway_deposit(self, request):
                return {
                    "mode": "gateway_deposit",
                    "gatewayBalance": {"availableAtomic": request.amountAtomic},
                    "depositTransactionId": "deposit-tx",
                    "raw": {"provider": "secret"},
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_topup",
            agent_name="Topup Agent",
            email="topup@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-topup",
        )
        payload = {
            "notificationId": "notif_topup_1",
            "notificationType": "transactions.inbound",
            "notification": {
                "id": "circle-tx-1",
                "state": "COMPLETE",
                "transactionType": "INBOUND",
                "destinationAddress": "0x1111111111111111111111111111111111111111",
                "walletId": "circle-topup",
                "tokenSymbol": "USDC",
                "amount": "2.5",
            },
        }

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            result = asyncio.run(main.process_circle_wallet_webhook(payload))
            duplicate = asyncio.run(main.process_circle_wallet_webhook(payload))

        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["account"]["availableAtomic"], "2500000")
        self.assertEqual(duplicate["status"], "duplicate")
        entries = main.get_store().load().entries
        self.assertEqual(len(entries), 2)
        self.assertEqual(
            [entry.metadata.get("dashboardStatus") for entry in entries],
            ["pending_inbound_chain", "credited"],
        )
        pending_entry, credited_entry = entries
        self.assertEqual(pending_entry.entryType, "pending_inbound")
        self.assertEqual(pending_entry.availableDeltaAtomic, "0")
        self.assertEqual(pending_entry.metadata["amountAtomic"], "2500000")
        self.assertEqual(pending_entry.metadata["notificationId"], "notif_topup_1")
        self.assertEqual(pending_entry.metadata["circleTransactionId"], "circle-tx-1")
        self.assertEqual(
            pending_entry.metadata["gatewayRefId"],
            "circle-webhook:notif_topup_1",
        )
        self.assertEqual(credited_entry.entryType, "credit")
        self.assertEqual(credited_entry.availableDeltaAtomic, "2500000")
        self.assertEqual(credited_entry.metadata["amountAtomic"], "2500000")
        self.assertEqual(credited_entry.metadata["notificationId"], "notif_topup_1")
        self.assertEqual(credited_entry.metadata["circleTransactionId"], "circle-tx-1")
        self.assertEqual(
            credited_entry.metadata["gatewayRefId"],
            "circle-webhook:notif_topup_1",
        )
        self.assertEqual(
            credited_entry.metadata["linkedEntryId"],
            pending_entry.entryId,
        )
        self.assertEqual(credited_entry.metadata["depositTransactionId"], "deposit-tx")
        self.assertEqual(
            credited_entry.metadata["gatewayBalance"],
            {"availableAtomic": "2500000"},
        )
        self.assertNotIn("gatewayDepositResult", credited_entry.metadata)
        self.assertNotIn("raw", credited_entry.metadata)

    def test_wallet_webhook_received_replay_completes_missing_entries(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address, circle_wallet_id):
                return {"balances": {"USDC": "2.5"}}

            async def gateway_deposit(self, request):
                return {
                    "mode": "gateway_deposit",
                    "gatewayBalance": {"availableAtomic": request.amountAtomic},
                    "depositTransactionId": "deposit-recovered",
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_topup",
            agent_name="Topup Agent",
            email="topup@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-topup",
        )
        payload = {
            "notificationId": "notif_topup_received",
            "notificationType": "transactions.inbound",
            "notification": {
                "id": "circle-tx-received",
                "state": "COMPLETE",
                "transactionType": "INBOUND",
                "destinationAddress": "0x1111111111111111111111111111111111111111",
                "walletId": "circle-topup",
                "tokenSymbol": "USDC",
                "amount": "2.5",
            },
        }
        store.save_circle_webhook_event(
            main.circle_webhook_event_record(
                notification_id="notif_topup_received",
                notification_type="transactions.inbound",
                status="received",
                payload=payload,
                transaction_id="circle-tx-received",
                agent_id="agent_topup",
                wallet_address="0x1111111111111111111111111111111111111111",
                circle_wallet_id="circle-topup",
                amount_atomic="2500000",
                reason="gateway_deposit_started",
            )
        )

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            result = asyncio.run(main.process_circle_wallet_webhook(payload))

        self.assertEqual(result["status"], "processed")
        entries = main.get_store().load().entries
        self.assertEqual(
            [entry.metadata.get("dashboardStatus") for entry in entries],
            ["pending_inbound_chain", "credited"],
        )
        self.assertEqual(entries[1].metadata["linkedEntryId"], entries[0].entryId)
        self.assertEqual(entries[1].metadata["depositTransactionId"], "deposit-recovered")
        self.assertEqual(result["account"]["availableAtomic"], "2500000")

    def test_wallet_webhook_processed_replay_completes_missing_credit(self) -> None:
        class FakeWalletClient:
            async def gateway_deposit(self, _request):
                raise AssertionError("processed webhook replay must not call gateway_deposit")

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_topup",
            agent_name="Topup Agent",
            email="topup@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-topup",
        )
        payload = {
            "notificationId": "notif_topup_processed",
            "notificationType": "transactions.inbound",
            "notification": {
                "id": "circle-tx-processed",
                "state": "COMPLETE",
                "transactionType": "INBOUND",
                "destinationAddress": "0x1111111111111111111111111111111111111111",
                "walletId": "circle-topup",
                "tokenSymbol": "USDC",
                "amount": "2.5",
            },
        }
        store.save_circle_webhook_event(
            main.circle_webhook_event_record(
                notification_id="notif_topup_processed",
                notification_type="transactions.inbound",
                status="processed",
                payload=payload,
                transaction_id="circle-tx-processed",
                agent_id="agent_topup",
                wallet_address="0x1111111111111111111111111111111111111111",
                circle_wallet_id="circle-topup",
                amount_atomic="2500000",
                reason="gateway_deposit_completed",
                gateway_deposit_result={
                    "mode": "gateway_deposit",
                    "gatewayBalance": {"availableAtomic": "2500000"},
                    "depositTransactionId": "deposit-processed",
                    "raw": {"provider": "secret"},
                },
            )
        )

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            result = asyncio.run(main.process_circle_wallet_webhook(payload))
            duplicate = asyncio.run(main.process_circle_wallet_webhook(payload))

        self.assertEqual(result["status"], "processed")
        self.assertEqual(duplicate["status"], "duplicate")
        entries = main.get_store().load().entries
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].entryType, "pending_inbound")
        self.assertEqual(entries[1].entryType, "credit")
        self.assertEqual(entries[1].metadata["linkedEntryId"], entries[0].entryId)
        self.assertEqual(entries[1].metadata["depositTransactionId"], "deposit-processed")
        self.assertNotIn("raw", entries[1].metadata)
        self.assertNotIn("gatewayDepositResult", entries[1].metadata)
        self.assertEqual(result["account"]["availableAtomic"], "2500000")

    def test_dashboard_pending_settlement_balance_uses_escrow_amount_fallback(self) -> None:
        state = main.build_dashboard_data(
            {
                "accounts": [
                    {
                        "agentId": "receiver",
                        "agentName": "Receiver Agent",
                        "email": "receiver@example.com",
                        "walletAddress": "0x1111111111111111111111111111111111111111",
                        "availableAtomic": "0",
                        "lockedAtomic": "0",
                    }
                ],
                "entries": [
                    {
                        "entryId": "entry_pending",
                        "entryType": "pending_settlement",
                        "agentId": "receiver",
                        "availableDeltaAtomic": "0",
                        "lockedDeltaAtomic": "0",
                        "escrowId": "escrow_pending",
                        "metadata": {},
                        "createdAt": main.now_iso(),
                    }
                ],
                "escrows": [
                    {
                        "escrowId": "escrow_pending",
                        "buyerAgentId": "payer",
                        "sellerAgentId": "receiver",
                        "amountAtomic": "500000",
                        "description": "pending task",
                    }
                ],
            },
            owner_email="receiver@example.com",
        )

        data = state["agents"]["receiver"]
        self.assertEqual(data["transactions"][0]["amountAtomic"], "500000")
        self.assertEqual(data["balance"]["pendingSettlement"], 0.5)
        self.assertEqual(data["balance"]["pendingSettlementAtomic"], "500000")

    def test_dashboard_claimable_agents_come_from_unclaimed_eoa_accounts(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_alpha",
            agent_name="Alpha Research",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-alpha",
            account_type="EOA",
        )
        store.bind_account_wallet(
            agent_id="agent_beta",
            agent_name="Beta Research",
            email="owner@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle-beta",
            account_type="EOA",
        )
        store.bind_account_wallet(
            agent_id="agent_other",
            agent_name="Other Research",
            email="other@example.com",
            wallet_address="0x3333333333333333333333333333333333333333",
            circle_wallet_id="circle-other",
            account_type="EOA",
        )
        store.bind_account_wallet(
            agent_id="agent_sca",
            agent_name="SCA Research",
            email="owner@example.com",
            wallet_address="0x4444444444444444444444444444444444444444",
            circle_wallet_id="circle-sca",
            account_type="SCA",
        )
        store.credit(
            agent_id="agent_beta",
            amount_atomic="1250000",
            reason="operator funding",
            metadata={},
        )

        response = self.client.get("/ledger/claims/candidates")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            {agent["account"]["agentId"] for agent in payload["candidates"]},
            {"agent_alpha", "agent_beta", "agent_other"},
        )
        candidate = next(agent for agent in payload["candidates"] if agent["account"]["agentId"] == "agent_beta")
        self.assertEqual(candidate["account"]["agentId"], "agent_beta")
        self.assertEqual(candidate["account"]["agentName"], "Beta Research")
        self.assertEqual(candidate["account"]["email"], "owner@example.com")
        self.assertEqual(candidate["account"]["accountType"], "EOA")
        self.assertTrue(candidate["claimCode"].startswith("clm_"))
        self.assertNotEqual(candidate["claimCode"], "agent_beta")

    def test_claim_link_endpoint_creates_wallet_and_returns_urls(self) -> None:
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

        with patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://ledger.example.test"}), patch.object(
            services,
            "get_ledger_wallet_client",
            return_value=FakeWalletClient(),
        ):
            response = self.client.post(
                "/ledger/claims/link",
                json={
                    "agentId": "312586087945994240",
                    "agentName": "OpenClaw OntologyAgent",
                    "email": "OWNER@example.com",
                    "agentDescription": "OpenClaw profile bio",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], "312586087945994240")
        self.assertEqual(payload["agentName"], "OpenClaw OntologyAgent")
        self.assertEqual(payload["ownerEmail"], "owner@example.com")
        self.assertTrue(payload["claimCode"].startswith("clm_"))
        self.assertIn("claimCode=" + payload["claimCode"], payload["claimUrl"])
        self.assertIn("agentId=312586087945994240", payload["claimUrl"])
        self.assertEqual(
            payload["agentUrl"],
            "https://ledger.example.test/dashboard?agentId=312586087945994240",
        )
        self.assertEqual(payload["walletAddress"], "0x1111111111111111111111111111111111111111")
        self.assertEqual(payload["circleWalletId"], "circle-wallet-1")
        self.assertEqual(payload["accountType"], "EOA")

    def test_claim_link_endpoint_persists_claimable_account_without_wallet_ids(self) -> None:
        class FakeWalletClient:
            async def get_or_create(self, request):
                return {"mode": "mock"}

        with patch.dict(os.environ, {"PUBLIC_BASE_URL": "   "}), patch.object(
            services,
            "get_ledger_wallet_client",
            return_value=FakeWalletClient(),
        ):
            response = self.client.post(
                "/ledger/claims/link",
                json={
                    "agentId": "312586087945994240",
                    "agentName": "OpenClaw OntologyAgent",
                    "email": "OWNER@example.com",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], "312586087945994240")
        self.assertEqual(payload["agentName"], "OpenClaw OntologyAgent")
        self.assertEqual(payload["ownerEmail"], "owner@example.com")
        self.assertIn("claimCode=" + payload["claimCode"], payload["claimUrl"])
        self.assertEqual(
            payload["agentUrl"],
            "https://ledger.curawealth.ai/dashboard?agentId=312586087945994240",
        )

        account = main.get_store().load().accounts[0]
        self.assertEqual(account.agentId, "312586087945994240")
        self.assertEqual(account.agentName, "OpenClaw OntologyAgent")
        self.assertEqual(account.email, "owner@example.com")

        claimable = self.client.get("/ledger/claims/candidates").json()
        self.assertEqual(len(claimable["candidates"]), 1)
        self.assertEqual(claimable["candidates"][0]["account"]["agentId"], "312586087945994240")

    def test_dashboard_claim_endpoint_marks_manual_claims_for_session(self) -> None:
        store = main.get_store()
        account = store.bind_account_wallet(
            agent_id="agent_manual",
            agent_name="Manual Agent",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-manual",
            account_type="EOA",
        )
        claim_code = main.claim_code_for_account(account.model_dump(), "owner@example.com")

        response = self.client.post(
            "/ledger/claims",
            json={
                "agentId": "agent_manual",
                "claimCode": claim_code,
                "email": "DASHBOARD-USER@example.com",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], "agent_manual")
        self.assertTrue(payload["claimed"])
        claimed = main.get_store().load().accounts[0]
        self.assertIsNotNone(claimed.dashboardClaimedAt)
        self.assertEqual(claimed.dashboardClaimedByEmail, "dashboard-user@example.com")

        claimable = self.client.get("/ledger/claims/candidates").json()
        self.assertEqual(claimable["candidates"], [])

    def test_dashboard_claimable_agents_does_not_scope_by_dashboard_email(self) -> None:
        store = main.get_store()
        account = store.bind_account_wallet(
            agent_id="agent_cross_email",
            agent_name="Cross Email Agent",
            email="agent-owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-cross-email",
            account_type="EOA",
        )
        claim_code = main.claim_code_for_account(account.model_dump(), "agent-owner@example.com")

        response = self.client.get(
            "/ledger/claims/candidates"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["candidates"]), 1)
        self.assertEqual(payload["candidates"][0]["account"]["agentId"], "agent_cross_email")
        self.assertEqual(payload["candidates"][0]["claimCode"], claim_code)

    def test_dashboard_claim_endpoint_uses_signed_in_dashboard_email(self) -> None:
        store = main.get_store()
        account = store.bind_account_wallet(
            agent_id="agent_session_claim",
            agent_name="Session Claim Agent",
            email="agent-owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-session-claim",
            account_type="EOA",
        )
        claim_code = main.claim_code_for_account(account.model_dump(), "agent-owner@example.com")

        with patch.dict(os.environ, {"AUTH_SESSION_SECRET": "session-secret"}):
            session = main.sign_auth_session(
                {
                    "provider": "github",
                    "login": "dashboard-user",
                    "name": "Dashboard User",
                    "email": "dashboard-user@example.com",
                    "avatar_url": None,
                }
            )
            response = self.client.post(
                "/ledger/claims",
                json={
                    "agentId": "agent_session_claim",
                    "claimCode": claim_code,
                },
                headers={"Cookie": f"chief_ledger_session={session}"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["agentId"], "agent_session_claim")
        self.assertEqual(payload["ownerEmail"], "dashboard-user@example.com")
        claimed = main.get_store().load().accounts[0]
        self.assertEqual(claimed.dashboardClaimedByEmail, "dashboard-user@example.com")

    def test_claim_link_endpoint_rejects_non_eoa_wallets(self) -> None:
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
                "/ledger/claims/link",
                json={
                    "agentId": "312586087945994240",
                    "agentName": "OpenClaw OntologyAgent",
                    "email": "owner@example.com",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "claim wallet must be an EOA Circle wallet")
        self.assertEqual(self.ledger_domain_state()["accounts"], [])

    def test_claim_link_endpoint_requires_profile_identity(self) -> None:
        response = self.client.post(
            "/ledger/claims/link",
            json={
                "agentId": "",
                "agentName": "OpenClaw OntologyAgent",
                "email": "owner@example.com",
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_claim_link_endpoint_requires_email_via_route_logic(self) -> None:
        response = self.client.post(
            "/ledger/claims/link",
            json={
                "agentId": "312586087945994240",
                "agentName": "OpenClaw OntologyAgent",
                "email": "   ",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "email is required")

    def test_dashboard_claimable_agents_can_load_without_chief_email(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_eigenflux",
            agent_name="EigenFlux Worker",
            email="agent-bound@example.com",
            wallet_address="0x4444444444444444444444444444444444444444",
            circle_wallet_id="circle-eigenflux",
            account_type="EOA",
        )

        response = self.client.get("/ledger/claims/candidates")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["candidates"]), 1)
        candidate = payload["candidates"][0]
        self.assertEqual(candidate["account"]["agentId"], "agent_eigenflux")
        self.assertEqual(candidate["account"]["email"], "agent-bound@example.com")
        self.assertEqual(candidate["account"]["accountType"], "EOA")
        self.assertTrue(candidate["claimCode"].startswith("clm_"))

    def test_dashboard_claimable_agents_ignores_stale_client_claimed_ids(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_stale_local",
            agent_name="Stale Local Agent",
            email="agent-bound@example.com",
            wallet_address="0x4444444444444444444444444444444444444444",
            circle_wallet_id="circle-stale-local",
            account_type="EOA",
        )

        response = self.client.get("/ledger/claims/candidates")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            {agent["account"]["agentId"] for agent in payload["candidates"]},
            {"agent_stale_local"},
        )

    def test_dashboard_data_uses_wallet_and_gateway_total_as_available_balance(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                assert circle_wallet_id == "circle-wallet-1"
                return {
                    "balances": {"USDC": "1.98"},
                    "gatewayBalance": {
                        "formattedAvailable": "1.10",
                        "formattedTotal": "1.25",
                        "availableAtomic": "1100000",
                    },
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_research",
            agent_name="Research Agent",
            email="agent@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-wallet-1",
        )

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            accounts = asyncio.run(
                services.enriched_account_payloads(main.get_store().load().accounts)
            )
            dashboard = main.build_dashboard_data(
                {
                    **main.get_store().load().model_dump(),
                    "accounts": accounts,
                },
                owner_email="agent@example.com",
            )

        self.assertEqual(
            dashboard["agents"]["agent_research"]["balance"]["available"],
            3.23,
        )
        self.assertEqual(
            dashboard["agents"]["agent_research"]["balance"]["withdrawAvailable"],
            1.1,
        )
        self.assertEqual(
            dashboard["agents"]["agent_research"]["balance"]["withdrawAvailableAtomic"],
            "1100000",
        )
        self.assertEqual(main.get_store().load().accounts[0].availableAtomic, "0")

    def test_dashboard_frontend_reads_domain_portfolio_without_rebuilding_transactions(self) -> None:
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        source = self.dashboard_source(response.text)
        self.assertIn("fetch(`/ledger/portfolio?ownerEmail=${encodeURIComponent(ownerEmail)}`", source)
        self.assertNotIn("function buildDashboardAgent", source)
        self.assertNotIn("function dashboardAvailableAtomic", source)
        self.assertNotIn("function activeGatewayPendingEntryIds", source)
        self.assertNotIn("const entryStatus", source)
        self.assertNotIn("fetch(`/ledger/accounts?claimedByEmail=", source)
        self.assertNotIn("/entries?limit=100", source)
        self.assertNotIn("/escrows", source)

    def test_dashboard_root_waits_for_babel_global_dependencies_before_render(self) -> None:
        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 200)
        source = self.dashboard_source(response.text)
        self.assertIn("const REQUIRED_DASHBOARD_GLOBALS = [", source)
        self.assertIn("'LangProvider'", source)
        self.assertIn("'AppStateProvider'", source)
        self.assertIn("'LangSwitcher'", source)
        self.assertIn("'MvpGithubAuthScreen'", source)
        self.assertIn("'MvpDashboardChrome'", source)
        self.assertIn("function waitForDashboardDependencies", source)
        self.assertIn("await waitForDashboardDependencies();", source)
        self.assertIn("root.render(<DashboardRoot />);", source)
