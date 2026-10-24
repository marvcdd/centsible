"""Tests for the SQLite database wrapper and migrations.

SQLite 数据库封装与迁移的测试。"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from centsible.errors import DataError
from centsible.infrastructure.database import Database
from centsible.infrastructure.schema import SCHEMA_VERSION


class FileDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.path = Path(self._tempdir.name) / "nested" / "ledger.db"
        self.database = Database(self.path)

    def test_migrate_creates_the_file_and_records_the_version(self) -> None:
        self.assertEqual(self.database.migrate(), SCHEMA_VERSION)
        self.assertTrue(self.path.exists())
        self.assertEqual(self.database.schema_version(), SCHEMA_VERSION)

    def test_migrate_is_idempotent(self) -> None:
        self.database.migrate()
        self.assertEqual(self.database.migrate(), SCHEMA_VERSION)

    def test_expected_tables_exist(self) -> None:
        self.database.migrate()
        with self.database.connection() as connection:
            names = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        for table in (
            "accounts",
            "transactions",
            "postings",
            "budgets",
            "recurring_rules",
            "recurring_postings",
            "import_batches",
            "import_entries",
            "audit_log",
            "meta",
        ):
            self.assertIn(table, names)

    def test_foreign_keys_are_enforced(self) -> None:
        self.database.migrate()
        with self.database.connection() as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO postings (transaction_id, position, account_code, "
                    "amount_minor, memo) VALUES (1, 0, 'NOPE', 100, '')"
                )

    def test_postings_reject_zero_amount_by_constraint(self) -> None:
        self.database.migrate()
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO accounts (code, name, type, created_at) "
                "VALUES ('1000', 'Cash', 'asset', '2025-01-01T00:00:00+00:00')"
            )
            connection.execute(
                "INSERT INTO transactions (date, description, created_at) "
                "VALUES ('2025-01-01', 'x', '2025-01-01T00:00:00+00:00')"
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO postings (transaction_id, position, account_code, "
                    "amount_minor) VALUES (1, 0, '1000', 0)"
                )

    def test_integrity_check_is_clean(self) -> None:
        self.database.migrate()
        self.assertEqual(self.database.integrity_check(), [])

    def test_describe_reports_size_and_version(self) -> None:
        self.database.migrate()
        info = self.database.describe()
        self.assertEqual(info["schema_version"], SCHEMA_VERSION)
        self.assertTrue(info["exists"])
        self.assertGreater(info["size_bytes"], 0)

    def test_backup_creates_a_readable_copy(self) -> None:
        self.database.migrate()
        target = Path(self._tempdir.name) / "backup.db"
        self.database.backup_to(target)
        self.assertTrue(target.exists())
        copy = Database(target)
        self.assertEqual(copy.schema_version(), SCHEMA_VERSION)

    def test_newer_schema_is_refused(self) -> None:
        self.database.migrate()
        with self.database.connection() as connection:
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        with self.assertRaises(DataError):
            self.database.migrate()


class MemoryDatabaseTests(unittest.TestCase):
    def test_schema_survives_across_connections(self) -> None:
        database = Database(":memory:")
        self.addCleanup(database.close)
        self.assertTrue(database.is_memory)
        self.assertEqual(database.migrate(), SCHEMA_VERSION)

        with database.connection() as connection:
            connection.execute(
                "INSERT INTO meta (key, value) VALUES ('unit', 'test')"
            )

        with database.connection() as connection:
            row = connection.execute(
                "SELECT value FROM meta WHERE key = 'unit'"
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["value"], "test")

    def test_two_memory_databases_are_isolated(self) -> None:
        first = Database(":memory:")
        second = Database(":memory:")
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.migrate()
        second.migrate()
        with first.connection() as connection:
            connection.execute("INSERT INTO meta (key, value) VALUES ('a', '1')")
        with second.connection() as connection:
            count = connection.execute("SELECT COUNT(*) AS n FROM meta").fetchone()["n"]
        self.assertEqual(count, 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
