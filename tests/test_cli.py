"""End-to-end tests for the command line interface.

Every test drives ``main()`` exactly like a user would, with ``--db`` pointing at
a throw-away file, and asserts on exit codes and output.  That is the cheapest
way to keep the CLI contract (including the documented exit codes) honest.

命令行接口的端到端测试。

每个测试都完全按用户的方式调用 ``main()``，把 ``--db`` 指向一个临时文件，并断言
退出码与输出。这是让 CLI 契约（包括文档中写明的退出码）保持可信的最省力方式。
"""

from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from decimal import Decimal
from pathlib import Path

from centsible.interface.cli.main import main
from centsible.infrastructure.csvio import read_csv_rows, write_csv


class CliTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.directory = Path(self._tempdir.name)
        self.db = self.directory / "ledger.db"
        self.today = date.today().isoformat()

    # ------------------------------------------------------------------ tools
    # ------------------------------------------------------------------ 工具
    def run_cli(self, *arguments: str, db: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--db", str(db or self.db), *arguments])
        return code, out.getvalue(), err.getvalue()

    def init(self) -> None:
        code, _out, err = self.run_cli("init")
        self.assertEqual(code, 0, err)

    def add_transaction(self, description: str = "Office rent", amount: str = "1200") -> int:
        code, out, err = self.run_cli(
            "tx",
            "add",
            "--date",
            self.today,
            "--desc",
            description,
            "--debit",
            f"5100={amount}",
            "--credit",
            f"1010={amount}",
        )
        self.assertEqual(code, 0, err)
        return int(out.split("#")[1].split()[0])

    def json_cli(self, *arguments: str) -> tuple[int, dict | list]:
        code, out, err = self.run_cli("--json", *arguments)
        self.assertEqual(code, 0, err)
        return code, json.loads(out)


class BasicCliTests(CliTestCase):
    def test_no_command_prints_help(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main([])
        self.assertEqual(code, 0)
        self.assertIn("usage: centsible", out.getvalue())

    def test_version_exits_cleanly(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            main(["--version"])
        self.assertEqual(caught.exception.code, 0)

    def test_unknown_command_is_a_usage_error(self) -> None:
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["bogus"])
        self.assertEqual(caught.exception.code, 2)

    def test_init_creates_the_database_and_the_chart(self) -> None:
        code, out, err = self.run_cli("init")
        self.assertEqual(code, 0, err)
        self.assertTrue(self.db.exists())
        self.assertIn("Ledger ready at", out)
        _code, payload = self.json_cli("account", "list")
        self.assertEqual(payload["count"], 23)

    def test_init_is_idempotent(self) -> None:
        self.init()
        code, out, err = self.run_cli("init")
        self.assertEqual(code, 0, err)
        self.assertIn("already initialised", out)
        self.assertIn("Nothing to do", out)
        _code, payload = self.json_cli("account", "list")
        self.assertEqual(payload["count"], 23)

    def test_init_adds_missing_accounts_without_touching_existing_ones(self) -> None:
        code, _out, err = self.run_cli("init", "--chart", "empty")
        self.assertEqual(code, 0, err)
        self.run_cli("account", "add", "CUSTOM", "--name", "Custom", "--type", "asset")

        code, out, err = self.run_cli("init")
        self.assertEqual(code, 0, err)
        self.assertIn("accounts created", out)

        _code, payload = self.json_cli("account", "list")
        codes = {account["code"] for account in payload["accounts"]}
        self.assertIn("CUSTOM", codes)
        self.assertIn("1000", codes)
        self.assertEqual(len(codes), 24)

    def test_init_cannot_change_the_currency_of_an_existing_ledger(self) -> None:
        self.init()
        code, _out, err = self.run_cli("init", "--currency", "USD")
        self.assertEqual(code, 4)
        self.assertIn("cannot be changed", err)
        self.assertIn("CNY", err)

    def test_init_can_choose_the_currency_of_a_new_ledger(self) -> None:
        code, out, err = self.run_cli("init", "--currency", "JPY")
        self.assertEqual(code, 0, err)
        _code, payload = self.json_cli("db", "info")
        self.assertEqual(payload["ledger"]["ledger_currency"], "JPY")

    def test_config_show_does_not_touch_the_database(self) -> None:
        code, out, err = self.run_cli("config", "show")
        self.assertEqual(code, 0, err)
        self.assertFalse(self.db.exists())
        self.assertIn("ledger.currency", out)

    def test_config_show_json(self) -> None:
        _code, payload = self.json_cli("config", "show")
        self.assertEqual(payload["ledger"]["currency"], "CNY")

    def test_db_check_is_clean_on_a_fresh_ledger(self) -> None:
        self.init()
        code, out, _err = self.run_cli("db", "check")
        self.assertEqual(code, 0)
        self.assertIn("OK", out)
        self.assertIn("Every transaction balances on its own", out)

    def test_db_check_catches_an_unbalanced_entry_the_totals_hide(self) -> None:
        self.init()
        self.add_transaction()
        self.add_transaction(description="Second rent", amount="500")

        # Move five minor units from one entry to another: the journal total still
        # balances, so only a per-entry check can notice.
        # 把五个最小货币单位从一张凭证挪到另一张：账簿合计依然平衡，因此只有逐张
        # 凭证的校验才能发现。
        connection = sqlite3.connect(str(self.db))
        try:
            rows = connection.execute(
                "SELECT id, transaction_id FROM postings ORDER BY id"
            ).fetchall()
            donor = rows[0]
            receiver = next(row for row in rows if row[1] != donor[1])
            connection.execute(
                "UPDATE postings SET amount_minor = amount_minor + 5 WHERE id = ?",
                (donor[0],),
            )
            connection.execute(
                "UPDATE postings SET amount_minor = amount_minor - 5 WHERE id = ?",
                (receiver[0],),
            )
            connection.commit()
        finally:
            connection.close()

        code, out, _err = self.run_cli("--json", "db", "check")
        self.assertEqual(code, 5)
        payload = json.loads(out)
        self.assertTrue(payload["balanced"])
        self.assertEqual(len(payload["unbalanced_transactions"]), 2)
        self.assertIn("do not sum to zero", out)


class DatabaseCliTests(CliTestCase):
    """Backup and restore round trip at the command line.

    命令行层的备份与恢复闭环。"""

    def backup_path(self) -> Path:
        return self.directory / "backup.db"

    def test_restore_requires_force_when_the_ledger_has_data(self) -> None:
        self.init()
        self.add_transaction()
        backup = self.backup_path()
        code, _out, err = self.run_cli("db", "backup", "--out", str(backup))
        self.assertEqual(code, 0, err)

        code, _out, err = self.run_cli("db", "restore", str(backup))
        self.assertEqual(code, 4)
        self.assertIn("--force", err)

    def test_restore_reports_a_missing_backup_before_the_conflict(self) -> None:
        self.init()
        self.add_transaction()
        code, _out, err = self.run_cli(
            "db", "restore", str(self.directory / "ghost.db")
        )
        self.assertEqual(code, 3)
        self.assertIn("not found", err)

    def test_restore_reports_a_corrupt_backup_before_the_conflict(self) -> None:
        self.init()
        self.add_transaction()
        junk = self.directory / "junk-restore.db"
        junk.write_bytes(b"not a sqlite database, not at all")
        code, _out, err = self.run_cli("db", "restore", str(junk))
        self.assertEqual(code, 5)
        self.assertIn("readable SQLite", err)

    def test_restore_replaces_the_ledger(self) -> None:
        self.init()
        self.add_transaction(description="Keep me")
        backup = self.backup_path()
        code, _out, err = self.run_cli("db", "backup", "--out", str(backup))
        self.assertEqual(code, 0, err)
        self.add_transaction(description="Throwaway", amount="500")

        code, out, err = self.run_cli("db", "restore", str(backup), "--force")
        self.assertEqual(code, 0, err)
        self.assertIn("Database restored", out)

        _code, payload = self.json_cli("tx", "list")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["transactions"][0]["description"], "Keep me")
        code, _out, err = self.run_cli("db", "check")
        self.assertEqual(code, 0, err)

    def test_restore_records_an_audit_entry(self) -> None:
        self.init()
        self.add_transaction()
        backup = self.backup_path()
        self.run_cli("db", "backup", "--out", str(backup))
        code, out, err = self.run_cli("db", "restore", str(backup), "--force")
        self.assertEqual(code, 0, err)
        code, out, _err = self.run_cli("journal", "audit")
        self.assertEqual(code, 0)
        self.assertIn("database.restore", out)

    def test_restore_refuses_a_foreign_file(self) -> None:
        self.init()
        junk = self.directory / "junk.db"
        junk.write_bytes(b"this is not a sqlite database at all, really")
        code, _out, err = self.run_cli("db", "restore", str(junk))
        self.assertEqual(code, 5)
        self.assertIn("readable SQLite", err)

    def test_restore_refuses_an_empty_sqlite_file(self) -> None:
        self.init()
        foreign = self.directory / "foreign.db"
        connection = sqlite3.connect(str(foreign))
        try:
            connection.execute("CREATE TABLE something_else (id INTEGER)")
            connection.commit()
        finally:
            connection.close()
        code, _out, err = self.run_cli("db", "restore", str(foreign))
        self.assertEqual(code, 5)
        self.assertIn("not a Centsible database", err)


class AccountCliTests(CliTestCase):
    def test_add_and_list(self) -> None:
        self.init()
        code, out, err = self.run_cli(
            "account", "add", "cash", "--name", "Petty cash", "--type", "asset"
        )
        self.assertEqual(code, 0, err)
        self.assertIn("CASH", out)

        code, payload = self.json_cli("account", "list")
        self.assertEqual(code, 0)
        self.assertIn("CASH", [account["code"] for account in payload["accounts"]])

    def test_duplicate_code_is_a_conflict(self) -> None:
        self.init()
        code, _out, err = self.run_cli("account", "add", "1000", "--name", "X", "--type", "asset")
        self.assertEqual(code, 4)
        self.assertIn("already exists", err)

    def test_invalid_type_is_a_validation_error(self) -> None:
        self.init()
        code, _out, err = self.run_cli("account", "add", "X", "--name", "X", "--type", "profit")
        self.assertEqual(code, 2)
        self.assertIn("not an account type", err)

    def test_tree_view(self) -> None:
        self.init()
        self.run_cli("account", "add", "HEAD", "--name", "Heading", "--type", "asset")
        self.run_cli("account", "add", "LEAF", "--name", "Leaf", "--type", "asset", "--parent", "HEAD")
        code, out, err = self.run_cli("account", "list", "--tree")
        self.assertEqual(code, 0, err)
        self.assertIn("LEAF", out)
        self.assertIn("  Leaf", out)

    def test_show_includes_balance(self) -> None:
        self.init()
        self.add_transaction()
        code, out, err = self.run_cli("account", "show", "5100")
        self.assertEqual(code, 0, err)
        self.assertIn("1,200.00", out)

    def test_show_unknown_account_is_not_found(self) -> None:
        self.init()
        code, _out, err = self.run_cli("account", "show", "9999")
        self.assertEqual(code, 3)
        self.assertIn("does not exist", err)

    def test_update_requires_a_change(self) -> None:
        self.init()
        code, _out, err = self.run_cli("account", "update", "1000")
        self.assertEqual(code, 2)
        self.assertIn("nothing to update", err)

    def test_archive_and_restore(self) -> None:
        self.init()
        self.run_cli("account", "add", "TEMP", "--name", "Temp", "--type", "asset")
        code, out, _err = self.run_cli("account", "archive", "TEMP")
        self.assertEqual(code, 0)
        self.assertIn("Archived", out)
        code, out, _err = self.run_cli("account", "restore", "TEMP")
        self.assertEqual(code, 0)
        self.assertIn("Restored", out)

    def test_delete_unused_account(self) -> None:
        self.init()
        self.run_cli("account", "add", "TEMP", "--name", "Temp", "--type", "asset")
        code, out, err = self.run_cli("account", "delete", "TEMP")
        self.assertEqual(code, 0, err)
        self.assertIn("Deleted", out)


class TransactionCliTests(CliTestCase):
    def test_add_list_and_show(self) -> None:
        self.init()
        transaction_id = self.add_transaction()
        self.assertEqual(transaction_id, 1)

        code, payload = self.json_cli("tx", "list")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["transactions"][0]["description"], "Office rent")

        code, out, err = self.run_cli("tx", "show", str(transaction_id))
        self.assertEqual(code, 0, err)
        self.assertIn("1,200.00", out)

    def test_auto_balanced_posting(self) -> None:
        self.init()
        code, _out, err = self.run_cli(
            "tx", "add", "--date", self.today, "--desc", "Split sale",
            "--debit", "1010=4520", "--credit", "4000=4000", "--credit", "2200=",
        )
        self.assertEqual(code, 0, err)
        _code, payload = self.json_cli("tx", "list")
        amounts = [posting["amount"] for posting in payload["transactions"][0]["postings"]]
        self.assertEqual(amounts, ["4520.00", "-4000.00", "-520.00"])

    def test_list_can_filter_by_status(self) -> None:
        self.init()
        posted = self.add_transaction()
        voided = self.add_transaction(description="Wrong month")
        code, _out, err = self.run_cli(
            "tx", "void", str(voided), "--reason", "duplicated a manual entry"
        )
        self.assertEqual(code, 0, err)

        _code, payload = self.json_cli("tx", "list")
        self.assertEqual(payload["count"], 2)
        self.assertIsNone(payload["status"])

        _code, payload = self.json_cli("tx", "list", "--status", "void")
        self.assertEqual([item["id"] for item in payload["transactions"]], [voided])

        _code, payload = self.json_cli("tx", "list", "--status", "posted")
        self.assertEqual([item["id"] for item in payload["transactions"]], [posted])

    def test_unbalanced_entry_exits_with_code_2(self) -> None:
        self.init()
        code, _out, err = self.run_cli(
            "tx", "add", "--date", self.today, "--desc", "Bad",
            "--debit", "5100=100", "--credit", "1010=90",
        )
        self.assertEqual(code, 2)
        self.assertIn("do not balance", err)

    def test_unknown_account_exits_with_code_3(self) -> None:
        self.init()
        code, _out, err = self.run_cli(
            "tx", "add", "--date", self.today, "--desc", "Bad",
            "--debit", "9999=100", "--credit", "1010=100",
        )
        self.assertEqual(code, 3)
        self.assertIn("9999", err)

    def test_future_date_is_refused_then_allowed(self) -> None:
        self.init()
        future = f"{date.today().year + 1}-01-01"
        code, _out, err = self.run_cli(
            "tx", "add", "--date", future, "--desc", "Future",
            "--debit", "5100=1", "--credit", "1010=1",
        )
        self.assertEqual(code, 2)
        self.assertIn("future", err)

        code, _out, err = self.run_cli(
            "tx", "add", "--date", future, "--desc", "Future", "--allow-future",
            "--debit", "5100=1", "--credit", "1010=1",
        )
        self.assertEqual(code, 0, err)

    def test_dry_run_writes_nothing(self) -> None:
        self.init()
        code, out, err = self.run_cli(
            "tx", "add", "--date", self.today, "--desc", "Preview", "--dry-run",
            "--debit", "5100=10", "--credit", "1010=10",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Dry run", out)
        _code, payload = self.json_cli("tx", "list")
        self.assertEqual(payload["count"], 0)

    def test_void_flow(self) -> None:
        self.init()
        transaction_id = self.add_transaction()
        code, out, err = self.run_cli("tx", "void", str(transaction_id), "--reason", "duplicate")
        self.assertEqual(code, 0, err)
        self.assertIn("VOID", out)

        code, _out, err = self.run_cli("tx", "void", str(transaction_id), "--reason", "again")
        self.assertEqual(code, 4)
        self.assertIn("already void", err)

        _code, payload = self.json_cli("report", "trial-balance")
        self.assertTrue(payload["balanced"])
        self.assertEqual(payload["rows"], [])

    def test_void_requires_a_reason(self) -> None:
        self.init()
        transaction_id = self.add_transaction()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["--db", str(self.db), "tx", "void", str(transaction_id)])

    def test_restore_undoes_a_void(self) -> None:
        self.init()
        transaction_id = self.add_transaction()
        self.run_cli("tx", "void", str(transaction_id), "--reason", "typed the wrong id")

        code, out, err = self.run_cli("tx", "restore", str(transaction_id))
        self.assertEqual(code, 0, err)
        self.assertIn("posted again", out)

        _code, payload = self.json_cli("report", "trial-balance")
        self.assertTrue(payload["balanced"])
        self.assertEqual(len(payload["rows"]), 2)

    def test_restore_of_a_live_transaction_is_a_conflict(self) -> None:
        self.init()
        transaction_id = self.add_transaction()
        code, _out, err = self.run_cli("tx", "restore", str(transaction_id))
        self.assertEqual(code, 4)
        self.assertIn("not void", err)

    def test_list_filters(self) -> None:
        self.init()
        self.add_transaction("Office rent")
        self.add_transaction("Bank fees", "12")
        _code, payload = self.json_cli("tx", "list", "--text", "rent")
        self.assertEqual(payload["count"], 1)
        _code, payload = self.json_cli("tx", "list", "--account", "5100")
        self.assertEqual(payload["count"], 2)
        _code, payload = self.json_cli("tx", "list", "--limit", "1")
        self.assertEqual(payload["count"], 1)


class ReportCliTests(CliTestCase):
    def test_trial_balance_json(self) -> None:
        self.init()
        self.add_transaction()
        _code, payload = self.json_cli("report", "trial-balance")
        self.assertEqual(payload["kind"], "trial-balance")
        self.assertTrue(payload["balanced"])
        self.assertEqual(len(payload["rows"]), 2)

    def test_income_statement_renders_sections(self) -> None:
        self.init()
        self.add_transaction()
        code, out, err = self.run_cli("report", "income")
        self.assertEqual(code, 0, err)
        self.assertIn("Income statement", out)
        self.assertIn("Expenses", out)
        self.assertIn("Net income", out)

    def test_balance_sheet_renders_and_balances(self) -> None:
        self.init()
        self.add_transaction()
        code, out, err = self.run_cli("report", "balance-sheet")
        self.assertEqual(code, 0, err)
        self.assertIn("Balanced", out)

    def test_ledger_report(self) -> None:
        self.init()
        self.add_transaction()
        code, out, err = self.run_cli("report", "ledger", "5100")
        self.assertEqual(code, 0, err)
        self.assertIn("Opening balance", out)
        self.assertIn("Closing balance", out)

    def test_summary_report_json(self) -> None:
        self.init()
        self.add_transaction()
        _code, payload = self.json_cli("report", "summary", "--months", "2")
        self.assertEqual(payload["kind"], "activity")
        self.assertEqual(len(payload["months"]), 2)

    def test_report_with_explicit_month(self) -> None:
        self.init()
        self.add_transaction()
        month = self.today[:7]
        code, out, err = self.run_cli("report", "trial-balance", "--month", month)
        self.assertEqual(code, 0, err)
        self.assertIn(month, out)


class BudgetAndRecurringCliTests(CliTestCase):
    def test_budget_set_list_report(self) -> None:
        self.init()
        self.add_transaction()
        month = self.today[:7]
        code, out, err = self.run_cli(
            "budget", "set", "5100", "--month", month, "--amount", "1500"
        )
        self.assertEqual(code, 0, err)
        self.assertIn("1,500.00", out)

        code, out, err = self.run_cli("budget", "list")
        self.assertEqual(code, 0, err)
        self.assertIn("5100", out)

        code, out, err = self.run_cli("budget", "report", "--month", month)
        self.assertEqual(code, 0, err)
        self.assertIn("variance", out.lower())

    def test_budget_on_a_balance_sheet_account_is_refused(self) -> None:
        self.init()
        code, _out, err = self.run_cli(
            "budget", "set", "1000", "--month", self.today[:7], "--amount", "10"
        )
        self.assertEqual(code, 2)
        self.assertIn("income or expense", err)

    def test_recurring_lifecycle(self) -> None:
        self.init()
        code, out, err = self.run_cli(
            "recurring", "add", "Monthly rent", "--desc", "Office rent",
            "--frequency", "monthly", "--start", self.today,
            "--debit", "5100=1200", "--credit", "1010=1200",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("Monthly rent", out)

        code, out, err = self.run_cli("recurring", "run")
        self.assertEqual(code, 0, err)
        self.assertIn("Created 1 transaction", out)

        code, out, err = self.run_cli("recurring", "list")
        self.assertEqual(code, 0, err)
        self.assertIn("active", out)

    def test_recurring_run_dry_run(self) -> None:
        self.init()
        self.run_cli(
            "recurring", "add", "Rent", "--desc", "Rent", "--frequency", "monthly",
            "--start", self.today, "--debit", "5100=10", "--credit", "1010=10",
        )
        code, out, err = self.run_cli("recurring", "run", "--dry-run")
        self.assertEqual(code, 0, err)
        self.assertIn("Would create", out)
        _code, payload = self.json_cli("tx", "list")
        self.assertEqual(payload["count"], 0)


class JournalCliTests(CliTestCase):
    def test_lock_and_status(self) -> None:
        self.init()
        code, out, err = self.run_cli("journal", "lock", "--date", self.today)
        self.assertEqual(code, 0, err)
        self.assertIn("closed through", out)

        code, out, err = self.run_cli("journal", "status")
        self.assertEqual(code, 0, err)
        self.assertIn(self.today, out)

    def test_locked_period_rejects_postings(self) -> None:
        self.init()
        self.run_cli("journal", "lock", "--date", self.today)
        code, _out, err = self.run_cli(
            "tx", "add", "--date", self.today, "--desc", "Late",
            "--debit", "5100=1", "--credit", "1010=1",
        )
        self.assertEqual(code, 6)
        self.assertIn("closed period", err)

    def test_unlock_needs_force(self) -> None:
        self.init()
        self.run_cli("journal", "lock", "--date", self.today)
        code, _out, err = self.run_cli("journal", "unlock")
        self.assertEqual(code, 4)
        self.assertIn("--force", err)

        code, out, err = self.run_cli("journal", "unlock", "--force")
        self.assertEqual(code, 0, err)
        self.assertIn("open again", out)

    def test_lock_in_the_future_is_refused(self) -> None:
        self.init()
        future = f"{date.today().year + 1}-01-01"
        code, _out, err = self.run_cli("journal", "lock", "--date", future)
        self.assertEqual(code, 2)

    def test_audit_trail(self) -> None:
        self.init()
        self.add_transaction()
        code, out, err = self.run_cli("journal", "audit")
        self.assertEqual(code, 0, err)
        self.assertIn("transaction.record", out)


class ImportExportCliTests(CliTestCase):
    def statement(self) -> Path:
        path = self.directory / "statement.csv"
        write_csv(
            path,
            ("date", "amount", "description"),
            (
                (self.today, "-120.00", "Cloud hosting"),
                (self.today, "-30.50", "Coffee beans"),
            ),
        )
        return path

    def test_import_and_reimport(self) -> None:
        self.init()
        path = self.statement()
        code, out, err = self.run_cli(
            "import", "bank", str(path), "--account", "1010", "--counter-account", "5900"
        )
        self.assertEqual(code, 0, err)
        self.assertIn("imported", out)

        code, out, err = self.run_cli(
            "import", "bank", str(path), "--account", "1010", "--counter-account", "5900"
        )
        self.assertEqual(code, 0, err)
        self.assertIn("duplicates skipped", out)
        _code, payload = self.json_cli("tx", "list")
        self.assertEqual(payload["count"], 2)

    def test_import_batches(self) -> None:
        self.init()
        self.run_cli(
            "import", "bank", str(self.statement()),
            "--account", "1010", "--counter-account", "5900",
        )
        code, out, err = self.run_cli("import", "batches")
        self.assertEqual(code, 0, err)
        self.assertIn("statement.csv", out)

    def test_import_reports_repeated_lines_and_can_keep_them(self) -> None:
        self.init()
        path = self.directory / "duplicates.csv"
        write_csv(
            path,
            ("date", "amount", "description"),
            ((self.today, "-4.50", "Coffee"), (self.today, "-4.50", "Coffee")),
        )
        arguments = (
            "import", "bank", str(path), "--account", "1010", "--counter-account", "5900",
        )
        code, out, err = self.run_cli(*arguments)
        self.assertEqual(code, 0, err)
        # The skipped look-alike is named explicitly, not folded into "duplicates".
        # 被跳过的相似行会被明确点名，而不是混进“重复”里。
        self.assertIn("repeated lines skipped", out)
        self.assertIn("--keep-duplicate-lines", out)
        _code, payload = self.json_cli("tx", "list")
        self.assertEqual(payload["count"], 1)

        code, out, err = self.run_cli(*arguments, "--keep-duplicate-lines")
        self.assertEqual(code, 0, err)
        _code, payload = self.json_cli("tx", "list")
        self.assertEqual(payload["count"], 2)

    def test_import_with_match_rules(self) -> None:
        self.init()
        path = self.directory / "mixed.csv"
        write_csv(
            path,
            ("date", "amount", "description", "payee"),
            (
                (self.today, "-45.50", "Coffee beans", "Bean shop"),
                (self.today, "-1200.00", "Office rent", "Landlord"),
            ),
        )
        code, out, err = self.run_cli(
            "import", "bank", str(path), "--account", "1010", "--counter-account", "5900",
            "--match-rule", "coffee=5800", "--match-rule", "rent=5100",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("matched by rules", out)
        self.assertIn("coffee -> 5800", out)
        self.assertIn("rent -> 5100", out)
        _code, payload = self.json_cli("tx", "list")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["transactions"][0]["description"], "Office rent")

    def test_match_rule_without_an_account_is_a_usage_error(self) -> None:
        self.init()
        path = self.directory / "mixed.csv"
        write_csv(
            path,
            ("date", "amount", "description", "payee"),
            ((self.today, "-45.50", "Coffee beans", "Bean shop"),),
        )
        code, _out, err = self.run_cli(
            "import", "bank", str(path), "--account", "1010", "--counter-account", "5900",
            "--match-rule", "coffee",
        )
        self.assertEqual(code, 2)
        self.assertIn("PATTERN=ACCOUNT", err)

    def test_export_journal_and_backup(self) -> None:
        self.init()
        self.add_transaction()
        journal_path = self.directory / "journal.csv"
        code, out, err = self.run_cli("export", "journal", "--out", str(journal_path))
        self.assertEqual(code, 0, err)
        self.assertIn("Wrote 2 posting row", out)
        rows = read_csv_rows(journal_path)
        self.assertEqual(rows[0][0], "id")
        self.assertEqual(len(rows), 3)

        backup_path = self.directory / "backup.json"
        code, out, err = self.run_cli("export", "backup", "--out", str(backup_path))
        self.assertEqual(code, 0, err)
        payload = json.loads(backup_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["format"], "centsible-backup")
        self.assertEqual(len(payload["transactions"]), 1)

    def test_db_backup(self) -> None:
        self.init()
        target = self.directory / "copy.db"
        code, out, err = self.run_cli("db", "backup", "--out", str(target))
        self.assertEqual(code, 0, err)
        self.assertTrue(target.exists())
        self.assertIn("Backup written", out)


class OutputPathTests(CliTestCase):
    """An unusable output path is a clean error, never a traceback.

    不可用的输出路径必须给出清晰错误，绝不能是 traceback。"""

    def setUp(self) -> None:
        super().setUp()
        self.init()
        self.add_transaction()

    def test_journal_csv_into_a_directory(self) -> None:
        code, _out, err = self.run_cli(
            "export", "journal", "--out", str(self.directory)
        )
        self.assertEqual(code, 2)
        self.assertIn("cannot write", err)

    def test_json_backup_into_a_directory(self) -> None:
        code, _out, err = self.run_cli(
            "export", "backup", "--out", str(self.directory)
        )
        self.assertEqual(code, 2)
        self.assertIn("cannot write", err)

    def test_database_backup_into_a_directory(self) -> None:
        code, _out, err = self.run_cli("db", "backup", "--out", str(self.directory))
        self.assertEqual(code, 2)
        self.assertIn("cannot write", err)


class DemoAndIntegrationTests(CliTestCase):
    def test_demo_then_reports(self) -> None:
        code, out, err = self.run_cli("demo")
        self.assertEqual(code, 0, err)
        self.assertIn("Demo ledger ready", out)

        _code, payload = self.json_cli("report", "balance-sheet")
        self.assertTrue(payload["balanced"])

        _code, payload = self.json_cli("report", "summary", "--months", "3")
        self.assertEqual(len(payload["months"]), 3)

    def test_demo_twice_is_a_conflict(self) -> None:
        self.run_cli("demo")
        code, _out, err = self.run_cli("demo")
        self.assertEqual(code, 4)
        self.assertIn("--fresh", err)

    def test_demo_fresh_wipes_previous_data(self) -> None:
        self.run_cli("demo")
        code, _out, err = self.run_cli("demo", "--fresh")
        self.assertEqual(code, 0, err)
        _code, payload = self.json_cli("report", "trial-balance")
        self.assertTrue(payload["balanced"])

    def test_json_flag_is_ignored_by_text_commands(self) -> None:
        self.init()
        code, out, err = self.run_cli("journal", "status")
        self.assertEqual(code, 0, err)
        self.assertIn("Journal status", out)


class ExcelCliTests(CliTestCase):
    """Excel import/export exercised end to end through ``main``.

    通过 ``main`` 端到端地演练 Excel 导入/导出。"""

    def xlsx_statement(self, sheet_name: str = "流水") -> Path:
        from centsible.application.workbook import Column, ColumnKind, Sheet, Workbook
        from centsible.infrastructure.xlsx import write_workbook

        workbook = Workbook(
            title="bank export",
            sheets=(
                Sheet(
                    title=sheet_name,
                    columns=(
                        Column("date", ColumnKind.DATE),
                        Column("amount", ColumnKind.MONEY),
                        Column("description", ColumnKind.TEXT),
                        Column("reference", ColumnKind.TEXT),
                    ),
                    rows=(
                        (date.today(), Decimal("-1299.00"), "Cloud hosting", "INV-1001"),
                        (date.today(), Decimal("2500.00"), "Client payment", "PMT-88"),
                    ),
                ),
            ),
        )
        path = self.directory / "bank.xlsx"
        write_workbook(path, workbook)
        return path

    def test_export_workbook_command(self) -> None:
        self.init()
        self.add_transaction()
        out = self.directory / "pack.xlsx"
        code, text, err = self.run_cli("export", "workbook", "--out", str(out))
        self.assertEqual(code, 0, err)
        self.assertIn("9 sheet(s)", text)
        self.assertTrue(out.exists())

        from centsible.infrastructure.xlsx import list_sheets
        self.assertIn("Trial balance", list_sheets(out))

    def test_export_workbook_json(self) -> None:
        self.init()
        out = self.directory / "pack.xlsx"
        _code, payload = self.json_cli("export", "workbook", "--out", str(out))
        self.assertEqual(len(payload["sheets"]), 9)

    def test_export_journal_auto_switches_to_excel(self) -> None:
        self.init()
        self.add_transaction()
        out = self.directory / "journal.xlsx"
        code, text, err = self.run_cli("export", "journal", "--out", str(out))
        self.assertEqual(code, 0, err)
        self.assertIn("Excel workbook", text)

        from centsible.infrastructure.xlsx import read_worksheet
        sheet = read_worksheet(out)
        self.assertEqual(sheet.rows[2], (
            "id", "date", "description", "payee", "reference",
            "tags", "status", "account", "amount", "memo",
        ))
        self.assertEqual(sheet.rows[3][0], 1)
        self.assertEqual(sheet.rows[3][8], Decimal("1200.00"))

    def test_import_bank_from_excel(self) -> None:
        self.init()
        path = self.xlsx_statement()
        code, text, err = self.run_cli(
            "import", "bank", str(path),
            "--account", "1010", "--counter-account", "5900",
            "--sheet", "流水",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("imported", text)

        _code, payload = self.json_cli("tx", "list")
        self.assertEqual(payload["count"], 2)

    def test_excel_import_is_idempotent(self) -> None:
        self.init()
        path = self.xlsx_statement()
        self.run_cli(
            "import", "bank", str(path),
            "--account", "1010", "--counter-account", "5900",
        )
        code, text, err = self.run_cli(
            "import", "bank", str(path),
            "--account", "1010", "--counter-account", "5900",
        )
        self.assertEqual(code, 0, err)
        self.assertIn("duplicates skipped", text)
        _code, payload = self.json_cli("tx", "list")
        self.assertEqual(payload["count"], 2)

    def test_import_rejects_unsupported_file_types(self) -> None:
        self.init()
        bogus = self.directory / "statement.pdf"
        bogus.write_bytes(b"%PDF-1.4")
        code, _out, err = self.run_cli(
            "import", "bank", str(bogus),
            "--account", "1010", "--counter-account", "5900",
        )
        self.assertEqual(code, 2)
        self.assertIn("unsupported statement file type", err)

    def test_import_from_a_missing_sheet_is_a_clear_error(self) -> None:
        self.init()
        path = self.xlsx_statement()
        code, _out, err = self.run_cli(
            "import", "bank", str(path),
            "--account", "1010", "--counter-account", "5900",
            "--sheet", "不存在的表",
        )
        self.assertEqual(code, 2)
        self.assertIn("available sheets", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
