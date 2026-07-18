from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

import psycopg

from migrate_sqlite_to_postgres import migrate_sqlite_to_postgres
from models import WaitlistApplication
from postgres_store import PostgresLedgerStore
from store import OffchainLedgerStore
from utils import now_iso


@unittest.skipUnless(os.environ.get("TEST_POSTGRES_URL"), "TEST_POSTGRES_URL is not set")
class PostgresLedgerStoreIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_url = os.environ["TEST_POSTGRES_URL"]
        self.schema = f"kovaloop_test_{uuid.uuid4().hex}"
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.sqlite_path = Path(self.temporary_directory.name) / "ledger.sqlite3"

    def tearDown(self) -> None:
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            connection.execute(f'DROP SCHEMA IF EXISTS "{self.schema}" CASCADE')
        self.temporary_directory.cleanup()

    def test_store_preserves_ledger_behavior(self) -> None:
        store = PostgresLedgerStore(self.database_url, schema=self.schema)
        self._bind_test_wallets(store)
        store.credit(
            agent_id="agent_sender",
            amount_atomic="1000",
            reason="integration test",
            metadata={},
        )

        sender, receiver, entries = store.transfer_between_agents(
            from_agent_id="agent_sender",
            to_agent_id="agent_receiver",
            amount_atomic="250",
            reason="integration test",
            metadata={},
            transfer_id="transfer_integration",
            settlement_record_id=None,
        )

        self.assertEqual(sender.availableAtomic, "1000")
        self.assertEqual(receiver.availableAtomic, "0")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].availableDeltaAtomic, "-250")
        self.assertEqual(entries[1].availableDeltaAtomic, "250")
        self.assertEqual(len(store.load().accounts), 2)
        self.assertEqual(len(store.list_accounts(claimable=True)), 2)
        self.assertEqual(len(store.list_entries(agent_id="agent_sender", limit=10)), 2)

    def test_migration_is_verified_and_idempotent(self) -> None:
        source = OffchainLedgerStore(str(self.sqlite_path))
        self._bind_test_wallets(source)
        source.credit(
            agent_id="agent_sender",
            amount_atomic="1000",
            reason="migration test",
            metadata={},
        )
        source.transfer_between_agents(
            from_agent_id="agent_sender",
            to_agent_id="agent_receiver",
            amount_atomic="250",
            reason="migration test",
            metadata={},
            transfer_id="transfer_migration",
            settlement_record_id=None,
        )
        source.append_waitlist_application(
            WaitlistApplication(
                applicationId="application_migration",
                email="migration@example.com",
                name="Migration Test",
                createdAt=now_iso(),
            )
        )

        first = migrate_sqlite_to_postgres(
            self.sqlite_path,
            self.database_url,
            schema=self.schema,
        )
        second = migrate_sqlite_to_postgres(
            self.sqlite_path,
            self.database_url,
            schema=self.schema,
        )

        self.assertEqual(first["status"], "migrated")
        self.assertEqual(second["status"], "already_migrated")
        self.assertTrue(second["verified"])

    @staticmethod
    def _bind_test_wallets(store: OffchainLedgerStore) -> None:
        store.bind_account_wallet(
            agent_id="agent_sender",
            wallet_address="0x1111111111111111111111111111111111111111",
            circle_wallet_id=None,
        )
        store.bind_account_wallet(
            agent_id="agent_receiver",
            wallet_address="0x2222222222222222222222222222222222222222",
            circle_wallet_id=None,
        )
