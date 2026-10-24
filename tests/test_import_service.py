"""Tests for statement import: parsing, balancing and idempotency.

对账单导入的测试：解析、平衡与幂等性。"""

from __future__ import annotations

import unittest
from decimal import Decimal
from pathlib import Path

from centsible.application.import_service import StatementImportRequest
from centsible.application.ports import MatchRule, StatementMapping
from centsible.errors import NotFoundError, ValidationError
from tests.support import LedgerTestCase, write_statement

HEADER = ("date", "amount", "description", "reference")
ROWS = (
    ("2025-03-01", "-1299.00", "Cloud hosting", "INV-1001"),
    ("2025-03-02", "-45.50", "Coffee beans", "INV-1002"),
    ("2025-03-03", "2500.00", "Client payment", "PMT-88"),
)
DEFAULT_MAPPING = StatementMapping(
    date_column="date",
    amount_column="amount",
    description_column="description",
    id_column="reference",
)


class ImportServiceTests(LedgerTestCase):
    def statement(self, rows=ROWS, header=HEADER, name: str = "statement.csv") -> Path:
        return write_statement(
            Path(self._tempdir.name) / name, rows, header=header
        )

    def do_import(self, path: Path, **kwargs):
        mapping = kwargs.pop("mapping", DEFAULT_MAPPING)
        request = StatementImportRequest(
            path=path,
            account_code=kwargs.pop("account_code", "1010"),
            counter_account=kwargs.pop("counter_account", "5900"),
            mapping=mapping,
            **kwargs,
        )
        return self.services.imports.import_statement(request)

    def test_import_creates_balanced_transactions(self) -> None:
        result = self.do_import(self.statement())
        self.assertEqual(result.rows_read, 3)
        self.assertEqual(result.imported, 3)
        self.assertEqual(result.duplicates, 0)
        self.assertEqual(result.failed, 0)
        self.assertIsNotNone(result.batch_id)
        self.assertEqual(len(result.transaction_ids), 3)

        transactions = self.services.ledger.search(limit=10)
        self.assertEqual(len(transactions), 3)
        self.assertEqual([tx.date.isoformat() for tx in transactions],
                         ["2025-03-03", "2025-03-02", "2025-03-01"])
        self.assertTrue(all(tx.import_batch == result.batch_id for tx in transactions))

        # 1010 receives the signed amount, 5900 (expense) the opposite.
        # 1010 记入带符号的金额，5900（费用）则记入相反方向。
        self.assertEqual(
            self.balance("5900"),
            Decimal("1299") + Decimal("45.50") - Decimal("2500"),
        )

    def test_reimport_is_idempotent(self) -> None:
        path = self.statement()
        self.do_import(path)
        second = self.do_import(path)
        self.assertEqual(second.imported, 0)
        self.assertEqual(second.duplicates, 3)
        self.assertEqual(second.failed, 0)
        self.assertEqual(self.services.ledger.stats()["transactions"], 3)
        self.assertEqual(len(self.services.imports.batches()), 2)

    def test_fingerprints_are_scoped_per_account(self) -> None:
        path = self.statement()
        self.do_import(path)
        other = self.do_import(path, account_code="1000", counter_account="5100")
        self.assertEqual(other.imported, 3)
        self.assertEqual(other.duplicates, 0)

    def test_a_bad_row_is_reported_and_the_rest_is_imported(self) -> None:
        path = self.statement(
            rows=(
                ("2025-03-01", "-10.00", "Fine", "A"),
                ("not-a-date", "-20.00", "Broken date", "B"),
                ("2025-03-03", "abc", "Broken amount", "C"),
                ("2025-03-04", "-30.00", "Fine again", "D"),
            )
        )
        result = self.do_import(path)
        self.assertEqual(result.imported, 2)
        self.assertEqual(result.failed, 2)
        self.assertEqual([issue.line_number for issue in result.issues], [3, 4])
        self.assertEqual(self.services.ledger.stats()["transactions"], 2)

    def test_amounts_with_too_many_decimals_are_rejected(self) -> None:
        path = self.statement(rows=(("2025-03-01", "-10.001", "Too precise", "A"),))
        result = self.do_import(path)
        self.assertEqual(result.imported, 0)
        self.assertEqual(result.failed, 1)
        self.assertIn("decimal places", result.issues[0].message)

    def test_dry_run_does_not_write(self) -> None:
        result = self.do_import(self.statement(), dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.imported, 3)
        self.assertIsNone(result.batch_id)
        self.assertEqual(result.transaction_ids, ())
        self.assertEqual(self.services.ledger.stats()["transactions"], 0)

    def test_headerless_file_with_index_columns(self) -> None:
        path = self.statement(rows=ROWS, header=None)
        mapping = StatementMapping(
            date_column="0",
            amount_column="1",
            description_column="2",
            id_column="3",
            has_header=False,
        )
        result = self.do_import(path, mapping=mapping)
        self.assertEqual(result.imported, 3)
        self.assertEqual(result.rows_read, 3)

    def test_split_debit_and_credit_columns(self) -> None:
        path = self.statement(
            rows=(
                ("2025-03-01", "100.00", "", "Withdrawal"),
                ("2025-03-02", "", "300.00", "Deposit"),
            ),
            header=("date", "out", "in", "description"),
        )
        mapping = StatementMapping(
            date_column="date",
            amount_column=None,
            debit_column="out",
            credit_column="in",
            description_column="description",
        )
        result = self.do_import(path, mapping=mapping)
        self.assertEqual(result.imported, 2)
        self.assertEqual(self.balance("1010"), 200)

    def test_amount_sign_can_be_flipped(self) -> None:
        path = self.statement(rows=(("2025-03-01", "100.00", "Sign flip", "A"),))
        mapping = StatementMapping(
            date_column="date",
            amount_column="amount",
            description_column="description",
            amount_sign=-1,
        )
        result = self.do_import(path, mapping=mapping)
        self.assertEqual(result.imported, 1)
        self.assertEqual(self.balance("1010"), -100)

    def test_custom_date_format(self) -> None:
        path = self.statement(rows=(("01/03/2025", "-10.00", "Custom", "A"),))
        mapping = StatementMapping(
            date_column="date",
            amount_column="amount",
            description_column="description",
            date_format="%d/%m/%Y",
        )
        result = self.do_import(path, mapping=mapping)
        self.assertEqual(result.imported, 1)
        self.assertEqual(
            self.services.ledger.search(limit=1)[0].date.isoformat(), "2025-03-01"
        )

    def test_unknown_account(self) -> None:
        with self.assertRaises(NotFoundError):
            self.do_import(self.statement(), account_code="9999")

    def test_same_account_and_counter_account_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            self.do_import(self.statement(), counter_account="1010")

    def test_missing_file(self) -> None:
        with self.assertRaises(NotFoundError):
            self.do_import(Path(self._tempdir.name) / "nope.csv")

    def test_repeated_lines_inside_one_file_are_skipped_and_reported(self) -> None:
        path = self.statement(
            rows=(
                ("2025-03-01", "-10.00", "Same", "A"),
                ("2025-03-01", "-10.00", "Same", "A"),
            )
        )
        result = self.do_import(path)
        self.assertEqual(result.imported, 1)
        # A line that repeats an earlier line of the same file is *not* a row
        # that is already in the ledger; the two are counted apart so the user
        # can tell a duplicated export from a re-run.
        # 文件内重复的行并不等于“账本中已存在的行”，两者分开计数，用户才能分辨
        # 是导出文件重复还是自己又跑了一遍。
        self.assertEqual(result.duplicates, 0)
        self.assertEqual(result.repeated, 1)
        self.assertEqual(result.failed, 0)

    def test_keep_duplicate_lines_imports_both_copies(self) -> None:
        path = self.statement(
            rows=(
                ("2025-03-01", "-10.00", "Same", "A"),
                ("2025-03-01", "-10.00", "Same", "A"),
            )
        )
        result = self.do_import(path, keep_duplicate_lines=True)
        self.assertEqual(result.imported, 2)
        self.assertEqual(result.repeated, 0)
        self.assertEqual(len(result.transaction_ids), 2)
        self.assertEqual(self.balance("5900"), Decimal("20"))

    def test_keep_duplicate_lines_is_still_idempotent(self) -> None:
        path = self.statement(
            rows=(
                ("2025-03-01", "-10.00", "Same", "A"),
                ("2025-03-01", "-10.00", "Same", "A"),
            )
        )
        self.do_import(path, keep_duplicate_lines=True)
        again = self.do_import(path, keep_duplicate_lines=True)
        self.assertEqual(again.imported, 0)
        self.assertEqual(again.duplicates, 2)
        self.assertEqual(self.services.ledger.stats()["transactions"], 2)

    def test_look_alike_rows_without_a_reference_column(self) -> None:
        mapping = StatementMapping(
            date_column="date",
            amount_column="amount",
            description_column="description",
        )
        path = self.statement(
            rows=(
                ("2025-03-02", "-4.50", "Coffee"),
                ("2025-03-02", "-4.50", "Coffee"),
            ),
            header=("date", "amount", "description"),
        )
        skipped = self.do_import(path, mapping=mapping)
        self.assertEqual((skipped.imported, skipped.repeated), (1, 1))

        kept = self.do_import(path, mapping=mapping, keep_duplicate_lines=True)
        # Only the copy that was skipped before is new; the first one is already
        # in the ledger, so the flag never duplicates what is already there.
        # 只有此前被跳过的那一行是新的；第一行已在账本里，因此这个开关不会重复
        # 导入账本中已有的内容。
        self.assertEqual(
            (kept.imported, kept.duplicates, kept.repeated), (1, 1, 0)
        )
        self.assertEqual(self.services.ledger.stats()["transactions"], 2)

    def test_import_into_a_closed_period_fails_per_row(self) -> None:
        self.services.ledger.set_lock_date(self.today)
        result = self.do_import(self.statement())
        self.assertEqual(result.imported, 0)
        self.assertEqual(result.failed, 3)
        self.assertIn("closed period", result.issues[0].message)

    def test_batch_metadata_is_stored(self) -> None:
        result = self.do_import(self.statement())
        batches = self.services.imports.batches()
        self.assertEqual(len(batches), 1)
        batch = batches[0]
        self.assertEqual(batch.id, result.batch_id)
        self.assertEqual(batch.filename, "statement.csv")
        self.assertEqual(batch.rows_read, 3)
        self.assertEqual(batch.rows_imported, 3)
        self.assertEqual(batch.rows_skipped, 0)

    def test_result_json_shape(self) -> None:
        payload = self.do_import(self.statement()).to_dict()
        self.assertEqual(payload["kind"], "statement-import")
        self.assertEqual(payload["imported"], 3)
        self.assertTrue(payload["transaction_ids"])


class ExcelImportTests(LedgerTestCase):
    """The same service, fed with an .xlsx file instead of CSV.

    同一个服务，只是输入换成了 .xlsx 文件而不是 CSV。"""

    def statement(
        self,
        rows=ROWS,
        header=HEADER,
        name: str = "statement.xlsx",
        sheet_name: str = "流水",
    ) -> Path:
        from centsible.application.workbook import Column, ColumnKind, Sheet, Workbook
        from centsible.infrastructure.xlsx import write_workbook

        kinds = {
            "date": ColumnKind.DATE,
            "amount": ColumnKind.MONEY,
            "description": ColumnKind.TEXT,
            "reference": ColumnKind.TEXT,
        }
        columns = tuple(
            Column(field, kinds.get(field, ColumnKind.TEXT)) for field in header
        )
        typed = tuple(
            tuple(
                _typed_excel(field, value) for field, value in zip(header, row)
            )
            for row in rows
        )
        workbook = Workbook(
            title="statement",
            sheets=(Sheet(title=sheet_name, columns=columns, rows=typed),),
        )
        path = Path(self._tempdir.name) / name
        write_workbook(path, workbook)
        return path

    def do_import(self, path: Path, **kwargs):
        mapping = kwargs.pop("mapping", DEFAULT_MAPPING)
        request = StatementImportRequest(
            path=path,
            account_code=kwargs.pop("account_code", "1010"),
            counter_account=kwargs.pop("counter_account", "5900"),
            mapping=mapping,
            **kwargs,
        )
        return self.services.imports.import_statement(request)

    def test_import_from_an_excel_workbook(self) -> None:
        result = self.do_import(self.statement())
        self.assertEqual(result.rows_read, 3)
        self.assertEqual(result.imported, 3)
        self.assertEqual(result.failed, 0)
        self.assertEqual(result.batch_id, 1)

        transactions = self.services.ledger.search(limit=10)
        self.assertEqual(len(transactions), 3)
        self.assertEqual(
            transactions[0].date, self.today.replace(day=3)
        )

    def test_excel_import_is_idempotent_like_csv(self) -> None:
        path = self.statement()
        self.do_import(path)
        second = self.do_import(path)
        self.assertEqual(second.imported, 0)
        self.assertEqual(second.duplicates, 3)
        self.assertEqual(self.services.ledger.stats()["transactions"], 3)

    def test_sheet_selection(self) -> None:
        path = self.statement(sheet_name="Bank data")
        result = self.do_import(path, mapping=StatementMapping(
            date_column="date",
            amount_column="amount",
            description_column="description",
            id_column="reference",
            sheet="Bank data",
        ))
        self.assertEqual(result.imported, 3)

        with self.assertRaises(ValidationError):
            self.do_import(path, mapping=StatementMapping(
                date_column="date",
                amount_column="amount",
                description_column="description",
                sheet="missing",
            ))

    def test_dry_run_reads_the_workbook_without_writing(self) -> None:
        result = self.do_import(self.statement(), dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.imported, 3)
        self.assertEqual(self.services.ledger.stats()["transactions"], 0)

    def test_unsupported_file_type_is_rejected(self) -> None:
        path = Path(self._tempdir.name) / "statement.pdf"
        path.write_bytes(b"%PDF-1.4")
        with self.assertRaises(ValidationError) as caught:
            self.do_import(path)
        self.assertIn("unsupported statement file type", str(caught.exception))


class MatchRuleTests(LedgerTestCase):
    """Description based counter account rules for statement import.

    基于描述为对账单导入挑选对方科目的规则。"""

    MAPPING = StatementMapping(
        date_column="date",
        amount_column="amount",
        description_column="description",
        payee_column="payee",
    )

    def statement(self, rows, name: str = "mixed.csv") -> Path:
        return write_statement(
            Path(self._tempdir.name) / name,
            rows,
            header=("date", "amount", "description", "payee"),
        )

    def import_with(
        self, path: Path, *rules: tuple[str, str], counter: str = "5900"
    ):
        return self.services.imports.import_statement(
            StatementImportRequest(
                path=path,
                account_code="1010",
                counter_account=counter,
                mapping=self.MAPPING,
                rules=tuple(
                    MatchRule(pattern, account) for pattern, account in rules
                ),
            )
        )

    def test_rules_override_the_counter_account_per_row(self) -> None:
        path = self.statement(
            (
                ("2025-03-01", "-45.50", "Coffee beans", "Bean shop"),
                ("2025-03-02", "-1200.00", "Office rent", "Landlord"),
                ("2025-03-03", "-9.90", "Stationery", "Corner shop"),
            )
        )
        result = self.import_with(path, ("coffee", "5800"), ("rent", "5100"))
        self.assertEqual(result.imported, 3)
        self.assertEqual(result.matched, 2)
        self.assertEqual(
            result.rule_usage, {"coffee -> 5800": 1, "rent -> 5100": 1}
        )
        self.assertEqual(self.balance("5800"), Decimal("45.50"))
        self.assertEqual(self.balance("5100"), Decimal("1200"))
        self.assertEqual(self.balance("5900"), Decimal("9.90"))

    def test_first_matching_rule_wins(self) -> None:
        path = self.statement(
            (("2025-03-02", "-1200.00", "Office rent", "Landlord"),)
        )
        result = self.import_with(path, ("rent", "5100"), ("office", "5500"))
        self.assertEqual(result.rule_usage, {"rent -> 5100": 1})
        self.assertEqual(self.balance("5100"), Decimal("1200"))

    def test_rules_match_the_payee_too(self) -> None:
        path = self.statement(
            (("2025-03-01", "-8.00", "Card payment", "Landlord"),)
        )
        result = self.import_with(path, ("landlord", "5100"))
        self.assertEqual(result.matched, 1)
        self.assertEqual(self.balance("5100"), Decimal("8"))

    def test_reimport_with_the_same_rules_is_idempotent(self) -> None:
        path = self.statement(
            (
                ("2025-03-01", "-45.50", "Coffee beans", "Bean shop"),
                ("2025-03-03", "-9.90", "Stationery", "Corner shop"),
            )
        )
        self.import_with(path, ("coffee", "5800"))
        second = self.import_with(path, ("coffee", "5800"))
        self.assertEqual(second.imported, 0)
        self.assertEqual(second.duplicates, 2)
        self.assertEqual(self.services.ledger.stats()["transactions"], 2)

    def test_a_ruleless_import_still_deduplicates_under_rules(self) -> None:
        path = self.statement(
            (
                ("2025-03-01", "-45.50", "Coffee beans", "Bean shop"),
                ("2025-03-03", "-9.90", "Stationery", "Corner shop"),
            )
        )
        plain = StatementImportRequest(
            path=path,
            account_code="1010",
            counter_account="5900",
            mapping=self.MAPPING,
        )
        self.assertEqual(self.services.imports.import_statement(plain).imported, 2)
        # Rules only change the matched row; the fallback row keeps its
        # fingerprint and is still recognised.  The earlier coffee entry stays
        # where it was booked - imports never rewrite history - and only the new
        # classification is added.
        # 规则只影响被命中的行；回退到默认科目的行指纹不变，仍能被识别为重复。先前
        # 的咖啡分录保留在原科目——导入从不改写历史——只新增一条重新分类的分录。
        second = self.import_with(path, ("coffee", "5800"))
        self.assertEqual(second.imported, 1)
        self.assertEqual(second.duplicates, 1)
        self.assertEqual(self.services.ledger.stats()["transactions"], 3)
        self.assertEqual(self.balance("5800"), Decimal("45.50"))
        self.assertEqual(self.balance("5900"), Decimal("55.40"))

    def test_changing_a_rule_reimports_only_the_affected_rows(self) -> None:
        path = self.statement(
            (
                ("2025-03-01", "-45.50", "Coffee beans", "Bean shop"),
                ("2025-03-03", "-9.90", "Stationery", "Corner shop"),
            )
        )
        self.import_with(path, ("coffee", "5800"))
        moved = self.import_with(path, ("coffee", "5500"))
        self.assertEqual(moved.imported, 1)
        self.assertEqual(moved.duplicates, 1)
        # Imports never rewrite history: the old classification stays until the
        # user voids it; the row is additionally booked to the new account.
        # 导入从不改写历史：旧的分类会保留到用户作废为止；该行会另行记入新科目。
        self.assertEqual(self.balance("5800"), Decimal("45.50"))
        self.assertEqual(self.balance("5500"), Decimal("45.50"))

    def test_rule_account_must_be_a_real_account(self) -> None:
        path = self.statement((("2025-03-01", "-1.00", "Coffee", "Shop"),))
        with self.assertRaises(NotFoundError):
            self.import_with(path, ("coffee", "9999"))

    def test_rule_account_cannot_be_the_statement_account(self) -> None:
        path = self.statement((("2025-03-01", "-1.00", "Coffee", "Shop"),))
        with self.assertRaises(ValidationError):
            self.import_with(path, ("coffee", "1010"))


def _typed_excel(field: str, value):
    from datetime import date
    from decimal import Decimal, InvalidOperation

    if field == "amount" and isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return value
    if field == "date" and isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value
    return value


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
