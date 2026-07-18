from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import services
from postgres_store import PostgresLedgerStore
from store import OffchainLedgerStore


class StoreSelectionTests(unittest.TestCase):
    def tearDown(self) -> None:
        services.get_store.cache_clear()

    def test_database_url_is_required_outside_tests(self) -> None:
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "", "LEDGER_SQLITE_TEST_MODE": "false"},
            clear=False,
        ):
            services.get_store.cache_clear()
            with self.assertRaisesRegex(RuntimeError, "DATABASE_URL is required"):
                services.get_store()

    def test_database_url_selects_postgres(self) -> None:
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql://example.invalid/postgres",
                "LEDGER_SQLITE_TEST_MODE": "false",
            },
            clear=False,
        ):
            services.get_store.cache_clear()
            store = services.get_store()

        self.assertIsInstance(store, PostgresLedgerStore)

    def test_sqlite_requires_explicit_test_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.dict(
                os.environ,
                {
                    "DATABASE_URL": "",
                    "LEDGER_SQLITE_TEST_MODE": "true",
                    "LEDGER_DB_PATH": str(Path(temporary_directory) / "ledger.sqlite3"),
                    "LEDGER_STATE_PATH": str(Path(temporary_directory) / "ledger.json"),
                },
                clear=False,
            ):
                services.get_store.cache_clear()
                store = services.get_store()

        self.assertIsInstance(store, OffchainLedgerStore)


if __name__ == "__main__":
    unittest.main()
