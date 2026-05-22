from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Callable
from typing import Any, Literal, Optional

from pydantic import BaseModel

from config import DEFAULT_ASSET, DEFAULT_CHAIN_HTTP_URL, DEFAULT_SETTLEMENT_HTTP_URL
from models import (
    CircleWebhookEventRecord,
    ConfirmOnrampSessionRequest,
    EscrowRecord,
    LedgerAccount,
    LedgerChainRecord,
    LedgerEntry,
    LedgerSettlementRecord,
    LedgerState,
    OnrampEventRecord,
    OnrampSessionRecord,
)
from utils import (
    add_atomic,
    normalize_email,
    normalize_evm_address,
    now_iso,
    parse_nonnegative_atomic,
    parse_positive_atomic,
    short_address,
)


def migrate_ledger_state_payload(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw

    def clean_model_record(record: Any, model_type: type[BaseModel]) -> Any:
        if not isinstance(record, dict):
            return record
        allowed_fields = set(model_type.model_fields)
        return {
            key: value
            for key, value in record.items()
            if key in allowed_fields
        }

    legacy_transport_url_key = "chain" + "M" + "cpUrl"
    for record in raw.get("chainRecords") or []:
        if not isinstance(record, dict):
            continue
        legacy_url = record.pop(legacy_transport_url_key, None)
        if "chainHttpUrl" not in record and legacy_url is not None:
            record["chainHttpUrl"] = legacy_url
        if "chainHttpUrl" not in record:
            record["chainHttpUrl"] = DEFAULT_CHAIN_HTTP_URL
        legacy_result = record.pop("toolResult", None)
        if "actionResult" not in record and legacy_result is not None:
            record["actionResult"] = legacy_result
        record.pop("chainTool", None)

    for record in raw.get("settlementRecords") or []:
        if not isinstance(record, dict):
            continue
        legacy_url = record.pop(legacy_transport_url_key, None)
        if "settlementHttpUrl" not in record and legacy_url is not None:
            record["settlementHttpUrl"] = legacy_url
        if "settlementHttpUrl" not in record:
            record["settlementHttpUrl"] = DEFAULT_SETTLEMENT_HTTP_URL
        legacy_result = record.pop("toolResult", None)
        if "actionResult" not in record and legacy_result is not None:
            record["actionResult"] = legacy_result
        record.pop("settlementTool", None)

    for collection_name, model_type in (
        ("accounts", LedgerAccount),
        ("entries", LedgerEntry),
        ("escrows", EscrowRecord),
        ("onrampSessions", OnrampSessionRecord),
        ("onrampEvents", OnrampEventRecord),
        ("circleWebhookEvents", CircleWebhookEventRecord),
        ("chainRecords", LedgerChainRecord),
        ("settlementRecords", LedgerSettlementRecord),
    ):
        collection = raw.get(collection_name)
        if isinstance(collection, list):
            raw[collection_name] = [
                clean_model_record(record, model_type)
                for record in collection
            ]

    allowed_state_fields = set(LedgerState.model_fields)
    for key in list(raw):
        if key not in allowed_state_fields:
            raw.pop(key, None)

    return raw


LedgerRecordId = Callable[[dict[str, Any], int], str]


def _account_record_id(record: dict[str, Any], _position: int) -> str:
    return f"{record.get('asset') or DEFAULT_ASSET}:{record.get('agentId')}"


def _field_record_id(field_name: str) -> LedgerRecordId:
    def record_id(record: dict[str, Any], position: int) -> str:
        value = record.get(field_name)
        if isinstance(value, str) and value:
            return value
        return f"missing-{field_name}-{position}"

    return record_id


LEDGER_COLLECTIONS: tuple[tuple[str, str, LedgerRecordId], ...] = (
    ("accounts", "accounts", _account_record_id),
    ("entries", "entries", _field_record_id("entryId")),
    ("escrows", "escrows", _field_record_id("escrowId")),
    ("onramp_sessions", "onrampSessions", _field_record_id("sessionId")),
    ("onramp_events", "onrampEvents", _field_record_id("eventId")),
    ("circle_webhook_events", "circleWebhookEvents", _field_record_id("notificationId")),
    ("chain_records", "chainRecords", _field_record_id("recordId")),
    ("settlement_records", "settlementRecords", _field_record_id("recordId")),
)


class OffchainLedgerStore:
    def __init__(self, path: str, *, legacy_json_path: Optional[str] = None) -> None:
        self.path = path
        self.legacy_json_path = legacy_json_path
        self._lock = threading.RLock()

    def load(self) -> LedgerState:
        with self._lock:
            return self._load_unlocked()

    def ensure_account(self, agent_id: str) -> LedgerAccount:
        def mutate(state: LedgerState) -> LedgerAccount:
            account, _ = self._account_for_update(state, agent_id, create=True)
            return account

        return self._mutate(mutate)

    def bind_account_wallet(
        self,
        *,
        agent_id: str,
        agent_name: Optional[str] = None,
        email: Optional[str] = None,
        wallet_address: Optional[str],
        circle_wallet_id: Optional[str],
        account_type: Optional[str] = None,
    ) -> LedgerAccount:
        def mutate(state: LedgerState) -> LedgerAccount:
            account, account_index = self._account_for_update(
                state, agent_id, create=True
            )
            updates: dict[str, Any] = {"updatedAt": now_iso()}
            if agent_name is not None:
                updates["agentName"] = agent_name
            if email is not None:
                updates["email"] = email
            if wallet_address is not None:
                updates["walletAddress"] = wallet_address
            if circle_wallet_id is not None:
                updates["circleWalletId"] = circle_wallet_id
            if account_type is not None:
                updates["accountType"] = account_type
            updated = account.model_copy(update=updates)
            state.accounts[account_index] = updated
            return updated

        return self._mutate(mutate)

    def find_account_by_wallet(
        self,
        *,
        wallet_address: Optional[str],
        circle_wallet_id: Optional[str],
    ) -> Optional[LedgerAccount]:
        normalized_wallet_address = (
            wallet_address.strip().lower()
            if isinstance(wallet_address, str) and wallet_address.strip()
            else None
        )
        normalized_circle_wallet_id = (
            circle_wallet_id.strip()
            if isinstance(circle_wallet_id, str) and circle_wallet_id.strip()
            else None
        )
        if normalized_wallet_address is None and normalized_circle_wallet_id is None:
            return None

        state = self.load()
        for account in state.accounts:
            if (
                normalized_circle_wallet_id is not None
                and account.circleWalletId == normalized_circle_wallet_id
            ):
                return account
            if (
                normalized_wallet_address is not None
                and isinstance(account.walletAddress, str)
                and account.walletAddress.lower() == normalized_wallet_address
            ):
                return account
        return None

    def get_circle_webhook_event(
        self,
        notification_id: str,
    ) -> Optional[CircleWebhookEventRecord]:
        state = self.load()
        for event in state.circleWebhookEvents:
            if event.notificationId == notification_id:
                return event
        return None

    def save_circle_webhook_event(
        self,
        event: CircleWebhookEventRecord,
    ) -> CircleWebhookEventRecord:
        def mutate(state: LedgerState) -> CircleWebhookEventRecord:
            for index, existing in enumerate(state.circleWebhookEvents):
                if existing.notificationId == event.notificationId:
                    state.circleWebhookEvents[index] = event
                    return event
            state.circleWebhookEvents.append(event)
            return event

        return self._mutate(mutate)

    def credit(
        self,
        *,
        agent_id: str,
        amount_atomic: str,
        reason: Optional[str],
        metadata: dict[str, Any],
    ) -> tuple[LedgerAccount, LedgerEntry]:
        amount = parse_positive_atomic(amount_atomic)

        def mutate(state: LedgerState) -> tuple[LedgerAccount, LedgerEntry]:
            account, account_index = self._account_for_update(state, agent_id, create=True)
            current = now_iso()
            updated = account.model_copy(
                update={
                    "availableAtomic": add_atomic(account.availableAtomic, amount),
                    "updatedAt": current,
                }
            )
            state.accounts[account_index] = updated
            entry = self._entry(
                entry_type="credit",
                agent_id=agent_id,
                available_delta=amount,
                reason=reason,
                metadata=metadata,
            )
            state.entries.append(entry)
            return updated, entry

        return self._mutate(mutate)

    def record_dashboard_event(
        self,
        *,
        entry_type: Literal[
            "pending_settlement",
            "pending_inbound",
            "withdrawal_submitted",
        ],
        agent_id: str,
        reason: Optional[str],
        metadata: dict[str, Any],
        escrow_id: Optional[str] = None,
    ) -> tuple[LedgerAccount, LedgerEntry]:
        def mutate(state: LedgerState) -> tuple[LedgerAccount, LedgerEntry]:
            account, account_index = self._account_for_update(state, agent_id, create=True)
            current = now_iso()
            updated = account.model_copy(update={"updatedAt": current})
            state.accounts[account_index] = updated
            entry = self._entry(
                entry_type=entry_type,
                agent_id=agent_id,
                escrow_id=escrow_id,
                reason=reason,
                metadata=metadata,
            )
            state.entries.append(entry)
            return updated, entry

        return self._mutate(mutate)

    def validate_agent_transfer(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        amount_atomic: str,
    ) -> None:
        parse_positive_atomic(amount_atomic)
        state = self.load()
        sender = self._find_account(state, from_agent_id)
        receiver = self._find_account(state, to_agent_id)
        if sender is None:
            raise ValueError("sender account not found")
        if receiver is None:
            raise ValueError("receiver account not found")
        self._require_circle_wallet(sender, "sender")
        self._require_circle_wallet(receiver, "receiver")

    def validate_withdrawal(
        self,
        *,
        agent_id: str,
        amount_atomic: str,
        owner_email: Optional[str],
        available_atomic: Optional[str] = None,
    ) -> LedgerAccount:
        amount = parse_positive_atomic(amount_atomic)
        state = self.load()
        account = self._find_account(state, agent_id)
        if account is None:
            raise ValueError("agent account not found")
        self._require_circle_wallet(account, "source")
        normalized_owner_email = normalize_email(owner_email)
        if normalized_owner_email and normalize_email(account.email) != normalized_owner_email:
            raise ValueError("ownerEmail does not match agent account")
        balance_basis = (
            parse_nonnegative_atomic(available_atomic)
            if available_atomic is not None
            else parse_nonnegative_atomic(account.availableAtomic)
        )
        if balance_basis < amount:
            raise ValueError("amount exceeds available balance")
        return account

    def account_by_email(self, email: str) -> LedgerAccount:
        normalized = normalize_email(email)
        if normalized is None:
            raise ValueError("email must not be empty")
        state = self.load()
        for account in state.accounts:
            if normalize_email(account.email) == normalized:
                return account
        raise LookupError(f"ledger account email not found: {normalized}")

    def transfer_between_agents(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        amount_atomic: str,
        reason: Optional[str],
        metadata: dict[str, Any],
        transfer_id: str,
        settlement_record_id: Optional[str],
    ) -> tuple[LedgerAccount, LedgerAccount, list[LedgerEntry]]:
        amount = parse_positive_atomic(amount_atomic)

        def mutate(state: LedgerState) -> tuple[LedgerAccount, LedgerAccount, list[LedgerEntry]]:
            sender, _sender_index = self._account_for_update(
                state, from_agent_id, create=False
            )
            receiver, _receiver_index = self._account_for_update(
                state, to_agent_id, create=False
            )
            self._require_circle_wallet(sender, "sender")
            self._require_circle_wallet(receiver, "receiver")

            entry_metadata = {
                **metadata,
                "transferId": transfer_id,
            }
            if settlement_record_id is not None:
                entry_metadata["settlementRecordId"] = settlement_record_id
            sender_entry = self._entry(
                entry_type="agent_transfer",
                agent_id=from_agent_id,
                available_delta=-amount,
                reason=reason or "agent transfer sent",
                metadata={**entry_metadata, "counterpartyAgentId": to_agent_id},
            )
            receiver_entry = self._entry(
                entry_type="agent_transfer",
                agent_id=to_agent_id,
                available_delta=amount,
                reason=reason or "agent transfer received",
                metadata={**entry_metadata, "counterpartyAgentId": from_agent_id},
            )
            state.entries.extend([sender_entry, receiver_entry])
            return sender, receiver, [sender_entry, receiver_entry]

        return self._mutate(mutate)

    def withdraw(
        self,
        *,
        agent_id: str,
        destination_address: str,
        amount_atomic: str,
        reason: Optional[str],
        metadata: dict[str, Any],
        withdrawal_id: str,
        settlement_record_id: Optional[str],
        available_atomic: Optional[str] = None,
    ) -> tuple[LedgerAccount, LedgerEntry]:
        amount = parse_positive_atomic(amount_atomic)
        destination = normalize_evm_address(destination_address)

        def mutate(state: LedgerState) -> tuple[LedgerAccount, LedgerEntry]:
            account, account_index = self._account_for_update(
                state, agent_id, create=False
            )
            self._require_circle_wallet(account, "source")
            balance_basis = (
                parse_nonnegative_atomic(available_atomic)
                if available_atomic is not None
                else parse_nonnegative_atomic(account.availableAtomic)
            )
            if balance_basis < amount:
                raise ValueError("amount exceeds available balance")
            current = now_iso()
            updated = account.model_copy(
                update={
                    "availableAtomic": str(balance_basis - amount),
                    "updatedAt": current,
                }
            )
            entry_metadata = {
                **metadata,
                "withdrawalId": withdrawal_id,
                "destinationAddress": destination,
                "counterparty": f"External · {short_address(destination)}",
            }
            if settlement_record_id is not None:
                entry_metadata["settlementRecordId"] = settlement_record_id
            entry = self._entry(
                entry_type="withdrawal",
                agent_id=agent_id,
                available_delta=-amount,
                reason=reason or "withdrawal",
                metadata=entry_metadata,
            )
            state.accounts[account_index] = updated
            state.entries.append(entry)
            return updated, entry

        return self._mutate(mutate)

    def withdrawal_submitted(
        self,
        *,
        agent_id: str,
        destination_address: str,
        amount_atomic: str,
        reason: Optional[str],
        metadata: dict[str, Any],
        withdrawal_id: str,
    ) -> LedgerEntry:
        amount = parse_positive_atomic(amount_atomic)
        destination = normalize_evm_address(destination_address)
        _account, entry = self.record_dashboard_event(
            entry_type="withdrawal_submitted",
            agent_id=agent_id,
            reason=reason or "withdrawal submitted",
            metadata={
                **metadata,
                "dashboardStatus": "withdraw_submitted",
                "amountAtomic": str(amount),
                "withdrawalId": withdrawal_id,
                "destinationAddress": destination,
                "counterparty": f"External · {short_address(destination)}",
                "network": "Base",
            },
        )
        return entry

    def create_escrow(
        self,
        *,
        buyer_agent_id: str,
        seller_agent_id: str,
        amount_atomic: str,
        task_id: Optional[str],
        description: Optional[str],
        metadata: dict[str, Any],
    ) -> tuple[EscrowRecord, LedgerEntry]:
        amount = parse_positive_atomic(amount_atomic)

        def mutate(state: LedgerState) -> tuple[EscrowRecord, LedgerEntry]:
            buyer, buyer_index = self._account_for_update(
                state, buyer_agent_id, create=False
            )
            if int(buyer.availableAtomic) < amount:
                raise ValueError("insufficient available balance")

            current = now_iso()
            updated_buyer = buyer.model_copy(
                update={
                    "availableAtomic": add_atomic(buyer.availableAtomic, -amount),
                    "lockedAtomic": add_atomic(buyer.lockedAtomic, amount),
                    "updatedAt": current,
                }
            )
            escrow = EscrowRecord(
                escrowId=f"escrow_{uuid.uuid4().hex}",
                buyerAgentId=buyer_agent_id,
                sellerAgentId=seller_agent_id,
                amountAtomic=str(amount),
                status="locked",
                taskId=task_id,
                description=description,
                createdAt=current,
                updatedAt=current,
            )
            entry = self._entry(
                entry_type="escrow_lock",
                agent_id=buyer_agent_id,
                available_delta=-amount,
                locked_delta=amount,
                escrow_id=escrow.escrowId,
                reason="escrow created",
                metadata=metadata,
            )
            state.accounts[buyer_index] = updated_buyer
            state.escrows.append(escrow)
            state.entries.append(entry)
            return escrow, entry

        return self._mutate(mutate)

    def release_escrow(self, escrow_id: str) -> EscrowRecord:
        def mutate(state: LedgerState) -> EscrowRecord:
            escrow, escrow_index = self._escrow_for_update(state, escrow_id)
            self._require_locked(escrow)
            amount = int(escrow.amountAtomic)
            buyer, buyer_index = self._account_for_update(
                state, escrow.buyerAgentId, create=False
            )
            seller, seller_index = self._account_for_update(
                state, escrow.sellerAgentId, create=True
            )
            current = now_iso()
            state.accounts[buyer_index] = buyer.model_copy(
                update={
                    "lockedAtomic": add_atomic(buyer.lockedAtomic, -amount),
                    "updatedAt": current,
                }
            )
            state.accounts[seller_index] = seller.model_copy(
                update={
                    "availableAtomic": add_atomic(seller.availableAtomic, amount),
                    "updatedAt": current,
                }
            )
            updated_escrow = escrow.model_copy(
                update={
                    "status": "released",
                    "releasedAt": current,
                    "updatedAt": current,
                }
            )
            state.escrows[escrow_index] = updated_escrow
            state.entries.append(
                self._entry(
                    entry_type="escrow_release",
                    agent_id=escrow.buyerAgentId,
                    locked_delta=-amount,
                    escrow_id=escrow.escrowId,
                    reason="escrow released",
                )
            )
            state.entries.append(
                self._entry(
                    entry_type="escrow_release",
                    agent_id=escrow.sellerAgentId,
                    available_delta=amount,
                    escrow_id=escrow.escrowId,
                    reason="escrow released",
                )
            )
            return updated_escrow

        return self._mutate(mutate)

    def refund_escrow(self, escrow_id: str) -> EscrowRecord:
        def mutate(state: LedgerState) -> EscrowRecord:
            escrow, escrow_index = self._escrow_for_update(state, escrow_id)
            self._require_locked(escrow)
            amount = int(escrow.amountAtomic)
            buyer, buyer_index = self._account_for_update(
                state, escrow.buyerAgentId, create=False
            )
            current = now_iso()
            state.accounts[buyer_index] = buyer.model_copy(
                update={
                    "availableAtomic": add_atomic(buyer.availableAtomic, amount),
                    "lockedAtomic": add_atomic(buyer.lockedAtomic, -amount),
                    "updatedAt": current,
                }
            )
            updated_escrow = escrow.model_copy(
                update={
                    "status": "refunded",
                    "refundedAt": current,
                    "updatedAt": current,
                }
            )
            state.escrows[escrow_index] = updated_escrow
            state.entries.append(
                self._entry(
                    entry_type="escrow_refund",
                    agent_id=escrow.buyerAgentId,
                    available_delta=amount,
                    locked_delta=-amount,
                    escrow_id=escrow.escrowId,
                    reason="escrow refunded",
                )
            )
            return updated_escrow

        return self._mutate(mutate)

    def entries_for_escrow_event(
        self,
        *,
        escrow_id: str,
        entry_type: Literal["escrow_lock", "escrow_release", "escrow_refund"],
    ) -> list[LedgerEntry]:
        state = self.load()
        return [
            entry
            for entry in state.entries
            if entry.escrowId == escrow_id and entry.entryType == entry_type
        ]

    def add_chain_record(self, record: LedgerChainRecord) -> LedgerChainRecord:
        def mutate(state: LedgerState) -> LedgerChainRecord:
            state.chainRecords.append(record)
            return record

        return self._mutate(mutate)

    def add_settlement_record(self, record: LedgerSettlementRecord) -> LedgerSettlementRecord:
        def mutate(state: LedgerState) -> LedgerSettlementRecord:
            state.settlementRecords.append(record)
            return record

        return self._mutate(mutate)

    def get_escrow(self, escrow_id: str) -> EscrowRecord:
        state = self.load()
        for escrow in state.escrows:
            if escrow.escrowId == escrow_id:
                return escrow
        raise LookupError("escrow not found")

    def find_onramp_session_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> Optional[OnrampSessionRecord]:
        state = self.load()
        for session in state.onrampSessions:
            if session.idempotencyKey == idempotency_key:
                return session
        return None

    def get_onramp_session(self, session_id: str) -> OnrampSessionRecord:
        state = self.load()
        for session in state.onrampSessions:
            if session.sessionId == session_id:
                return session
        raise LookupError("onramp session not found")

    def add_onramp_session(self, session: OnrampSessionRecord) -> OnrampSessionRecord:
        def mutate(state: LedgerState) -> OnrampSessionRecord:
            for existing in state.onrampSessions:
                if existing.idempotencyKey == session.idempotencyKey:
                    return existing
            state.onrampSessions.append(session)
            state.onrampEvents.append(
                OnrampEventRecord(
                    eventId=f"evt_{uuid.uuid4().hex}",
                    sessionId=session.sessionId,
                    eventType="session_created",
                    rawPayload={"idempotencyKey": session.idempotencyKey},
                    createdAt=session.createdAt,
                )
            )
            return session

        return self._mutate(mutate)

    def confirm_onramp_session(
        self,
        session_id: str,
        request: ConfirmOnrampSessionRequest,
    ) -> OnrampSessionRecord:
        amount = parse_positive_atomic(request.amountAtomic)

        def mutate(state: LedgerState) -> OnrampSessionRecord:
            session, session_index = self._onramp_session_for_update(state, session_id)
            if session.status == "credited":
                return session

            metadata = {
                "onrampSessionId": session.sessionId,
                "provider": "coinbase",
                "providerOrderId": request.providerOrderId,
                "destinationAddress": session.destinationAddress,
                "destinationNetwork": session.destinationNetwork,
                "asset": session.purchaseCurrency,
            }
            if request.txHash:
                metadata["txHash"] = request.txHash

            account, account_index = self._account_for_update(
                state, session.agentId, create=True
            )
            current = now_iso()
            updated_account = account.model_copy(
                update={
                    "availableAtomic": add_atomic(account.availableAtomic, amount),
                    "updatedAt": current,
                }
            )
            entry = self._entry(
                entry_type="credit",
                agent_id=session.agentId,
                available_delta=amount,
                reason="coinbase_onramp_confirmed",
                metadata=metadata,
            )
            updated_session = session.model_copy(
                update={
                    "status": "credited",
                    "providerOrderId": request.providerOrderId,
                    "creditedAmountAtomic": str(amount),
                    "txHash": request.txHash,
                    "ledgerEntryId": entry.entryId,
                    "creditedAt": current,
                    "updatedAt": current,
                }
            )
            state.accounts[account_index] = updated_account
            state.entries.append(entry)
            state.onrampSessions[session_index] = updated_session
            state.onrampEvents.append(
                OnrampEventRecord(
                    eventId=f"evt_{uuid.uuid4().hex}",
                    sessionId=session.sessionId,
                    eventType="ledger_credited",
                    providerEventId=request.providerEventId,
                    rawPayload=request.rawPayload,
                    createdAt=current,
                )
            )
            return updated_session

        return self._mutate(mutate)

    def _load_unlocked(self) -> LedgerState:
        connection = self._connect()
        try:
            self._ensure_schema(connection)
            self._maybe_import_legacy_json(connection)
            return self._load_state_from_db(connection)
        finally:
            connection.close()

    def _save_unlocked(self, state: LedgerState) -> None:
        parent_dir = os.path.dirname(self.path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        connection = self._connect()
        try:
            self._ensure_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._replace_state(connection, state)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        finally:
            connection.close()

    def _mutate(self, mutator):
        with self._lock:
            connection = self._connect()
            try:
                self._ensure_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                try:
                    self._maybe_import_legacy_json(connection)
                    state = self._load_state_from_db(connection)
                    result = mutator(state)
                    self._replace_state(connection, state)
                    connection.commit()
                    return result
                except Exception:
                    connection.rollback()
                    raise
            finally:
                connection.close()

    def _connect(self) -> sqlite3.Connection:
        parent_dir = os.path.dirname(self.path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _ensure_schema(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger_records (
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
            CREATE INDEX IF NOT EXISTS idx_ledger_records_collection_position
            ON ledger_records(collection, position)
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO ledger_meta(key, value)
            VALUES ('schema_version', '1')
            """
        )

    def _maybe_import_legacy_json(self, connection: sqlite3.Connection) -> None:
        if not self.legacy_json_path or not os.path.exists(self.legacy_json_path):
            return
        imported = connection.execute(
            "SELECT value FROM ledger_meta WHERE key = 'legacy_json_imported'"
        ).fetchone()
        if imported is not None:
            return
        record_count = connection.execute(
            "SELECT COUNT(*) AS count FROM ledger_records"
        ).fetchone()["count"]
        if record_count:
            return
        with open(self.legacy_json_path, encoding="utf-8") as handle:
            state = LedgerState.model_validate(
                migrate_ledger_state_payload(json.load(handle))
            )
        started_transaction = not connection.in_transaction
        if started_transaction:
            connection.execute("BEGIN IMMEDIATE")
        try:
            self._replace_state(connection, state)
            connection.execute(
                """
                INSERT OR REPLACE INTO ledger_meta(key, value)
                VALUES ('legacy_json_imported', ?)
                """,
                (self.legacy_json_path,),
            )
            if started_transaction:
                connection.commit()
        except Exception:
            if started_transaction:
                connection.rollback()
            raise

    def _load_state_from_db(self, connection: sqlite3.Connection) -> LedgerState:
        raw_state: dict[str, Any] = {}
        for sql_collection, state_field, _record_id in LEDGER_COLLECTIONS:
            rows = connection.execute(
                """
                SELECT payload FROM ledger_records
                WHERE collection = ?
                ORDER BY position ASC, record_id ASC
                """,
                (sql_collection,),
            ).fetchall()
            raw_state[state_field] = [json.loads(row["payload"]) for row in rows]
        return LedgerState.model_validate(migrate_ledger_state_payload(raw_state))

    def _replace_state(self, connection: sqlite3.Connection, state: LedgerState) -> None:
        payload = state.model_dump()
        connection.execute("DELETE FROM ledger_records")
        for sql_collection, state_field, record_id_for in LEDGER_COLLECTIONS:
            records = payload.get(state_field) or []
            used_record_ids: set[str] = set()
            for position, record in enumerate(records):
                if not isinstance(record, dict):
                    continue
                base_record_id = record_id_for(record, position)
                record_id = base_record_id
                if record_id in used_record_ids:
                    record_id = f"{base_record_id}#{position}"
                while record_id in used_record_ids:
                    record_id = f"{record_id}#duplicate"
                used_record_ids.add(record_id)
                connection.execute(
                    """
                    INSERT INTO ledger_records(collection, record_id, position, payload)
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        sql_collection,
                        record_id,
                        position,
                        json.dumps(record, sort_keys=True),
                    ),
                )
        connection.execute(
            """
            INSERT OR REPLACE INTO ledger_meta(key, value)
            VALUES ('updated_at', ?)
            """,
            (now_iso(),),
        )

    def _account_for_update(
        self, state: LedgerState, agent_id: str, *, create: bool
    ) -> tuple[LedgerAccount, int]:
        for index, account in enumerate(state.accounts):
            if account.agentId == agent_id and account.asset == DEFAULT_ASSET:
                return account, index
        if not create:
            raise ValueError("account not found")
        account = LedgerAccount(
            agentId=agent_id,
            createdAt=now_iso(),
            updatedAt=now_iso(),
        )
        state.accounts.append(account)
        return account, len(state.accounts) - 1

    @staticmethod
    def _find_account(state: LedgerState, agent_id: str) -> Optional[LedgerAccount]:
        for account in state.accounts:
            if account.agentId == agent_id and account.asset == DEFAULT_ASSET:
                return account
        return None

    @staticmethod
    def _require_circle_wallet(account: LedgerAccount, role: str) -> None:
        if not account.circleWalletId and not account.walletAddress:
            raise ValueError(f"{role} account is not bound to a Circle wallet")

    def _escrow_for_update(
        self, state: LedgerState, escrow_id: str
    ) -> tuple[EscrowRecord, int]:
        for index, escrow in enumerate(state.escrows):
            if escrow.escrowId == escrow_id:
                return escrow, index
        raise LookupError("escrow not found")

    def _onramp_session_for_update(
        self, state: LedgerState, session_id: str
    ) -> tuple[OnrampSessionRecord, int]:
        for index, session in enumerate(state.onrampSessions):
            if session.sessionId == session_id:
                return session, index
        raise LookupError("onramp session not found")

    @staticmethod
    def _require_locked(escrow: EscrowRecord) -> None:
        if escrow.status != "locked":
            raise ValueError("escrow is not locked")

    @staticmethod
    def _entry(
        *,
        entry_type: Literal[
            "credit",
            "escrow_lock",
            "escrow_release",
            "escrow_refund",
            "agent_transfer",
            "pending_settlement",
            "pending_inbound",
            "withdrawal_submitted",
        ],
        agent_id: str,
        available_delta: int = 0,
        locked_delta: int = 0,
        escrow_id: Optional[str] = None,
        reason: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> LedgerEntry:
        return LedgerEntry(
            entryId=f"entry_{uuid.uuid4().hex}",
            entryType=entry_type,
            agentId=agent_id,
            availableDeltaAtomic=str(available_delta),
            lockedDeltaAtomic=str(locked_delta),
            escrowId=escrow_id,
            reason=reason,
            metadata=metadata or {},
            createdAt=now_iso(),
        )
