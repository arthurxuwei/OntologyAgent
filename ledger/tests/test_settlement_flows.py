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


class TestSettlementFlows(LedgerServiceTestCase):
    def claim_for_withdrawal(
        self,
        *,
        agent_id: str,
        account_email: str = "owner@example.com",
        dashboard_email: str = "owner@example.com",
    ) -> None:
        main.get_store().claim_dashboard_account(
            agent_id=agent_id,
            email=account_email,
            dashboard_email=dashboard_email,
        )

    def test_chain_recorder_posts_rest_execution(self) -> None:
        calls = []

        async def handler(request):
            calls.append(
                (
                    request.url.path,
                    request.headers.get("content-type"),
                    json.loads(request.content.decode("utf-8")),
                )
            )
            return httpx.Response(
                200,
                json={
                    "execution": {"txHash": "0xabc123", "mode": "mock"},
                    "settlement": {"kind": "submitted"},
                },
            )

        recorder = main.LedgerChainRecorder(
            enabled=True,
            chain_http_url="http://chain.test",
            recorder_address="0x000000000000000000000000000000000000dEaD",
            timeout_seconds=30,
            max_payload_bytes=2048,
            require_success=True,
            transport=httpx.MockTransport(handler),
        )

        entry = main.LedgerEntry(
            entryId="entry_1",
            entryType="credit",
            agentId="agentA",
            availableDeltaAtomic="100",
            createdAt=main.now_iso(),
        )
        record = asyncio.run(
            recorder.submit(
                event_type="credit",
                entries=[entry],
                payload={"eventType": "credit"},
            )
        )

        self.assertEqual(calls[0][0], "/chain/executions")
        self.assertEqual(calls[0][2]["to"], "0x000000000000000000000000000000000000dEaD")
        self.assertEqual(record.status, "submitted")
        self.assertEqual(record.txHash, "0xabc123")

    def test_withdrawal_settlement_client_uses_gateway_withdrawal_endpoint(self) -> None:
        calls = []

        async def handler(request):
            calls.append((request.url.path, json.loads(request.content.decode("utf-8"))))
            return httpx.Response(
                200,
                json={
                    "agentId": "agent_sender",
                    "recipientAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "1250000",
                    "mode": "gateway_withdraw",
                    "gatewayTransferId": "gateway-transfer-1",
                    "mintTransactionId": "mint-tx",
                    "mintTransactionHash": "0xmint",
                    "mintState": "CONFIRMED",
                    "transactionHash": "0xmint",
                },
            )

        client = main.LedgerSettlementClient(
            enabled=True,
            settlement_http_url="http://circle.test",
            timeout_seconds=30,
            require_success=False,
            transport=httpx.MockTransport(handler),
        )

        record = asyncio.run(
            client.submit_withdrawal(
                from_agent_id="agent_sender",
                to_address="0x2222222222222222222222222222222222222222",
                amount_atomic="1250000",
                ref_id="withdrawal:test",
            )
        )

        self.assertEqual(calls, [
            (
                "/circle/gateway/withdrawals",
                {
                    "agentId": "agent_sender",
                    "amountAtomic": "1250000",
                    "recipientAddress": "0x2222222222222222222222222222222222222222",
                    "refId": "withdrawal:test",
                },
            )
        ])
        self.assertEqual(record.mode, "gateway_withdraw")
        self.assertEqual(record.transactionId, "mint-tx")
        self.assertEqual(record.transactionHash, "0xmint")
        self.assertEqual(record.transactionState, "CONFIRMED")

    def test_agent_transfer_calls_circle_then_records_ledger_entries(self) -> None:
        class FakeSettlementClient:
            def __init__(self) -> None:
                self.calls = []

            async def submit_agent_transfer(
                self,
                *,
                from_agent_id,
                to_agent_id,
                amount_atomic,
                ref_id,
            ):
                self.calls.append(
                    {
                        "fromAgentId": from_agent_id,
                        "toAgentId": to_agent_id,
                        "amountAtomic": amount_atomic,
                        "refId": ref_id,
                    }
                )
                current = main.now_iso()
                return main.LedgerSettlementRecord(
                    recordId="settle_direct",
                    eventType="agent_transfer",
                    status="submitted",
                    settlementHttpUrl="http://circle.test",
                    transferId=ref_id,
                    fromAgentId=from_agent_id,
                    toAgentId=to_agent_id,
                    amountAtomic=amount_atomic,
                    transactionId="circle-transfer-1",
                    transactionHash="0xagenttransfer",
                    transactionState="INITIATED",
                    mode="circle",
                    actionResult={"transactionHash": "0xagenttransfer"},
                    createdAt=current,
                    updatedAt=current,
                )

        main.get_store().bind_account_wallet(
            agent_id="agent_sender",
            agent_name="Sender",
            email="sender@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle_sender",
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_receiver",
            agent_name="Receiver",
            email="receiver@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle_receiver",
        )
        fake_settlement = FakeSettlementClient()

        with patch.object(
            services, "get_ledger_settlement_client", return_value=fake_settlement
        ):
            response = self.client.post(
                "/ledger/transfers",
                json={
                    "fromAgentId": "agent_sender",
                    "toAgentId": "agent_receiver",
                    "amountAtomic": "10000",
                    "reason": "direct payment",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["settlementRecord"]["transactionHash"], "0xagenttransfer")
        self.assertEqual(fake_settlement.calls[0]["fromAgentId"], "agent_sender")
        self.assertEqual(fake_settlement.calls[0]["toAgentId"], "agent_receiver")
        accounts = {
            item.agentId: item for item in main.get_store().load().accounts
        }
        self.assertEqual(accounts["agent_sender"].availableAtomic, "0")
        self.assertEqual(accounts["agent_receiver"].availableAtomic, "0")
        self.assertEqual([entry["entryType"] for entry in payload["entries"]], ["agent_transfer", "agent_transfer"])

    def test_agent_transfer_rejects_single_payment_above_limit(self) -> None:
        class FakeSettlementClient:
            def __init__(self) -> None:
                self.calls = []

            async def submit_agent_transfer(self, **kwargs):
                self.calls.append(kwargs)

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_sender",
            agent_name="Sender",
            email="sender@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle_sender",
        )
        store.bind_account_wallet(
            agent_id="agent_receiver",
            agent_name="Receiver",
            email="receiver@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle_receiver",
        )
        fake_settlement = FakeSettlementClient()

        with patch.object(
            services, "get_ledger_settlement_client", return_value=fake_settlement
        ):
            response = self.client.post(
                "/ledger/transfers",
                json={
                    "fromAgentId": "agent_sender",
                    "toAgentId": "agent_receiver",
                    "amountAtomic": "10000001",
                    "reason": "direct payment",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "single transfer limit exceeded: max 10 USDC",
        )
        self.assertEqual(fake_settlement.calls, [])
        self.assertEqual(main.get_store().load().entries, [])

    def test_agent_transfer_rejects_email_address_contract(self) -> None:
        response = self.client.post(
            "/ledger/transfers",
            json={
                "fromEmail": "sender@example.com",
                "toEmail": "receiver@example.com",
                "amountAtomic": "1000",
                "reason": "legacy email transfer",
            },
        )

        self.assertEqual(response.status_code, 422)

    def test_agent_transfer_pending_batch_surfaces_as_settling_in_dashboard(self) -> None:
        class FakeSettlementClient:
            async def submit_agent_transfer(self, **kwargs):
                return main.LedgerSettlementRecord(
                    recordId="settle_gateway_batch",
                    eventType="agent_transfer",
                    status="submitted",
                    settlementHttpUrl="http://circle.test",
                    transferId=kwargs["ref_id"],
                    fromAgentId=kwargs["from_agent_id"],
                    toAgentId=kwargs["to_agent_id"],
                    amountAtomic=kwargs["amount_atomic"],
                    transactionId="0xgatewaysettlement",
                    transactionHash="0xgatewaysettlement",
                    transactionState="SETTLED",
                    mode="gateway",
                    actionResult={
                        "transactionHash": "0xgatewaysettlement",
                        "gatewayBalance": {
                            "pendingBatchAtomic": kwargs["amount_atomic"],
                            "formattedPendingBatch": "0.001",
                        },
                    },
                    createdAt=main.now_iso(),
                    updatedAt=main.now_iso(),
                )

        main.get_store().bind_account_wallet(
            agent_id="agent_sender",
            agent_name="Sender",
            email="sender@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle_sender",
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_receiver",
            agent_name="Receiver",
            email="receiver@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle_receiver",
        )

        with patch.object(services, "get_ledger_settlement_client", return_value=FakeSettlementClient()):
            response = self.client.post(
                "/ledger/transfers",
                json={
                    "fromAgentId": "agent_sender",
                    "toAgentId": "agent_receiver",
                    "amountAtomic": "1000",
                    "reason": "direct payment",
                },
            )

        self.assertEqual(response.status_code, 200)
        for entry in response.json()["entries"]:
            self.assertNotIn("dashboardStatus", entry["metadata"])
            self.assertNotIn("gatewayStage", entry["metadata"])
            self.assertNotIn("gatewayPendingBatchAtomic", entry["metadata"])

        ledger_state = main.get_store().load().model_dump()
        for account in ledger_state["accounts"]:
            if account["agentId"] == "agent_receiver":
                account["gatewayPendingBatchAtomic"] = "1000"

        receiver_dashboard = main.build_dashboard_data(
            ledger_state,
            owner_email="receiver@example.com",
        )
        receiver = receiver_dashboard["agents"]["agent_receiver"]
        self.assertEqual(receiver["balance"]["pendingSettlementAtomic"], "1000")
        self.assertEqual(receiver["transactions"][0]["status"], "pending_settle")
        self.assertNotIn("gatewayStage", receiver["transactions"][0])
        self.assertNotIn("gatewayPendingBatchAtomic", receiver["transactions"][0])

    def test_agent_transfer_settled_gateway_without_pending_batch_is_released(self) -> None:
        class FakeSettlementClient:
            async def submit_agent_transfer(self, **kwargs):
                return main.LedgerSettlementRecord(
                    recordId="settle_gateway_done",
                    eventType="agent_transfer",
                    status="submitted",
                    settlementHttpUrl="http://circle.test",
                    transferId=kwargs["ref_id"],
                    fromAgentId=kwargs["from_agent_id"],
                    toAgentId=kwargs["to_agent_id"],
                    amountAtomic=kwargs["amount_atomic"],
                    transactionId="0xgatewaydone",
                    transactionHash="0xgatewaydone",
                    transactionState="SETTLED",
                    mode="gateway",
                    actionResult={
                        "transactionHash": "0xgatewaydone",
                    },
                    createdAt=main.now_iso(),
                    updatedAt=main.now_iso(),
                )

        main.get_store().bind_account_wallet(
            agent_id="agent_sender",
            agent_name="Sender",
            email="sender@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle_sender",
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_receiver",
            agent_name="Receiver",
            email="receiver@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle_receiver",
        )

        with patch.object(services, "get_ledger_settlement_client", return_value=FakeSettlementClient()):
            response = self.client.post(
                "/ledger/transfers",
                json={
                    "fromAgentId": "agent_sender",
                    "toAgentId": "agent_receiver",
                    "amountAtomic": "1000",
                    "reason": "direct payment",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [entry["metadata"].get("dashboardStatus") for entry in response.json()["entries"]],
            [None, None],
        )
        sender_dashboard = main.build_dashboard_data(
            main.get_store().load().model_dump(),
            owner_email="sender@example.com",
        )
        sender = sender_dashboard["agents"]["agent_sender"]
        self.assertEqual(sender["balance"]["pendingSettlementAtomic"], "0")
        self.assertEqual(sender["transactions"][0]["status"], "released")

    def test_agent_transfer_failure_does_not_mutate_ledger_balance(self) -> None:
        class FakeSettlementClient:
            async def submit_agent_transfer(self, **_kwargs):
                current = main.now_iso()
                record = main.LedgerSettlementRecord(
                    recordId="settle_failed_direct",
                    eventType="agent_transfer",
                    status="failed",
                    settlementHttpUrl="http://circle.test",
                    transferId="transfer_failed",
                    fromAgentId="agent_sender",
                    toAgentId="agent_receiver",
                    amountAtomic="1000",
                    error="Circle transfer failed",
                    createdAt=current,
                    updatedAt=current,
                )
                raise main.LedgerSettlementError(record)

        self.client.post(
            "/ledger/accounts/agent_sender/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_sender",
            agent_name="Sender",
            email="sender@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle_sender",
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_receiver",
            agent_name="Receiver",
            email="receiver@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle_receiver",
        )

        with patch.object(
            services, "get_ledger_settlement_client", return_value=FakeSettlementClient()
        ):
            response = self.client.post(
                "/ledger/transfers",
                json={
                    "fromAgentId": "agent_sender",
                    "toAgentId": "agent_receiver",
                    "amountAtomic": "1000",
                },
            )

        self.assertEqual(response.status_code, 424)
        detail = response.json()["detail"]
        self.assertEqual(detail["message"], "Circle transfer failed")
        self.assertEqual(detail["settlementRecord"]["status"], "failed")
        self.assertEqual(detail["settlementRecord"]["error"], "Circle transfer failed")
        accounts = {
            item.agentId: item for item in main.get_store().load().accounts
        }
        self.assertEqual(accounts["agent_sender"].availableAtomic, "5000000")
        self.assertEqual(accounts["agent_receiver"].availableAtomic, "0")
        settlement_records = main.get_store().load().settlementRecords
        self.assertEqual(len(settlement_records), 1)
        self.assertEqual(settlement_records[0].status, "failed")
        self.assertEqual(settlement_records[0].error, "Circle transfer failed")

    def test_withdrawal_calls_circle_then_records_ledger_entry(self) -> None:
        class FakeSettlementClient:
            def __init__(self) -> None:
                self.calls = []

            async def submit_withdrawal(
                self,
                *,
                from_agent_id,
                to_address,
                amount_atomic,
                ref_id,
            ):
                self.calls.append(
                    {
                        "fromAgentId": from_agent_id,
                        "toAddress": to_address,
                        "amountAtomic": amount_atomic,
                        "refId": ref_id,
                    }
                )
                current = main.now_iso()
                return main.LedgerSettlementRecord(
                    recordId="settle_withdrawal",
                    eventType="withdrawal",
                    status="submitted",
                    settlementHttpUrl="http://circle.test",
                    transferId=ref_id,
                    fromAgentId=from_agent_id,
                    toAddress=to_address,
                    amountAtomic=amount_atomic,
                    transactionId="circle-withdrawal-1",
                    transactionHash="0xwithdrawal",
                    transactionState="INITIATED",
                    mode="circle",
                    actionResult={"transactionHash": "0xwithdrawal"},
                    createdAt=current,
                    updatedAt=current,
                )

        self.client.post(
            "/ledger/accounts/agent_sender/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_sender",
            agent_name="Sender",
            email="sender@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle_sender",
        )
        self.claim_for_withdrawal(
            agent_id="agent_sender",
            account_email="sender@example.com",
            dashboard_email="sender@example.com",
        )
        fake_settlement = FakeSettlementClient()

        with patch.object(
            services, "get_ledger_settlement_client", return_value=fake_settlement
        ):
            response = self.client.post(
                "/ledger/withdrawals",
                headers=self.dashboard_auth_headers("sender@example.com"),
                json={
                    "agentId": "agent_sender",
                    "ownerEmail": "sender@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "1250000",
                    "reason": "dashboard withdrawal",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["settlementRecord"]["transactionHash"], "0xwithdrawal")
        self.assertEqual(fake_settlement.calls[0]["fromAgentId"], "agent_sender")
        self.assertEqual(
            fake_settlement.calls[0]["toAddress"],
            "0x2222222222222222222222222222222222222222",
        )
        self.assertEqual(payload["account"]["availableAtomic"], "3750000")
        self.assertEqual(payload["entry"]["entryType"], "withdrawal")
        self.assertEqual(
            payload["entry"]["metadata"]["destinationAddress"],
            "0x2222222222222222222222222222222222222222",
        )
        self.assertTrue(payload["entry"]["metadata"]["counterparty"].startswith("External"))
        self.assertEqual(payload["route"]["method"], "gateway_withdrawal")

    def test_withdrawal_requires_dashboard_login(self) -> None:
        response = self.client.post(
            "/ledger/withdrawals",
            json={
                "agentId": "agent_sender",
                "ownerEmail": "sender@example.com",
                "destinationAddress": "0x2222222222222222222222222222222222222222",
                "amountAtomic": "1250000",
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Dashboard authentication required")

    def test_withdrawal_requires_claimed_agent_owner(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_unclaimed",
            agent_name="Unclaimed",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-unclaimed",
        )

        response = self.client.post(
            "/ledger/withdrawals",
            headers=self.dashboard_auth_headers("owner@example.com"),
            json={
                "agentId": "agent_unclaimed",
                "ownerEmail": "owner@example.com",
                "destinationAddress": "0x2222222222222222222222222222222222222222",
                "amountAtomic": "1250000",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "agent must be claimed before withdrawal")

    def test_withdrawal_rejects_non_owner_dashboard_user(self) -> None:
        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_claimed",
            agent_name="Claimed",
            email="agent-profile@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-claimed",
        )
        store.claim_dashboard_account(
            agent_id="agent_claimed",
            email="agent-profile@example.com",
            dashboard_email="owner@example.com",
        )

        response = self.client.post(
            "/ledger/withdrawals",
            headers=self.dashboard_auth_headers("attacker@example.com"),
            json={
                "agentId": "agent_claimed",
                "ownerEmail": "attacker@example.com",
                "destinationAddress": "0x2222222222222222222222222222222222222222",
                "amountAtomic": "1250000",
            },
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"], "dashboard user is not authorized for this agent")

    def test_withdrawal_records_submitted_and_withdrawn_entries(self) -> None:
        class FakeSettlementClient:
            async def submit_withdrawal(self, **kwargs):
                return main.LedgerSettlementRecord(
                    recordId="settle_withdrawal",
                    eventType="withdrawal",
                    settlementHttpUrl="http://settlement.test",
                    transferId=kwargs["ref_id"],
                    fromAgentId=kwargs["from_agent_id"],
                    toAddress=kwargs["to_address"],
                    asset="USDC",
                    amountAtomic=kwargs["amount_atomic"],
                    status="submitted",
                    transactionHash="0xwithdrawal",
                    actionResult={
                        "transactionHash": "0xwithdrawal",
                        "estimatedGasFeeAtomic": "3000",
                        "estimatedGasFee": "0.003",
                        "netAmountAtomic": "997000",
                        "netAmount": "0.997",
                    },
                    createdAt=main.now_iso(),
                    updatedAt=main.now_iso(),
                )

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_withdraw",
            agent_name="Withdraw Agent",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-withdraw",
        )
        self.claim_for_withdrawal(agent_id="agent_withdraw")
        store.credit(
            agent_id="agent_withdraw",
            amount_atomic="2000000",
            reason="seed",
            metadata={},
        )

        with patch.object(services, "get_ledger_settlement_client", return_value=FakeSettlementClient()):
            response = self.client.post(
                "/ledger/withdrawals",
                headers=self.dashboard_auth_headers("owner@example.com"),
                json={
                    "agentId": "agent_withdraw",
                    "ownerEmail": "owner@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "1000000",
                    "reason": "dashboard withdrawal",
                    "metadata": {"source": "dashboard"},
                },
            )

        self.assertEqual(response.status_code, 200)
        statuses = [
            entry["metadata"].get("dashboardStatus")
            for entry in response.json()["entries"]
        ]
        self.assertEqual(statuses, ["withdrawn"])
        self.assertEqual(len(main.get_store().load().entries), 2)
        withdrawal_entries = [
            entry for entry in main.get_store().load().entries
            if entry.entryType in {"withdrawal", "withdrawal_submitted"}
        ]
        self.assertEqual(len(withdrawal_entries), 1)
        self.assertEqual(withdrawal_entries[0].entryType, "withdrawal")
        self.assertEqual(withdrawal_entries[0].metadata["dashboardStatus"], "withdrawn")

    def test_withdrawal_uses_gateway_available_for_circle_backed_accounts(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                return {
                    "balances": {"USDC": "0"},
                    "gatewayBalance": {
                        "availableAtomic": "2000000",
                        "formattedAvailable": "2.0",
                    },
                }

        class FakeSettlementClient:
            def __init__(self) -> None:
                self.calls = []

            async def submit_withdrawal(self, **kwargs):
                self.calls.append(kwargs)
                return main.LedgerSettlementRecord(
                    recordId="settle_gateway_available_withdrawal",
                    eventType="withdrawal",
                    settlementHttpUrl="http://settlement.test",
                    transferId=kwargs["ref_id"],
                    fromAgentId=kwargs["from_agent_id"],
                    toAddress=kwargs["to_address"],
                    asset="USDC",
                    amountAtomic=kwargs["amount_atomic"],
                    status="submitted",
                    transactionHash="0xwithdrawal",
                    createdAt=main.now_iso(),
                    updatedAt=main.now_iso(),
                )

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_gateway_available",
            agent_name="Gateway Agent",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-gateway-available",
        )
        self.claim_for_withdrawal(agent_id="agent_gateway_available")
        fake_settlement = FakeSettlementClient()

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()), patch.object(
            services, "get_ledger_settlement_client", return_value=fake_settlement
        ):
            response = self.client.post(
                "/ledger/withdrawals",
                headers=self.dashboard_auth_headers("owner@example.com"),
                json={
                    "agentId": "agent_gateway_available",
                    "ownerEmail": "owner@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "1000000",
                    "reason": "dashboard withdrawal",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake_settlement.calls[0]["amount_atomic"], "1000000")
        self.assertEqual(response.json()["account"]["availableAtomic"], "0")

    def test_withdrawal_allows_kovaloop_owner_email_to_differ_from_agent_email(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                return {
                    "balances": {"USDC": "0"},
                    "gatewayBalance": {
                        "availableAtomic": "2000000",
                        "formattedAvailable": "2.0",
                    },
                }

        class FakeSettlementClient:
            def __init__(self) -> None:
                self.calls = []

            async def submit_withdrawal(self, **kwargs):
                self.calls.append(kwargs)
                return main.LedgerSettlementRecord(
                    recordId="settle_kovaloop_owner_withdrawal",
                    eventType="withdrawal",
                    settlementHttpUrl="http://settlement.test",
                    transferId=kwargs["ref_id"],
                    fromAgentId=kwargs["from_agent_id"],
                    toAddress=kwargs["to_address"],
                    asset="USDC",
                    amountAtomic=kwargs["amount_atomic"],
                    status="submitted",
                    transactionHash="0xwithdrawal",
                    createdAt=main.now_iso(),
                    updatedAt=main.now_iso(),
                )

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_agent_bound_email",
            agent_name="Agent Bound Email",
            email="agent-bound@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-agent-bound-email",
        )
        self.claim_for_withdrawal(
            agent_id="agent_agent_bound_email",
            account_email="agent-bound@example.com",
            dashboard_email="kovaloop-user@example.com",
        )
        fake_settlement = FakeSettlementClient()

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()), patch.object(
            services, "get_ledger_settlement_client", return_value=fake_settlement
        ):
            response = self.client.post(
                "/ledger/withdrawals",
                headers=self.dashboard_auth_headers("kovaloop-user@example.com"),
                json={
                    "agentId": "agent_agent_bound_email",
                    "ownerEmail": "kovaloop-user@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "1000000",
                    "reason": "dashboard withdrawal",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fake_settlement.calls[0]["from_agent_id"], "agent_agent_bound_email")

    def test_withdrawal_rejects_amount_above_gateway_available(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                return {
                    "balances": {"USDC": "0"},
                    "gatewayBalance": {
                        "availableAtomic": "500000",
                        "formattedAvailable": "0.5",
                    },
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_gateway_low",
            agent_name="Gateway Low",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-gateway-low",
        )
        self.claim_for_withdrawal(agent_id="agent_gateway_low")

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post(
                "/ledger/withdrawals",
                headers=self.dashboard_auth_headers("owner@example.com"),
                json={
                    "agentId": "agent_gateway_low",
                    "ownerEmail": "owner@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "1000000",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "amount exceeds Gateway available balance")

    def test_withdrawal_rejects_daily_limit(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                return {
                    "balances": {"USDC": "0"},
                    "gatewayBalance": {
                        "availableAtomic": "20000000",
                        "formattedAvailable": "20.0",
                    },
                }

        class FakeSettlementClient:
            def __init__(self) -> None:
                self.calls = []

            async def submit_withdrawal(self, **kwargs):
                self.calls.append(kwargs)

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_daily_limit",
            agent_name="Daily Limit",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-daily-limit",
        )
        self.claim_for_withdrawal(agent_id="agent_daily_limit")
        store.withdrawal_submitted(
            agent_id="agent_daily_limit",
            destination_address="0x2222222222222222222222222222222222222222",
            amount_atomic="4500001",
            reason="existing withdrawal",
            metadata={},
            withdrawal_id="withdrawal_existing_daily",
        )
        fake_settlement = FakeSettlementClient()

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()), patch.object(
            services, "get_ledger_settlement_client", return_value=fake_settlement
        ):
            response = self.client.post(
                "/ledger/withdrawals",
                headers=self.dashboard_auth_headers("owner@example.com"),
                json={
                    "agentId": "agent_daily_limit",
                    "ownerEmail": "owner@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "500000",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "withdrawal rejected by service risk policy",
        )
        self.assertEqual(fake_settlement.calls, [])
        withdrawal_entries = [
            entry
            for entry in main.get_store().load().entries
            if entry.entryType == "withdrawal_submitted"
        ]
        self.assertEqual(len(withdrawal_entries), 1)

    def test_withdrawal_rejects_weekly_limit(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                return {
                    "balances": {"USDC": "0"},
                    "gatewayBalance": {
                        "availableAtomic": "20000000",
                        "formattedAvailable": "20.0",
                    },
                }

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_weekly_limit",
            agent_name="Weekly Limit",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-weekly-limit",
        )
        self.claim_for_withdrawal(agent_id="agent_weekly_limit")
        with patch("store.now_iso", return_value="2026-05-25T00:00:00+00:00"):
            store.withdrawal_submitted(
                agent_id="agent_weekly_limit",
                destination_address="0x2222222222222222222222222222222222222222",
                amount_atomic="9500001",
                reason="existing withdrawal",
                metadata={},
                withdrawal_id="withdrawal_existing_weekly",
            )

        with (
            patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()),
            patch("store.now_iso", return_value="2026-05-31T00:00:00+00:00"),
        ):
            response = self.client.post(
                "/ledger/withdrawals",
                headers=self.dashboard_auth_headers("owner@example.com"),
                json={
                    "agentId": "agent_weekly_limit",
                    "ownerEmail": "owner@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "500000",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json()["detail"],
            "withdrawal rejected by service risk policy",
        )

    def test_failed_withdrawal_does_not_count_against_limits(self) -> None:
        class FakeWalletClient:
            async def status(self, *, wallet_address=None, circle_wallet_id=None):
                return {
                    "balances": {"USDC": "0"},
                    "gatewayBalance": {
                        "availableAtomic": "20000000",
                        "formattedAvailable": "20.0",
                    },
                }

        class FakeSettlementClient:
            async def submit_withdrawal(self, **kwargs):
                current = main.now_iso()
                return main.LedgerSettlementRecord(
                    recordId="settle_after_failed_withdrawal",
                    eventType="withdrawal",
                    settlementHttpUrl="http://settlement.test",
                    transferId=kwargs["ref_id"],
                    fromAgentId=kwargs["from_agent_id"],
                    toAddress=kwargs["to_address"],
                    amountAtomic=kwargs["amount_atomic"],
                    status="submitted",
                    transactionHash="0xwithdrawal",
                    createdAt=current,
                    updatedAt=current,
                )

        store = main.get_store()
        store.bind_account_wallet(
            agent_id="agent_failed_limit",
            agent_name="Failed Limit",
            email="owner@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle-failed-limit",
        )
        self.claim_for_withdrawal(agent_id="agent_failed_limit")
        failed_entry = store.withdrawal_submitted(
            agent_id="agent_failed_limit",
            destination_address="0x2222222222222222222222222222222222222222",
            amount_atomic="5000000",
            reason="failed withdrawal",
            metadata={},
            withdrawal_id="withdrawal_existing_failed",
        )
        store.withdrawal_failed(
            entry_id=failed_entry.entryId,
            agent_id="agent_failed_limit",
            destination_address="0x2222222222222222222222222222222222222222",
            amount_atomic="5000000",
            reason="withdrawal failed",
            metadata={},
            withdrawal_id="withdrawal_existing_failed",
            failure_reason="Circle withdrawal failed",
        )

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()), patch.object(
            services, "get_ledger_settlement_client", return_value=FakeSettlementClient()
        ):
            response = self.client.post(
                "/ledger/withdrawals",
                headers=self.dashboard_auth_headers("owner@example.com"),
                json={
                    "agentId": "agent_failed_limit",
                    "ownerEmail": "owner@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "1000000",
                },
            )

        self.assertEqual(response.status_code, 200)

    def test_withdrawal_failure_does_not_mutate_ledger_balance(self) -> None:
        class FakeSettlementClient:
            async def submit_withdrawal(self, **_kwargs):
                current = main.now_iso()
                record = main.LedgerSettlementRecord(
                    recordId="settle_failed_withdrawal",
                    eventType="withdrawal",
                    status="failed",
                    settlementHttpUrl="http://circle.test",
                    transferId="withdrawal_failed",
                    fromAgentId="agent_sender",
                    toAddress="0x2222222222222222222222222222222222222222",
                    amountAtomic="1250000",
                    error="Circle withdrawal failed",
                    createdAt=current,
                    updatedAt=current,
                )
                raise main.LedgerSettlementError(record)

        self.client.post(
            "/ledger/accounts/agent_sender/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_sender",
            agent_name="Sender",
            email="sender@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle_sender",
        )
        self.claim_for_withdrawal(
            agent_id="agent_sender",
            account_email="sender@example.com",
            dashboard_email="sender@example.com",
        )

        with patch.object(
            services, "get_ledger_settlement_client", return_value=FakeSettlementClient()
        ):
            response = self.client.post(
                "/ledger/withdrawals",
                headers=self.dashboard_auth_headers("sender@example.com"),
                json={
                    "agentId": "agent_sender",
                    "ownerEmail": "sender@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "1250000",
                },
            )

        self.assertEqual(response.status_code, 424)
        detail = response.json()["detail"]
        self.assertEqual(detail["message"], "Circle withdrawal failed")
        self.assertEqual(detail["settlementRecord"]["status"], "failed")
        account = main.get_store().load().accounts[0]
        self.assertEqual(account.availableAtomic, "5000000")
        state = main.get_store().load()
        settlement_records = state.settlementRecords
        self.assertEqual(len(settlement_records), 1)
        self.assertEqual(settlement_records[0].status, "failed")
        withdrawal_entries = [
            entry for entry in state.entries if entry.entryType == "withdrawal"
        ]
        self.assertEqual(withdrawal_entries, [])
        lifecycle_entries = [
            entry
            for entry in state.entries
            if entry.entryType == "withdrawal_submitted"
        ]
        self.assertEqual(len(lifecycle_entries), 1)
        failed_entry = lifecycle_entries[0]
        self.assertEqual(failed_entry.availableDeltaAtomic, "0")
        self.assertEqual(failed_entry.metadata["dashboardStatus"], "failed")
        self.assertNotIn("linkedEntryId", failed_entry.metadata)
        self.assertEqual(
            failed_entry.metadata["destinationAddress"],
            main.normalize_evm_address("0x2222222222222222222222222222222222222222"),
        )
        self.assertEqual(
            failed_entry.metadata["counterparty"],
            "External · 0x22222222...222222",
        )

    def test_agent_transfer_requires_real_circle_settlement_enabled(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_sender/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_sender",
            agent_name="Sender",
            email="sender@example.com",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id="circle_sender",
        )
        main.get_store().bind_account_wallet(
            agent_id="agent_receiver",
            agent_name="Receiver",
            email="receiver@example.com",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id="circle_receiver",
        )

        response = self.client.post(
            "/ledger/transfers",
            json={
                "fromAgentId": "agent_sender",
                "toAgentId": "agent_receiver",
                "amountAtomic": "1000",
            },
        )

        self.assertEqual(response.status_code, 424)
        self.assertEqual(
            response.json()["detail"]["message"],
            "Circle settlement is required for direct agent transfers",
        )
        accounts = {
            item.agentId: item for item in main.get_store().load().accounts
        }
        self.assertEqual(accounts["agent_sender"].availableAtomic, "5000000")
        self.assertEqual(accounts["agent_receiver"].availableAtomic, "0")

    def test_settlement_client_uses_dedicated_circle_http_url(self) -> None:
        os.environ["LEDGER_SETTLEMENT_ENABLED"] = "true"
        os.environ["LEDGER_SETTLEMENT_HTTP_URL"] = "http://circle.test"
        main.get_ledger_settlement_client.cache_clear()

        client = main.get_ledger_settlement_client()

        self.assertTrue(client.enabled)
        self.assertEqual(client.settlement_http_url, "http://circle.test")
