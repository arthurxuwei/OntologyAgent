from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
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


def clean_model_record(record: Any, model_type: type[BaseModel]) -> Any:
    if not isinstance(record, dict):
        return record
    allowed_fields = set(model_type.model_fields)
    return {
        key: value
        for key, value in record.items()
        if key in allowed_fields
    }


def migrate_ledger_state_payload(raw: Any) -> Any:
    if not isinstance(raw, dict):
        return raw

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

AGENT_TRANSFER_SINGLE_LIMIT_ATOMIC = 1_000
WITHDRAWAL_DAILY_LIMIT_ATOMIC = 5_000_000
WITHDRAWAL_WEEKLY_LIMIT_ATOMIC = 10_000_000
WITHDRAWAL_DAILY_WINDOW = timedelta(hours=24)
WITHDRAWAL_WEEKLY_WINDOW = timedelta(days=7)


def _account_record_id(record: dict[str, Any], _position: int) -> str:
    return f"{record.get('asset') or DEFAULT_ASSET}:{record.get('agentId')}"


def _field_record_id(field_name: str) -> LedgerRecordId:
    def record_id(record: dict[str, Any], position: int) -> str:
        value = record.get(field_name)
        if isinstance(value, str) and value:
            return value
        return f"missing-{field_name}-{position}"

    return record_id


LedgerCollection = tuple[str, str, type[BaseModel], LedgerRecordId]


LEDGER_COLLECTIONS: tuple[LedgerCollection, ...] = (
    ("ledger_accounts", "accounts", LedgerAccount, _account_record_id),
    ("ledger_entries", "entries", LedgerEntry, _field_record_id("entryId")),
    ("ledger_escrows", "escrows", EscrowRecord, _field_record_id("escrowId")),
    (
        "ledger_onramp_sessions",
        "onrampSessions",
        OnrampSessionRecord,
        _field_record_id("sessionId"),
    ),
    (
        "ledger_onramp_events",
        "onrampEvents",
        OnrampEventRecord,
        _field_record_id("eventId"),
    ),
    (
        "ledger_circle_webhook_events",
        "circleWebhookEvents",
        CircleWebhookEventRecord,
        _field_record_id("notificationId"),
    ),
    ("ledger_chain_records", "chainRecords", LedgerChainRecord, _field_record_id("recordId")),
    (
        "ledger_settlement_records",
        "settlementRecords",
        LedgerSettlementRecord,
        _field_record_id("recordId"),
    ),
)

LEGACY_LEDGER_RECORD_COLLECTIONS: tuple[tuple[str, str, LedgerRecordId], ...] = (
    ("accounts", "accounts", _account_record_id),
    ("entries", "entries", _field_record_id("entryId")),
    ("escrows", "escrows", _field_record_id("escrowId")),
    ("onramp_sessions", "onrampSessions", _field_record_id("sessionId")),
    ("onramp_events", "onrampEvents", _field_record_id("eventId")),
    ("circle_webhook_events", "circleWebhookEvents", _field_record_id("notificationId")),
    ("chain_records", "chainRecords", _field_record_id("recordId")),
    ("settlement_records", "settlementRecords", _field_record_id("recordId")),
)


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def serialize_record_field(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def deserialize_record_field(value: Optional[str]) -> Any:
    if value is None:
        return None
    stripped = value.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def parse_ledger_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def withdrawal_limit_amount(entry: LedgerEntry) -> int:
    metadata_amount = entry.metadata.get("amountAtomic") if isinstance(entry.metadata, dict) else None
    if isinstance(metadata_amount, str):
        try:
            return parse_nonnegative_atomic(metadata_amount)
        except ValueError:
            pass
    try:
        return abs(int(entry.availableDeltaAtomic or "0"))
    except ValueError:
        return 0


def is_failed_withdrawal(entry: LedgerEntry) -> bool:
    return isinstance(entry.metadata, dict) and entry.metadata.get("dashboardStatus") == "failed"


def record_from_row(row: sqlite3.Row, model_type: type[BaseModel]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for field_name in model_type.model_fields:
        value = row[field_name]
        if value is not None:
            record[field_name] = deserialize_record_field(value)
    return record


def insert_record(
    connection: sqlite3.Connection,
    table_name: str,
    model_type: type[BaseModel],
    record_id: str,
    position: int,
    record: dict[str, Any],
) -> None:
    columns = ["record_id", "position", *model_type.model_fields]
    placeholders = ", ".join("?" for _ in columns)
    values = [
        record_id,
        position,
        *[serialize_record_field(record.get(field_name)) for field_name in model_type.model_fields],
    ]
    connection.execute(
        f"""
        INSERT INTO {quote_identifier(table_name)}
        ({", ".join(quote_identifier(column) for column in columns)})
        VALUES ({placeholders})
        """,
        values,
    )


def record_to_model(record: dict[str, Any], model_type: type[BaseModel]) -> BaseModel:
    return model_type.model_validate(clean_model_record(record, model_type))


class OffchainLedgerStore:
    def __init__(self, path: str, *, legacy_json_path: Optional[str] = None) -> None:
        self.path = path
        self.legacy_json_path = legacy_json_path
        self._lock = threading.RLock()

    def _read(self, operation):
        with self._lock:
            connection = self._connect()
            try:
                self._ensure_schema(connection)
                self._maybe_import_legacy_json(connection)
                return operation(connection)
            finally:
                connection.close()

    def _write(self, operation):
        with self._lock:
            connection = self._connect()
            try:
                self._ensure_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                try:
                    self._maybe_import_legacy_json(connection)
                    result = operation(connection)
                    connection.execute(
                        """
                        INSERT OR REPLACE INTO ledger_meta(key, value)
                        VALUES ('updated_at', ?)
                        """,
                        (now_iso(),),
                    )
                    connection.commit()
                    return result
                except Exception:
                    connection.rollback()
                    raise
            finally:
                connection.close()

    def _next_position(self, connection: sqlite3.Connection, table_name: str) -> int:
        row = connection.execute(
            f"SELECT COALESCE(MAX(position), -1) + 1 AS position FROM {quote_identifier(table_name)}"
        ).fetchone()
        return int(row["position"])

    def _get_record_by_id(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        model_type: type[BaseModel],
        record_id: str,
    ) -> Optional[BaseModel]:
        row = connection.execute(
            f"SELECT * FROM {quote_identifier(table_name)} WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            return None
        return record_to_model(record_from_row(row, model_type), model_type)

    def _list_records(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        model_type: type[BaseModel],
        where: str = "",
        params: tuple[Any, ...] = (),
        order_by: str = "position ASC, record_id ASC",
    ) -> list[BaseModel]:
        sql = f"SELECT * FROM {quote_identifier(table_name)}"
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY {order_by}"
        return [
            record_to_model(record_from_row(row, model_type), model_type)
            for row in connection.execute(sql, params).fetchall()
        ]

    def _upsert_record(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        model_type: type[BaseModel],
        record_id: str,
        record: BaseModel | dict[str, Any],
    ) -> BaseModel:
        payload = record.model_dump() if isinstance(record, BaseModel) else record
        existing = connection.execute(
            f"SELECT position FROM {quote_identifier(table_name)} WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        position = (
            int(existing["position"])
            if existing is not None
            else self._next_position(connection, table_name)
        )
        connection.execute(
            f"DELETE FROM {quote_identifier(table_name)} WHERE record_id = ?",
            (record_id,),
        )
        insert_record(connection, table_name, model_type, record_id, position, payload)
        return record_to_model(payload, model_type)

    def _append_record(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        model_type: type[BaseModel],
        base_record_id: str,
        record: BaseModel | dict[str, Any],
    ) -> BaseModel:
        payload = record.model_dump() if isinstance(record, BaseModel) else record
        position = self._next_position(connection, table_name)
        record_id = base_record_id
        existing_ids = {
            row["record_id"]
            for row in connection.execute(
                f"SELECT record_id FROM {quote_identifier(table_name)}"
            ).fetchall()
        }
        if record_id in existing_ids:
            record_id = f"{base_record_id}#{position}"
        while record_id in existing_ids:
            record_id = f"{record_id}#duplicate"
        insert_record(connection, table_name, model_type, record_id, position, payload)
        return record_to_model(payload, model_type)

    def _account_record_id_for_agent(self, agent_id: str) -> str:
        return f"{DEFAULT_ASSET}:{agent_id}"

    def _get_account(
        self,
        connection: sqlite3.Connection,
        agent_id: str,
        *,
        create: bool,
    ) -> LedgerAccount:
        record_id = self._account_record_id_for_agent(agent_id)
        account = self._get_record_by_id(
            connection,
            "ledger_accounts",
            LedgerAccount,
            record_id,
        )
        if isinstance(account, LedgerAccount):
            return account
        if not create:
            raise ValueError("account not found")
        current = now_iso()
        account = LedgerAccount(agentId=agent_id, createdAt=current, updatedAt=current)
        return self._save_account(connection, account)

    def _find_account_row(
        self,
        connection: sqlite3.Connection,
        agent_id: str,
    ) -> Optional[LedgerAccount]:
        account = self._get_record_by_id(
            connection,
            "ledger_accounts",
            LedgerAccount,
            self._account_record_id_for_agent(agent_id),
        )
        return account if isinstance(account, LedgerAccount) else None

    def _save_account(
        self,
        connection: sqlite3.Connection,
        account: LedgerAccount,
    ) -> LedgerAccount:
        return self._upsert_record(
            connection,
            "ledger_accounts",
            LedgerAccount,
            self._account_record_id_for_agent(account.agentId),
            account,
        )

    def _save_entry(self, connection: sqlite3.Connection, entry: LedgerEntry) -> LedgerEntry:
        return self._upsert_record(
            connection,
            "ledger_entries",
            LedgerEntry,
            entry.entryId,
            entry,
        )

    def _save_escrow(self, connection: sqlite3.Connection, escrow: EscrowRecord) -> EscrowRecord:
        return self._upsert_record(
            connection,
            "ledger_escrows",
            EscrowRecord,
            escrow.escrowId,
            escrow,
        )

    def _save_onramp_session(
        self,
        connection: sqlite3.Connection,
        session: OnrampSessionRecord,
    ) -> OnrampSessionRecord:
        return self._upsert_record(
            connection,
            "ledger_onramp_sessions",
            OnrampSessionRecord,
            session.sessionId,
            session,
        )

    def _save_onramp_event(
        self,
        connection: sqlite3.Connection,
        event: OnrampEventRecord,
    ) -> OnrampEventRecord:
        return self._upsert_record(
            connection,
            "ledger_onramp_events",
            OnrampEventRecord,
            event.eventId,
            event,
        )

    def load(self) -> LedgerState:
        return self._read(self._load_state_from_db)

    def list_accounts(
        self,
        *,
        owner_email: Optional[str] = None,
        claimed_by_email: Optional[str] = None,
        claimable: bool = False,
    ) -> list[LedgerAccount]:
        normalized_owner = normalize_email(owner_email)
        normalized_claimed_by = normalize_email(claimed_by_email)

        def read(connection: sqlite3.Connection) -> list[LedgerAccount]:
            clauses: list[str] = []
            params: list[Any] = []
            if normalized_owner is not None:
                clauses.append('LOWER("email") = ?')
                params.append(normalized_owner)
            if normalized_claimed_by is not None:
                clauses.append('LOWER(COALESCE("dashboardClaimedByEmail", "email", "")) = ?')
                params.append(normalized_claimed_by)
                clauses.append('"dashboardClaimedAt" IS NOT NULL')
            if claimable:
                clauses.append('"dashboardClaimedAt" IS NULL')
                clauses.append(
                    '('
                    'COALESCE("walletAddress", "circleWalletId", "") = "" '
                    'OR UPPER(COALESCE("accountType", "")) IN ("", "EOA")'
                    ')'
                )
            return self._list_records(
                connection,
                "ledger_accounts",
                LedgerAccount,
                " AND ".join(clauses),
                tuple(params),
                order_by='"updatedAt" DESC, position DESC, record_id DESC',
            )

        return self._read(read)

    def load_for_agent(self, agent_id: str) -> LedgerState:
        scoped_agent_id = str(agent_id or "").strip()
        if not scoped_agent_id:
            return LedgerState()

        def read(connection: sqlite3.Connection) -> LedgerState:
            accounts = self._list_records(
                connection,
                "ledger_accounts",
                LedgerAccount,
                '"agentId" = ?',
                (scoped_agent_id,),
            )
            entries = self._list_records(
                connection,
                "ledger_entries",
                LedgerEntry,
                '"agentId" = ?',
                (scoped_agent_id,),
            )
            entry_ids = {entry.entryId for entry in entries}
            escrows = self._list_records(
                connection,
                "ledger_escrows",
                EscrowRecord,
                '"buyerAgentId" = ? OR "sellerAgentId" = ?',
                (scoped_agent_id, scoped_agent_id),
            )
            escrow_ids = {escrow.escrowId for escrow in escrows}
            onramp_sessions = self._list_records(
                connection,
                "ledger_onramp_sessions",
                OnrampSessionRecord,
                '"agentId" = ?',
                (scoped_agent_id,),
            )
            session_ids = {session.sessionId for session in onramp_sessions}
            onramp_events: list[OnrampEventRecord] = []
            if session_ids:
                placeholders = ", ".join("?" for _ in session_ids)
                onramp_events = self._list_records(
                    connection,
                    "ledger_onramp_events",
                    OnrampEventRecord,
                    f'"sessionId" IN ({placeholders})',
                    tuple(sorted(session_ids)),
                )
            circle_events = self._list_records(
                connection,
                "ledger_circle_webhook_events",
                CircleWebhookEventRecord,
                '"agentId" = ?',
                (scoped_agent_id,),
            )
            chain_records = [
                record
                for record in self._list_records(connection, "ledger_chain_records", LedgerChainRecord)
                if (
                    (record.escrowId and record.escrowId in escrow_ids)
                    or any(entry_id in entry_ids for entry_id in record.entryIds)
                )
            ]
            settlement_records = self._list_records(
                connection,
                "ledger_settlement_records",
                LedgerSettlementRecord,
                '"fromAgentId" = ? OR "toAgentId" = ?',
                (scoped_agent_id, scoped_agent_id),
            )
            if escrow_ids:
                placeholders = ", ".join("?" for _ in escrow_ids)
                escrow_settlement_records = self._list_records(
                    connection,
                    "ledger_settlement_records",
                    LedgerSettlementRecord,
                    f'"escrowId" IN ({placeholders})',
                    tuple(sorted(escrow_ids)),
                )
                by_id = {record.recordId: record for record in settlement_records}
                for record in escrow_settlement_records:
                    by_id.setdefault(record.recordId, record)
                settlement_records = list(by_id.values())
            return LedgerState(
                accounts=accounts,
                entries=entries,
                escrows=escrows,
                onrampSessions=onramp_sessions,
                onrampEvents=onramp_events,
                circleWebhookEvents=circle_events,
                chainRecords=chain_records,
                settlementRecords=settlement_records,
            )

        return self._read(read)

    def list_entries(
        self,
        *,
        agent_id: Optional[str] = None,
        entry_type: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[LedgerEntry]:
        scoped_agent_id = str(agent_id or "").strip()
        scoped_entry_type = str(entry_type or "").strip()
        safe_limit = max(0, min(int(limit or 0), 500)) if limit is not None else None

        def read(connection: sqlite3.Connection) -> list[LedgerEntry]:
            clauses: list[str] = []
            params: list[Any] = []
            if scoped_agent_id:
                clauses.append('"agentId" = ?')
                params.append(scoped_agent_id)
            if scoped_entry_type:
                clauses.append('"entryType" = ?')
                params.append(scoped_entry_type)
            sql = "SELECT * FROM ledger_entries"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += ' ORDER BY "createdAt" DESC, position DESC, record_id DESC'
            if safe_limit is not None:
                sql += " LIMIT ?"
                params.append(safe_limit)
            return [
                record_to_model(record_from_row(row, LedgerEntry), LedgerEntry)
                for row in connection.execute(sql, tuple(params)).fetchall()
            ]

        return self._read(read)

    def list_escrows(
        self,
        *,
        agent_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[EscrowRecord]:
        scoped_agent_id = str(agent_id or "").strip()
        scoped_status = str(status or "").strip()

        def read(connection: sqlite3.Connection) -> list[EscrowRecord]:
            clauses: list[str] = []
            params: list[Any] = []
            if scoped_agent_id:
                clauses.append('("buyerAgentId" = ? OR "sellerAgentId" = ?)')
                params.extend([scoped_agent_id, scoped_agent_id])
            if scoped_status:
                clauses.append('"status" = ?')
                params.append(scoped_status)
            return self._list_records(
                connection,
                "ledger_escrows",
                EscrowRecord,
                " AND ".join(clauses),
                tuple(params),
                order_by='"updatedAt" DESC, position DESC, record_id DESC',
            )

        return self._read(read)

    def list_onramp_sessions(
        self,
        *,
        agent_id: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[OnrampSessionRecord]:
        scoped_agent_id = str(agent_id or "").strip()
        safe_limit = max(0, min(int(limit or 0), 500)) if limit is not None else None

        def read(connection: sqlite3.Connection) -> list[OnrampSessionRecord]:
            clauses: list[str] = []
            params: list[Any] = []
            if scoped_agent_id:
                clauses.append('"agentId" = ?')
                params.append(scoped_agent_id)
            sql = "SELECT * FROM ledger_onramp_sessions"
            if clauses:
                sql += " WHERE " + " AND ".join(clauses)
            sql += ' ORDER BY "updatedAt" DESC, position DESC, record_id DESC'
            if safe_limit is not None:
                sql += " LIMIT ?"
                params.append(safe_limit)
            return [
                record_to_model(record_from_row(row, OnrampSessionRecord), OnrampSessionRecord)
                for row in connection.execute(sql, tuple(params)).fetchall()
            ]

        return self._read(read)

    def admin_summary(self) -> dict[str, Any]:
        def read(connection: sqlite3.Connection) -> dict[str, Any]:
            accounts = self._list_records(connection, "ledger_accounts", LedgerAccount)
            escrows_count = connection.execute("SELECT COUNT(*) AS count FROM ledger_escrows").fetchone()["count"]
            onramp_count = connection.execute("SELECT COUNT(*) AS count FROM ledger_onramp_sessions").fetchone()["count"]
            return {
                "accounts": len(accounts),
                "circleUsdcAvailable": "0",
                "gatewayUsdcAvailable": "0",
                "pendingDeposits": "0",
                "pendingBatch": "0",
                "ledgerLockedAtomic": str(sum(int(account.lockedAtomic) for account in accounts)),
                "escrows": int(escrows_count),
                "onrampSessions": int(onramp_count),
            }

        return self._read(read)

    def ensure_account(self, agent_id: str) -> LedgerAccount:
        return self._write(lambda connection: self._get_account(connection, agent_id, create=True))

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
        def write(connection: sqlite3.Connection) -> LedgerAccount:
            account = self._get_account(connection, agent_id, create=True)
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
            return self._save_account(connection, updated)

        return self._write(write)

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

        def read(connection: sqlite3.Connection) -> Optional[LedgerAccount]:
            clauses: list[str] = []
            params: list[Any] = []
            if normalized_circle_wallet_id is not None:
                clauses.append('"circleWalletId" = ?')
                params.append(normalized_circle_wallet_id)
            if normalized_wallet_address is not None:
                clauses.append('LOWER("walletAddress") = ?')
                params.append(normalized_wallet_address)
            rows = self._list_records(
                connection,
                "ledger_accounts",
                LedgerAccount,
                " OR ".join(clauses),
                tuple(params),
                order_by="position ASC, record_id ASC",
            )
            return rows[0] if rows else None

        return self._read(read)

    def get_circle_webhook_event(
        self,
        notification_id: str,
    ) -> Optional[CircleWebhookEventRecord]:
        event = self._read(
            lambda connection: self._get_record_by_id(
                connection,
                "ledger_circle_webhook_events",
                CircleWebhookEventRecord,
                notification_id,
            )
        )
        return event if isinstance(event, CircleWebhookEventRecord) else None

    def save_circle_webhook_event(
        self,
        event: CircleWebhookEventRecord,
    ) -> CircleWebhookEventRecord:
        return self._write(
            lambda connection: self._upsert_record(
                connection,
                "ledger_circle_webhook_events",
                CircleWebhookEventRecord,
                event.notificationId,
                event,
            )
        )

    def credit(
        self,
        *,
        agent_id: str,
        amount_atomic: str,
        reason: Optional[str],
        metadata: dict[str, Any],
    ) -> tuple[LedgerAccount, LedgerEntry]:
        amount = parse_positive_atomic(amount_atomic)

        def write(connection: sqlite3.Connection) -> tuple[LedgerAccount, LedgerEntry]:
            account = self._get_account(connection, agent_id, create=True)
            current = now_iso()
            updated = account.model_copy(
                update={
                    "availableAtomic": add_atomic(account.availableAtomic, amount),
                    "updatedAt": current,
                }
            )
            entry = self._entry(
                entry_type="credit",
                agent_id=agent_id,
                available_delta=amount,
                reason=reason,
                metadata=metadata,
            )
            return self._save_account(connection, updated), self._save_entry(connection, entry)

        return self._write(write)

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
        def write(connection: sqlite3.Connection) -> tuple[LedgerAccount, LedgerEntry]:
            account = self._get_account(connection, agent_id, create=True)
            current = now_iso()
            updated = account.model_copy(update={"updatedAt": current})
            entry = self._entry(
                entry_type=entry_type,
                agent_id=agent_id,
                escrow_id=escrow_id,
                reason=reason,
                metadata=metadata,
            )
            return self._save_account(connection, updated), self._save_entry(connection, entry)

        return self._write(write)

    def validate_agent_transfer(
        self,
        *,
        from_agent_id: str,
        to_agent_id: str,
        amount_atomic: str,
    ) -> None:
        amount = parse_positive_atomic(amount_atomic)
        if amount > AGENT_TRANSFER_SINGLE_LIMIT_ATOMIC:
            raise ValueError("single transfer limit exceeded: max 0.001 USDC")

        def read(connection: sqlite3.Connection) -> None:
            sender = self._find_account_row(connection, from_agent_id)
            receiver = self._find_account_row(connection, to_agent_id)
            if sender is None:
                raise ValueError("sender account not found")
            if receiver is None:
                raise ValueError("receiver account not found")
            self._require_circle_wallet(sender, "sender")
            self._require_circle_wallet(receiver, "receiver")

        self._read(read)

    def validate_withdrawal(
        self,
        *,
        agent_id: str,
        amount_atomic: str,
        owner_email: Optional[str],
        available_atomic: Optional[str] = None,
        available_label: str = "available balance",
    ) -> LedgerAccount:
        amount = parse_positive_atomic(amount_atomic)
        def read(connection: sqlite3.Connection) -> LedgerAccount:
            account = self._find_account_row(connection, agent_id)
            if account is None:
                raise ValueError("agent account not found")
            self._require_circle_wallet(account, "source")
            balance_basis = (
                parse_nonnegative_atomic(available_atomic)
                if available_atomic is not None
                else parse_nonnegative_atomic(account.availableAtomic)
            )
            if balance_basis < amount:
                raise ValueError(f"amount exceeds {available_label}")
            self._validate_withdrawal_limits(connection, agent_id, amount)
            return account

        return self._read(read)

    def _validate_withdrawal_limits(
        self,
        connection: sqlite3.Connection,
        agent_id: str,
        amount: int,
    ) -> None:
        current = parse_ledger_datetime(now_iso()) or datetime.now(timezone.utc)
        daily_start = current - WITHDRAWAL_DAILY_WINDOW
        weekly_start = current - WITHDRAWAL_WEEKLY_WINDOW
        entries = self._list_records(
            connection,
            "ledger_entries",
            LedgerEntry,
            '"agentId" = ? AND "entryType" IN (?, ?)',
            (agent_id, "withdrawal", "withdrawal_submitted"),
            order_by='"createdAt" DESC, record_id DESC',
        )
        daily_total = 0
        weekly_total = 0
        for entry in entries:
            if not isinstance(entry, LedgerEntry) or is_failed_withdrawal(entry):
                continue
            created_at = parse_ledger_datetime(entry.createdAt)
            if created_at is None or created_at < weekly_start:
                continue
            entry_amount = withdrawal_limit_amount(entry)
            weekly_total += entry_amount
            if created_at >= daily_start:
                daily_total += entry_amount

        if daily_total + amount > WITHDRAWAL_DAILY_LIMIT_ATOMIC:
            raise ValueError("withdrawal rejected by service risk policy")
        if weekly_total + amount > WITHDRAWAL_WEEKLY_LIMIT_ATOMIC:
            raise ValueError("withdrawal rejected by service risk policy")

    def account_by_email(self, email: str) -> LedgerAccount:
        normalized = normalize_email(email)
        if normalized is None:
            raise ValueError("email must not be empty")
        def read(connection: sqlite3.Connection) -> LedgerAccount:
            accounts = self._list_records(
                connection,
                "ledger_accounts",
                LedgerAccount,
                'LOWER("email") = ?',
                (normalized,),
            )
            if accounts:
                return accounts[0]
            raise LookupError(f"ledger account email not found: {normalized}")

        return self._read(read)

    def claimed_agent_ids_for_dashboard_email(self, email: str) -> list[str]:
        normalized = normalize_email(email)
        if normalized is None:
            return []

        def read(connection: sqlite3.Connection) -> list[str]:
            accounts = self._list_records(
                connection,
                "ledger_accounts",
                LedgerAccount,
                '"dashboardClaimedAt" IS NOT NULL',
            )
            return [
                account.agentId
                for account in accounts
                if normalize_email(account.dashboardClaimedByEmail or account.email) == normalized
            ]

        return self._read(read)

    def get_account(self, agent_id: str) -> Optional[LedgerAccount]:
        return self._read(lambda connection: self._find_account_row(connection, agent_id))

    def claim_dashboard_account(
        self,
        *,
        agent_id: str,
        email: str,
        dashboard_email: Optional[str] = None,
    ) -> LedgerAccount:
        normalized = normalize_email(email)
        if normalized is None:
            raise ValueError("email must not be empty")
        normalized_dashboard_email = normalize_email(dashboard_email) or normalized

        def write(connection: sqlite3.Connection) -> LedgerAccount:
            account = self._get_account(connection, agent_id, create=False)
            if normalize_email(account.email) != normalized:
                raise ValueError("agent is not assigned to this email")
            updated = account.model_copy(
                update={
                    "dashboardClaimedAt": account.dashboardClaimedAt or now_iso(),
                    "dashboardClaimedByEmail": normalized_dashboard_email,
                    "updatedAt": now_iso(),
                }
            )
            return self._save_account(connection, updated)

        return self._write(write)

    def reset_dashboard_claims(
        self,
        *,
        agent_ids: Optional[list[str]] = None,
    ) -> list[LedgerAccount]:
        requested = {
            str(agent_id).strip()
            for agent_id in (agent_ids or [])
            if str(agent_id).strip()
        }

        def write(connection: sqlite3.Connection) -> list[LedgerAccount]:
            updated_accounts: list[LedgerAccount] = []
            current = now_iso()
            where = ""
            params: tuple[Any, ...] = ()
            if requested:
                placeholders = ", ".join("?" for _ in requested)
                where = f'"agentId" IN ({placeholders})'
                params = tuple(sorted(requested))
            accounts = self._list_records(
                connection,
                "ledger_accounts",
                LedgerAccount,
                where,
                params,
            )
            for account in accounts:
                if requested and account.agentId not in requested:
                    continue
                if not account.dashboardClaimedAt and not account.dashboardClaimedByEmail:
                    continue
                updated = account.model_copy(
                    update={
                        "dashboardClaimedAt": None,
                        "dashboardClaimedByEmail": None,
                        "updatedAt": current,
                    }
                )
                updated_accounts.append(self._save_account(connection, updated))
            return updated_accounts

        return self._write(write)

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

        def write(connection: sqlite3.Connection) -> tuple[LedgerAccount, LedgerAccount, list[LedgerEntry]]:
            sender = self._get_account(connection, from_agent_id, create=False)
            receiver = self._get_account(connection, to_agent_id, create=False)
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
            self._save_entry(connection, sender_entry)
            self._save_entry(connection, receiver_entry)
            return sender, receiver, [sender_entry, receiver_entry]

        return self._write(write)

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
        available_label: str = "available balance",
    ) -> tuple[LedgerAccount, LedgerEntry]:
        amount = parse_positive_atomic(amount_atomic)
        destination = normalize_evm_address(destination_address)

        def write(connection: sqlite3.Connection) -> tuple[LedgerAccount, LedgerEntry]:
            account = self._get_account(connection, agent_id, create=False)
            self._require_circle_wallet(account, "source")
            balance_basis = (
                parse_nonnegative_atomic(available_atomic)
                if available_atomic is not None
                else parse_nonnegative_atomic(account.availableAtomic)
            )
            if balance_basis < amount:
                raise ValueError(f"amount exceeds {available_label}")
            current = now_iso()
            local_available = parse_nonnegative_atomic(account.availableAtomic)
            next_available = (
                str(local_available - amount)
                if local_available >= amount
                else account.availableAtomic
            )
            updated = account.model_copy(
                update={
                    "availableAtomic": next_available,
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
            return self._save_account(connection, updated), self._save_entry(connection, entry)

        return self._write(write)

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

    def withdrawal_failed(
        self,
        *,
        entry_id: str,
        agent_id: str,
        destination_address: str,
        amount_atomic: str,
        reason: Optional[str],
        metadata: dict[str, Any],
        withdrawal_id: str,
        failure_reason: Optional[str],
    ) -> LedgerEntry:
        amount = parse_positive_atomic(amount_atomic)
        destination = normalize_evm_address(destination_address)

        def write(connection: sqlite3.Connection) -> LedgerEntry:
            entry = self._get_record_by_id(
                connection,
                "ledger_entries",
                LedgerEntry,
                entry_id,
            )
            if not isinstance(entry, LedgerEntry):
                raise ValueError("withdrawal entry not found")
            if entry.agentId != agent_id:
                raise ValueError("withdrawal entry does not match agent")
            updated = entry.model_copy(
                update={
                    "reason": reason or "withdrawal failed",
                    "metadata": {
                        **metadata,
                        "dashboardStatus": "failed",
                        "amountAtomic": str(amount),
                        "withdrawalId": withdrawal_id,
                        "failureReason": failure_reason,
                        "destinationAddress": destination,
                        "counterparty": f"External · {short_address(destination)}",
                        "network": "Base",
                    },
                }
            )
            return self._save_entry(connection, updated)

        return self._write(write)

    def withdrawal_completed(
        self,
        *,
        entry_id: str,
        agent_id: str,
        destination_address: str,
        amount_atomic: str,
        reason: Optional[str],
        metadata: dict[str, Any],
        withdrawal_id: str,
        settlement_record_id: Optional[str],
        available_atomic: Optional[str] = None,
        available_label: str = "available balance",
    ) -> tuple[LedgerAccount, LedgerEntry]:
        amount = parse_positive_atomic(amount_atomic)
        destination = normalize_evm_address(destination_address)

        def write(connection: sqlite3.Connection) -> tuple[LedgerAccount, LedgerEntry]:
            account = self._get_account(connection, agent_id, create=False)
            self._require_circle_wallet(account, "source")
            balance_basis = (
                parse_nonnegative_atomic(available_atomic)
                if available_atomic is not None
                else parse_nonnegative_atomic(account.availableAtomic)
            )
            if balance_basis < amount:
                raise ValueError(f"amount exceeds {available_label}")
            current = now_iso()
            local_available = parse_nonnegative_atomic(account.availableAtomic)
            next_available = (
                str(local_available - amount)
                if local_available >= amount
                else account.availableAtomic
            )
            updated_account = account.model_copy(
                update={
                    "availableAtomic": next_available,
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
            entry = self._get_record_by_id(
                connection,
                "ledger_entries",
                LedgerEntry,
                entry_id,
            )
            if not isinstance(entry, LedgerEntry):
                raise ValueError("withdrawal entry not found")
            if entry.agentId != agent_id:
                raise ValueError("withdrawal entry does not match agent")
            updated_entry = entry.model_copy(
                update={
                    "entryType": "withdrawal",
                    "availableDeltaAtomic": str(-amount),
                    "lockedDeltaAtomic": "0",
                    "reason": reason or "withdrawal",
                    "metadata": entry_metadata,
                }
            )
            return (
                self._save_account(connection, updated_account),
                self._save_entry(connection, updated_entry),
            )

        return self._write(write)

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

        def write(connection: sqlite3.Connection) -> tuple[EscrowRecord, LedgerEntry]:
            buyer = self._get_account(connection, buyer_agent_id, create=False)
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
            self._save_account(connection, updated_buyer)
            self._save_escrow(connection, escrow)
            self._save_entry(connection, entry)
            return escrow, entry

        return self._write(write)

    def release_escrow(self, escrow_id: str) -> EscrowRecord:
        def write(connection: sqlite3.Connection) -> EscrowRecord:
            escrow = self.get_escrow_from_connection(connection, escrow_id)
            self._require_locked(escrow)
            amount = int(escrow.amountAtomic)
            buyer = self._get_account(connection, escrow.buyerAgentId, create=False)
            seller = self._get_account(connection, escrow.sellerAgentId, create=True)
            current = now_iso()
            self._save_account(
                connection,
                buyer.model_copy(
                    update={
                        "lockedAtomic": add_atomic(buyer.lockedAtomic, -amount),
                        "updatedAt": current,
                    }
                ),
            )
            self._save_account(
                connection,
                seller.model_copy(
                    update={
                        "availableAtomic": add_atomic(seller.availableAtomic, amount),
                        "updatedAt": current,
                    }
                ),
            )
            updated_escrow = escrow.model_copy(
                update={
                    "status": "released",
                    "releasedAt": current,
                    "updatedAt": current,
                }
            )
            self._save_escrow(connection, updated_escrow)
            self._save_entry(
                connection,
                self._entry(
                    entry_type="escrow_release",
                    agent_id=escrow.buyerAgentId,
                    locked_delta=-amount,
                    escrow_id=escrow.escrowId,
                    reason="escrow released",
                ),
            )
            self._save_entry(
                connection,
                self._entry(
                    entry_type="escrow_release",
                    agent_id=escrow.sellerAgentId,
                    available_delta=amount,
                    escrow_id=escrow.escrowId,
                    reason="escrow released",
                ),
            )
            return updated_escrow

        return self._write(write)

    def refund_escrow(self, escrow_id: str) -> EscrowRecord:
        def write(connection: sqlite3.Connection) -> EscrowRecord:
            escrow = self.get_escrow_from_connection(connection, escrow_id)
            self._require_locked(escrow)
            amount = int(escrow.amountAtomic)
            buyer = self._get_account(connection, escrow.buyerAgentId, create=False)
            current = now_iso()
            self._save_account(
                connection,
                buyer.model_copy(
                    update={
                        "availableAtomic": add_atomic(buyer.availableAtomic, amount),
                        "lockedAtomic": add_atomic(buyer.lockedAtomic, -amount),
                        "updatedAt": current,
                    }
                ),
            )
            updated_escrow = escrow.model_copy(
                update={
                    "status": "refunded",
                    "refundedAt": current,
                    "updatedAt": current,
                }
            )
            self._save_escrow(connection, updated_escrow)
            self._save_entry(
                connection,
                self._entry(
                    entry_type="escrow_refund",
                    agent_id=escrow.buyerAgentId,
                    available_delta=amount,
                    locked_delta=-amount,
                    escrow_id=escrow.escrowId,
                    reason="escrow refunded",
                ),
            )
            return updated_escrow

        return self._write(write)

    def entries_for_escrow_event(
        self,
        *,
        escrow_id: str,
        entry_type: Literal["escrow_lock", "escrow_release", "escrow_refund"],
    ) -> list[LedgerEntry]:
        return self._read(
            lambda connection: self._list_records(
                connection,
                "ledger_entries",
                LedgerEntry,
                '"escrowId" = ? AND "entryType" = ?',
                (escrow_id, entry_type),
            )
        )

    def add_chain_record(self, record: LedgerChainRecord) -> LedgerChainRecord:
        return self._write(
            lambda connection: self._append_record(
                connection,
                "ledger_chain_records",
                LedgerChainRecord,
                record.recordId,
                record,
            )
        )

    def add_settlement_record(self, record: LedgerSettlementRecord) -> LedgerSettlementRecord:
        return self._write(
            lambda connection: self._append_record(
                connection,
                "ledger_settlement_records",
                LedgerSettlementRecord,
                record.recordId,
                record,
            )
        )

    def get_escrow(self, escrow_id: str) -> EscrowRecord:
        return self._read(lambda connection: self.get_escrow_from_connection(connection, escrow_id))

    def get_escrow_from_connection(
        self,
        connection: sqlite3.Connection,
        escrow_id: str,
    ) -> EscrowRecord:
        escrow = self._get_record_by_id(
            connection,
            "ledger_escrows",
            EscrowRecord,
            escrow_id,
        )
        if isinstance(escrow, EscrowRecord):
            return escrow
        raise LookupError("escrow not found")

    def find_onramp_session_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> Optional[OnrampSessionRecord]:
        def read(connection: sqlite3.Connection) -> Optional[OnrampSessionRecord]:
            sessions = self._list_records(
                connection,
                "ledger_onramp_sessions",
                OnrampSessionRecord,
                '"idempotencyKey" = ?',
                (idempotency_key,),
            )
            return sessions[0] if sessions else None

        return self._read(read)

    def get_onramp_session(self, session_id: str) -> OnrampSessionRecord:
        return self._read(lambda connection: self._get_onramp_session(connection, session_id))

    def _get_onramp_session(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> OnrampSessionRecord:
        session = self._get_record_by_id(
            connection,
            "ledger_onramp_sessions",
            OnrampSessionRecord,
            session_id,
        )
        if isinstance(session, OnrampSessionRecord):
            return session
        raise LookupError("onramp session not found")

    def add_onramp_session(self, session: OnrampSessionRecord) -> OnrampSessionRecord:
        def write(connection: sqlite3.Connection) -> OnrampSessionRecord:
            existing = self.find_onramp_session_by_idempotency_key_in_connection(
                connection,
                session.idempotencyKey,
            )
            if existing is not None:
                return existing
            saved = self._save_onramp_session(connection, session)
            self._save_onramp_event(
                connection,
                OnrampEventRecord(
                    eventId=f"evt_{uuid.uuid4().hex}",
                    sessionId=session.sessionId,
                    eventType="session_created",
                    rawPayload={"idempotencyKey": session.idempotencyKey},
                    createdAt=session.createdAt,
                ),
            )
            return saved

        return self._write(write)

    def find_onramp_session_by_idempotency_key_in_connection(
        self,
        connection: sqlite3.Connection,
        idempotency_key: str,
    ) -> Optional[OnrampSessionRecord]:
        sessions = self._list_records(
            connection,
            "ledger_onramp_sessions",
            OnrampSessionRecord,
            '"idempotencyKey" = ?',
            (idempotency_key,),
        )
        return sessions[0] if sessions else None

    def confirm_onramp_session(
        self,
        session_id: str,
        request: ConfirmOnrampSessionRequest,
    ) -> OnrampSessionRecord:
        amount = parse_positive_atomic(request.amountAtomic)

        def write(connection: sqlite3.Connection) -> OnrampSessionRecord:
            session = self._get_onramp_session(connection, session_id)
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

            account = self._get_account(connection, session.agentId, create=True)
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
            self._save_account(connection, updated_account)
            self._save_entry(connection, entry)
            self._save_onramp_session(connection, updated_session)
            self._save_onramp_event(
                connection,
                OnrampEventRecord(
                    eventId=f"evt_{uuid.uuid4().hex}",
                    sessionId=session.sessionId,
                    eventType="ledger_credited",
                    providerEventId=request.providerEventId,
                    rawPayload=request.rawPayload,
                    createdAt=current,
                ),
            )
            return updated_session

        return self._write(write)

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
            INSERT OR REPLACE INTO ledger_meta(key, value)
            VALUES ('schema_version', '2')
            """
        )
        for table_name, _state_field, model_type, _record_id in LEDGER_COLLECTIONS:
            self._ensure_record_table(connection, table_name, model_type)
        self._ensure_query_indexes(connection)

    def _table_exists(self, connection: sqlite3.Connection, table_name: str) -> bool:
        return (
            connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = ?
                """,
                (table_name,),
            ).fetchone()
            is not None
        )

    def _ensure_query_indexes(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ledger_accounts_agent_asset
            ON ledger_accounts("agentId", "asset")
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ledger_accounts_circle_wallet
            ON ledger_accounts("circleWalletId")
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ledger_accounts_wallet_address
            ON ledger_accounts("walletAddress")
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ledger_accounts_email
            ON ledger_accounts("email")
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ledger_entries_agent_type_created
            ON ledger_entries("agentId", "entryType", "createdAt")
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ledger_entries_escrow
            ON ledger_entries("escrowId")
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ledger_circle_webhook_events_transaction
            ON ledger_circle_webhook_events("transactionId")
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ledger_circle_webhook_events_agent
            ON ledger_circle_webhook_events("agentId")
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ledger_onramp_sessions_agent
            ON ledger_onramp_sessions("agentId")
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ledger_onramp_sessions_idempotency
            ON ledger_onramp_sessions("idempotencyKey")
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ledger_onramp_events_session
            ON ledger_onramp_events("sessionId")
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ledger_chain_records_escrow
            ON ledger_chain_records("escrowId")
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ledger_settlement_records_from_to
            ON ledger_settlement_records("fromAgentId", "toAgentId")
            """
        )

    def _maybe_import_legacy_json(self, connection: sqlite3.Connection) -> None:
        if self._relation_record_count(connection) > 0:
            self._drop_legacy_records_table(connection)
            return
        if not self._table_exists(connection, "ledger_records"):
            record_count = 0
        else:
            record_count = connection.execute(
                "SELECT COUNT(*) AS count FROM ledger_records"
            ).fetchone()["count"]
        if record_count:
            state = self._load_state_from_legacy_records(connection)
            started_transaction = not connection.in_transaction
            if started_transaction:
                connection.execute("BEGIN IMMEDIATE")
            try:
                self._replace_state(connection, state)
                connection.execute("DROP TABLE IF EXISTS ledger_records")
                connection.execute(
                    """
                    INSERT OR REPLACE INTO ledger_meta(key, value)
                    VALUES ('legacy_records_imported', ?)
                    """,
                    (now_iso(),),
                )
                if started_transaction:
                    connection.commit()
            except Exception:
                if started_transaction:
                    connection.rollback()
                raise
            return
        self._drop_legacy_records_table(connection)
        if not self.legacy_json_path or not os.path.exists(self.legacy_json_path):
            return
        imported = connection.execute(
            "SELECT value FROM ledger_meta WHERE key = 'legacy_json_imported'"
        ).fetchone()
        if imported is not None:
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

    def _drop_legacy_records_table(self, connection: sqlite3.Connection) -> None:
        if not self._table_exists(connection, "ledger_records"):
            return
        started_transaction = not connection.in_transaction
        if started_transaction:
            connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute("DROP TABLE ledger_records")
            connection.execute(
                """
                INSERT OR REPLACE INTO ledger_meta(key, value)
                VALUES ('legacy_records_imported', ?)
                """,
                (now_iso(),),
            )
            if started_transaction:
                connection.commit()
        except Exception:
            if started_transaction:
                connection.rollback()
            raise

    def _load_state_from_db(self, connection: sqlite3.Connection) -> LedgerState:
        raw_state: dict[str, Any] = {}
        for table_name, state_field, model_type, _record_id in LEDGER_COLLECTIONS:
            rows = connection.execute(
                f"""
                SELECT * FROM {quote_identifier(table_name)}
                ORDER BY position ASC, record_id ASC
                """
            ).fetchall()
            raw_state[state_field] = [
                record_from_row(row, model_type)
                for row in rows
            ]
        return LedgerState.model_validate(migrate_ledger_state_payload(raw_state))

    def _replace_state(self, connection: sqlite3.Connection, state: LedgerState) -> None:
        payload = state.model_dump()
        for table_name, state_field, model_type, record_id_for in LEDGER_COLLECTIONS:
            connection.execute(f"DELETE FROM {quote_identifier(table_name)}")
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
                insert_record(connection, table_name, model_type, record_id, position, record)
        if self._table_exists(connection, "ledger_records"):
            connection.execute("DROP TABLE ledger_records")
        connection.execute(
            """
            INSERT OR REPLACE INTO ledger_meta(key, value)
            VALUES ('updated_at', ?)
            """,
            (now_iso(),),
        )

    def _ensure_record_table(
        self,
        connection: sqlite3.Connection,
        table_name: str,
        model_type: type[BaseModel],
    ) -> None:
        field_columns = [
            f"{quote_identifier(field_name)} TEXT"
            for field_name in model_type.model_fields
        ]
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {quote_identifier(table_name)} (
                record_id TEXT PRIMARY KEY,
                position INTEGER NOT NULL,
                {", ".join(field_columns)}
            )
            """
        )
        existing_columns = {
            row["name"]
            for row in connection.execute(
                f"PRAGMA table_info({quote_identifier(table_name)})"
            ).fetchall()
        }
        for field_name in model_type.model_fields:
            if field_name not in existing_columns:
                connection.execute(
                    f"""
                    ALTER TABLE {quote_identifier(table_name)}
                    ADD COLUMN {quote_identifier(field_name)} TEXT
                    """
                )
        connection.execute(
            f"""
            CREATE INDEX IF NOT EXISTS {quote_identifier(f"idx_{table_name}_position")}
            ON {quote_identifier(table_name)}(position)
            """
        )

    def _relation_record_count(self, connection: sqlite3.Connection) -> int:
        total = 0
        for table_name, _state_field, _model_type, _record_id in LEDGER_COLLECTIONS:
            total += connection.execute(
                f"SELECT COUNT(*) AS count FROM {quote_identifier(table_name)}"
            ).fetchone()["count"]
        return total

    def _load_state_from_legacy_records(self, connection: sqlite3.Connection) -> LedgerState:
        raw_state: dict[str, Any] = {}
        for sql_collection, state_field, _record_id in LEGACY_LEDGER_RECORD_COLLECTIONS:
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
