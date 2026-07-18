from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models import WaitlistApplication
from postgres_store import PostgresLedgerStore
from store import LEDGER_COLLECTIONS, OffchainLedgerStore, quote_identifier


DATA_TABLES = [
    *(table_name for table_name, _field, _model, _record_id in LEDGER_COLLECTIONS),
    "ledger_waitlist_applications",
]
MIGRATION_TABLES = ["ledger_meta", *DATA_TABLES]
TABLE_COLUMNS = {
    "ledger_meta": ["key", "value"],
    **{
        table_name: ["record_id", "position", *model_type.model_fields]
        for table_name, _field, model_type, _record_id in LEDGER_COLLECTIONS
    },
    "ledger_waitlist_applications": [
        "record_id",
        "position",
        *WaitlistApplication.model_fields,
    ],
}


def _ordered_rows(connection, table_name: str) -> list[dict[str, Any]]:
    order_by = "key ASC" if table_name == "ledger_meta" else "position ASC, record_id ASC"
    selected_columns = ", ".join(
        quote_identifier(column) for column in TABLE_COLUMNS[table_name]
    )
    rows = connection.execute(
        f"SELECT {selected_columns} FROM {quote_identifier(table_name)} ORDER BY {order_by}"
    ).fetchall()
    return [dict(row) for row in rows]


def _digest_rows(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(
        rows,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _table_report(connection, table_names: list[str]) -> dict[str, dict[str, Any]]:
    return {
        table_name: {
            "rows": len(rows),
            "sha256": _digest_rows(rows),
        }
        for table_name in table_names
        for rows in [_ordered_rows(connection, table_name)]
    }


def _insert_rows(connection, table_name: str, rows: list[dict[str, Any]]) -> None:
    for row in rows:
        columns = list(row)
        placeholders = ", ".join("?" for _ in columns)
        connection.execute(
            f"""
            INSERT INTO {quote_identifier(table_name)}
            ({", ".join(quote_identifier(column) for column in columns)})
            VALUES ({placeholders})
            """,
            tuple(row[column] for column in columns),
        )


def migrate_sqlite_to_postgres(
    sqlite_path: Path,
    database_url: str,
    *,
    schema: str = "kovaloop",
) -> dict[str, Any]:
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite ledger does not exist: {sqlite_path}")

    source_store = OffchainLedgerStore(str(sqlite_path))
    source_store.load()
    source_connection = source_store._connect()
    try:
        integrity = source_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"SQLite integrity check failed: {integrity}")
        source_rows = {
            table_name: _ordered_rows(source_connection, table_name)
            for table_name in MIGRATION_TABLES
        }
        source_report = {
            table_name: {
                "rows": len(source_rows[table_name]),
                "sha256": _digest_rows(source_rows[table_name]),
            }
            for table_name in DATA_TABLES
        }
        source_state = source_store.load().model_dump()
        source_waitlist = [
            application.model_dump()
            for application in source_store._read(
                lambda connection: [
                    WaitlistApplication.model_validate(
                        {
                            field_name: row[field_name]
                            for field_name in WaitlistApplication.model_fields
                            if row[field_name] is not None
                        }
                    )
                    for row in connection.execute(
                        """
                        SELECT * FROM ledger_waitlist_applications
                        ORDER BY position ASC, record_id ASC
                        """
                    ).fetchall()
                ]
            )
        ]
    finally:
        source_connection.close()

    target_store = PostgresLedgerStore(database_url, schema=schema)
    target_connection = target_store._connect()
    try:
        target_store._ensure_schema(target_connection)
        existing_report = _table_report(target_connection, DATA_TABLES)
        if any(item["rows"] for item in existing_report.values()):
            if existing_report != source_report:
                raise RuntimeError(
                    "PostgreSQL target already contains data that differs from SQLite"
                )
            status = "already_migrated"
        else:
            target_connection.execute("BEGIN")
            try:
                for table_name in reversed(MIGRATION_TABLES):
                    target_connection.execute(
                        f"DELETE FROM {quote_identifier(table_name)}"
                    )
                for table_name in MIGRATION_TABLES:
                    _insert_rows(target_connection, table_name, source_rows[table_name])
                target_connection.execute(
                    """
                    INSERT INTO ledger_meta(key, value)
                    VALUES ('sqlite_migrated_at', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (datetime.now(timezone.utc).isoformat(),),
                )
                target_connection.commit()
            except Exception:
                target_connection.rollback()
                raise
            status = "migrated"

        target_report = _table_report(target_connection, DATA_TABLES)
        if target_report != source_report:
            raise RuntimeError("PostgreSQL table verification differs from SQLite")
    finally:
        target_connection.close()

    target_state = target_store.load().model_dump()
    target_waitlist = [
        application.model_dump()
        for application in target_store.list_all_waitlist_applications()
    ]
    if target_state != source_state:
        raise RuntimeError("PostgreSQL ledger state differs from SQLite")
    if target_waitlist != source_waitlist:
        raise RuntimeError("PostgreSQL waitlist data differs from SQLite")

    return {
        "status": status,
        "schema": schema,
        "sourceIntegrity": integrity,
        "tables": target_report,
        "verified": True,
    }


def main() -> None:
    database_url = os.environ.get("SUPABASE_CONNECTION_URL", "").strip()
    if not database_url:
        raise ValueError("SUPABASE_CONNECTION_URL is required")
    report = migrate_sqlite_to_postgres(
        Path(os.environ.get("LEDGER_DB_PATH", "/app/data/offchain_ledger.sqlite3")),
        database_url,
        schema=os.environ.get("LEDGER_POSTGRES_SCHEMA", "kovaloop"),
    )
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
