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
                escrow=None,
                entries=[entry],
                payload={"eventType": "credit"},
            )
        )

        self.assertEqual(calls[0][0], "/chain/executions")
        self.assertEqual(calls[0][2]["to"], "0x000000000000000000000000000000000000dEaD")
        self.assertEqual(record.status, "submitted")
        self.assertEqual(record.txHash, "0xabc123")

    def test_create_escrow_moves_buyer_available_to_locked(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "5000000", "reason": "demo funding"},
        )

        response = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
                "taskId": "task_123",
                "description": "Research task",
            },
        )

        self.assertEqual(response.status_code, 200)
        escrow = response.json()["escrow"]
        self.assertEqual(escrow["status"], "locked")
        self.assertEqual(escrow["amountAtomic"], "3000000")

        accounts = {
            item["agentId"]: item for item in self.client.get("/ledger/state?agentId=agent_buyer").json()["accounts"]
        }
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "2000000")
        self.assertEqual(accounts["agent_buyer"]["lockedAtomic"], "3000000")

    def test_chain_record_is_persisted_for_escrow_lock_when_enabled(self) -> None:
        class FakeRecorder:
            enabled = True

            async def submit(self, *, event_type, escrow, entries, payload):
                current = main.now_iso()
                return main.LedgerChainRecord(
                    recordId="chainrec_test",
                    eventType=event_type,
                    status="submitted",
                    chainHttpUrl="http://chain.test",
                    recorderAddress="0x000000000000000000000000000000000000dEaD",
                    txHash="0xtesttx",
                    mode="mock",
                    escrowId=escrow.escrowId if escrow is not None else None,
                    entryIds=[entry.entryId for entry in entries],
                    payload=payload,
                    actionResult={
                        "execution": {
                            "txHash": "0xtesttx",
                            "mode": "mock",
                        }
                    },
                    createdAt=current,
                    updatedAt=current,
                )

        with patch.object(services, "get_chain_recorder", return_value=FakeRecorder()):
            self.client.post(
                "/ledger/accounts/agent_buyer/credit",
                json={"amountAtomic": "5000000", "reason": "demo funding"},
            )
            response = self.client.post(
                "/ledger/escrows",
                json={
                    "buyerAgentId": "agent_buyer",
                    "sellerAgentId": "agent_seller",
                    "amountAtomic": "3000000",
                    "taskId": "task_123",
                    "description": "Research task",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["chainRecord"]["txHash"], "0xtesttx")
        state = self.client.get("/ledger/state?agentId=agent_buyer").json()
        self.assertEqual(len(state["chainRecords"]), 2)
        lock_record = state["chainRecords"][1]
        self.assertEqual(lock_record["eventType"], "escrow_lock")
        self.assertEqual(lock_record["status"], "submitted")
        self.assertEqual(lock_record["payload"]["escrow"]["taskId"], "task_123")

    def test_create_escrow_rejects_insufficient_available_balance(self) -> None:
        self.client.post(
            "/ledger/accounts/agent_buyer/credit",
            json={"amountAtomic": "1000000", "reason": "demo funding"},
        )

        response = self.client.post(
            "/ledger/escrows",
            json={
                "buyerAgentId": "agent_buyer",
                "sellerAgentId": "agent_seller",
                "amountAtomic": "3000000",
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "insufficient available balance")
        state = self.client.get("/ledger/state?agentId=agent_buyer").json()
        self.assertEqual(state["escrows"], [])
        self.assertEqual(state["accounts"][0]["availableAtomic"], "1000000")
        self.assertEqual(state["accounts"][0]["lockedAtomic"], "0")

    def test_release_escrow_moves_locked_funds_to_seller_available(self) -> None:
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
            },
        ).json()["escrow"]

        response = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["escrow"]["status"], "released")
        accounts = {
            item["agentId"]: item for item in self.client.get("/ledger/state?agentId=agent_buyer").json()["accounts"]
        }
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "2000000")
        self.assertEqual(accounts["agent_buyer"]["lockedAtomic"], "0")
        seller_accounts = {
            item["agentId"]: item for item in self.client.get("/ledger/state?agentId=agent_seller").json()["accounts"]
        }
        self.assertEqual(seller_accounts["agent_seller"]["availableAtomic"], "3000000")

    def test_release_escrow_persists_settlement_record_when_enabled(self) -> None:
        class FakeSettlementClient:
            enabled = True

            async def submit_release(self, escrow):
                current = main.now_iso()
                return main.LedgerSettlementRecord(
                    recordId="settle_test",
                    eventType="escrow_release",
                    status="submitted",
                    settlementHttpUrl="http://circle.test",
                    escrowId=escrow.escrowId,
                    fromAgentId=escrow.buyerAgentId,
                    toAgentId=escrow.sellerAgentId,
                    amountAtomic=escrow.amountAtomic,
                    transactionId="circle-tx-1",
                    transactionHash="0xrealtransfer",
                    transactionState="INITIATED",
                    mode="circle",
                    actionResult={"transactionHash": "0xrealtransfer"},
                    createdAt=current,
                    updatedAt=current,
                )

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
            },
        ).json()["escrow"]

        with patch.object(
            services, "get_ledger_settlement_client", return_value=FakeSettlementClient()
        ):
            response = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["settlementRecord"]["transactionHash"], "0xrealtransfer")
        state = self.client.get("/ledger/state?agentId=agent_buyer").json()
        self.assertEqual(len(state["settlementRecords"]), 1)
        self.assertEqual(state["settlementRecords"][0]["status"], "submitted")

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
                    "fromEmail": "sender@example.com",
                    "toEmail": "receiver@example.com",
                    "amountAtomic": "1250000",
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
        self.assertEqual(accounts["agent_sender"].lockedAtomic, "0")
        self.assertEqual(accounts["agent_receiver"].availableAtomic, "0")
        self.assertEqual(accounts["agent_receiver"].lockedAtomic, "0")
        self.assertEqual([entry["entryType"] for entry in payload["entries"]], ["agent_transfer", "agent_transfer"])

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
                            "formattedPendingBatch": "1.25",
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
                    "fromEmail": "sender@example.com",
                    "toEmail": "receiver@example.com",
                    "amountAtomic": "1250000",
                    "reason": "direct payment",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [entry["metadata"].get("dashboardStatus") for entry in response.json()["entries"]],
            ["pending_settle", "pending_settle"],
        )
        sender_dashboard = self.client.get("/dashboard/data?email=sender@example.com").json()
        sender = sender_dashboard["agents"]["agent_sender"]
        self.assertEqual(sender["balance"]["pendingSettlementAtomic"], "1250000")
        self.assertEqual(sender["transactions"][0]["status"], "pending_settle")
        self.assertEqual(sender["transactions"][0]["gatewayStage"], "pending_batch")

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
                    amountAtomic="1250000",
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
                    "fromEmail": "sender@example.com",
                    "toEmail": "receiver@example.com",
                    "amountAtomic": "1250000",
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
        fake_settlement = FakeSettlementClient()

        with patch.object(
            services, "get_ledger_settlement_client", return_value=fake_settlement
        ):
            response = self.client.post(
                "/ledger/withdrawals",
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
        self.assertEqual(payload["route"]["method"], "circle_withdrawal")

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
        store.credit(
            agent_id="agent_withdraw",
            amount_atomic="2000000",
            reason="seed",
            metadata={},
        )

        with patch.object(services, "get_ledger_settlement_client", return_value=FakeSettlementClient()):
            response = self.client.post(
                "/ledger/withdrawals",
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
        self.assertEqual(statuses, ["withdraw_submitted", "withdrawn"])

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
        fake_settlement = FakeSettlementClient()

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()), patch.object(
            services, "get_ledger_settlement_client", return_value=fake_settlement
        ):
            response = self.client.post(
                "/ledger/withdrawals",
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

        with patch.object(services, "get_ledger_wallet_client", return_value=FakeWalletClient()):
            response = self.client.post(
                "/ledger/withdrawals",
                json={
                    "agentId": "agent_gateway_low",
                    "ownerEmail": "owner@example.com",
                    "destinationAddress": "0x2222222222222222222222222222222222222222",
                    "amountAtomic": "1000000",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "amount exceeds Gateway available balance")

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

        with patch.object(
            services, "get_ledger_settlement_client", return_value=FakeSettlementClient()
        ):
            response = self.client.post(
                "/ledger/withdrawals",
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
        self.assertEqual(len(lifecycle_entries), 2)
        submitted_entry, failed_entry = lifecycle_entries
        self.assertEqual(submitted_entry.availableDeltaAtomic, "0")
        self.assertEqual(failed_entry.availableDeltaAtomic, "0")
        self.assertEqual(
            failed_entry.metadata["linkedEntryId"],
            submitted_entry.entryId,
        )
        self.assertEqual(failed_entry.metadata["dashboardStatus"], "failed")
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
                "fromEmail": "sender@example.com",
                "toEmail": "receiver@example.com",
                "amountAtomic": "1250000",
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

    def test_required_settlement_failure_blocks_release(self) -> None:
        class FakeSettlementClient:
            enabled = True
            require_success = True

            async def submit_release(self, escrow):
                current = main.now_iso()
                record = main.LedgerSettlementRecord(
                    recordId="settle_failed",
                    eventType="escrow_release",
                    status="failed",
                    settlementHttpUrl="http://circle.test",
                    escrowId=escrow.escrowId,
                    fromAgentId=escrow.buyerAgentId,
                    toAgentId=escrow.sellerAgentId,
                    amountAtomic=escrow.amountAtomic,
                    error="Circle resource not found",
                    createdAt=current,
                    updatedAt=current,
                )
                raise main.LedgerSettlementError(record)

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
            },
        ).json()["escrow"]

        with patch.object(
            services, "get_ledger_settlement_client", return_value=FakeSettlementClient()
        ):
            response = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release")

        self.assertEqual(response.status_code, 424)
        state = self.client.get("/ledger/state?agentId=agent_buyer").json()
        escrow_state = state["escrows"][0]
        self.assertEqual(escrow_state["status"], "locked")

    def test_refund_escrow_moves_locked_funds_back_to_buyer_available(self) -> None:
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
            },
        ).json()["escrow"]

        response = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/refund")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["escrow"]["status"], "refunded")
        accounts = {
            item["agentId"]: item for item in self.client.get("/ledger/state?agentId=agent_buyer").json()["accounts"]
        }
        self.assertEqual(accounts["agent_buyer"]["availableAtomic"], "5000000")
        self.assertEqual(accounts["agent_buyer"]["lockedAtomic"], "0")
        self.assertNotIn("agent_seller", accounts)

    def test_settled_escrow_cannot_be_mutated_again(self) -> None:
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
            },
        ).json()["escrow"]
        self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release")

        refund = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/refund")
        release = self.client.post(f"/ledger/escrows/{escrow['escrowId']}/release")

        self.assertEqual(refund.status_code, 400)
        self.assertEqual(refund.json()["detail"], "escrow is not locked")
        self.assertEqual(release.status_code, 400)
        self.assertEqual(release.json()["detail"], "escrow is not locked")
