from __future__ import annotations

import threading
from typing import Any

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from models import WaitlistApplication
from store import OffchainLedgerStore, quote_identifier


DEFAULT_POSTGRES_SCHEMA = "kovaloop"


class PostgresConnectionAdapter:
    def __init__(self, connection: psycopg.Connection) -> None:
        self.connection = connection

    @property
    def in_transaction(self) -> bool:
        return self.connection.info.transaction_status != TransactionStatus.IDLE

    def execute(self, sql: str, params: Any = ()):
        statement = sql.strip()
        if statement.upper() == "BEGIN IMMEDIATE":
            statement = "BEGIN"
        statement = statement.replace("?", "%s")
        return self.connection.execute(statement, params)

    def commit(self) -> None:
        self.connection.commit()

    def rollback(self) -> None:
        self.connection.rollback()

    def close(self) -> None:
        self.connection.close()


class PostgresLedgerStore(OffchainLedgerStore):
    def __init__(self, database_url: str, *, schema: str = DEFAULT_POSTGRES_SCHEMA) -> None:
        super().__init__(database_url, legacy_json_path=None)
        self.schema = schema
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    def _connect(self) -> PostgresConnectionAdapter:
        connection = psycopg.connect(
            self.path,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=15,
            prepare_threshold=None,
        )
        adapter = PostgresConnectionAdapter(connection)
        adapter.execute(f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(self.schema)}")
        adapter.execute(
            f"SET search_path TO {quote_identifier(self.schema)}, public"
        )
        return adapter

    def _ensure_schema(self, connection: PostgresConnectionAdapter) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            super()._ensure_schema(connection)
            connection.execute(
                f"REVOKE ALL ON SCHEMA {quote_identifier(self.schema)} FROM PUBLIC"
            )
            self._schema_ready = True

    def _table_exists(
        self,
        connection: PostgresConnectionAdapter,
        table_name: str,
    ) -> bool:
        row = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = ? AND table_name = ?
            ) AS present
            """,
            (self.schema, table_name),
        ).fetchone()
        return bool(row["present"])

    def _ensure_record_table(
        self,
        connection: PostgresConnectionAdapter,
        table_name: str,
        model_type,
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
            row["column_name"]
            for row in connection.execute(
                """
                SELECT column_name FROM information_schema.columns
                WHERE table_schema = ? AND table_name = ?
                """,
                (self.schema, table_name),
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

    def _maybe_import_legacy_json(self, connection: PostgresConnectionAdapter) -> None:
        return None

    def list_all_waitlist_applications(self) -> list[WaitlistApplication]:
        return self._read(
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
