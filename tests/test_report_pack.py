"""Tests for the Excel report pack assembly.

Excel 报表包装配的测试。"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from centsible.domain.periods import DatePeriod
from centsible.errors import ValidationError
from tests.support import LedgerTestCase, posts

SHEET_TITLES = (
    "Summary",
    "Monthly activity",
    "Trial balance",
    "Income statement",
    "Balance sheet",
    "Budgets",
    "Journal",
    "Recurring rules",
    "Accounts",
)


class ReportPackTests(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.record(
            "Opening capital",
            ("1010", "10000"),
            ("3000", "-10000"),
            day=date(2025, 2, 1),
        )
        self.record("February rent", ("5100", "1200"), ("1010", "-1200"), day=date(2025, 2, 10))
        self.record("March sales", ("1010", "5000"), ("4000", "-5000"), day=date(2025, 3, 5))
        self.record("March rent", ("5100", "1200"), ("1010", "-1200"), day=date(2025, 3, 10))
        self.services.budgets.set(account_code="5100", month="2025-03", amount="1500")
        self.services.recurring.create(
            name="Monthly rent",
            description="Office rent",
            frequency="monthly",
            postings=posts(("5100", "1200"), ("1010", "-1200")),
            start_date=date(2025, 4, 1),
        )
        self.pack = self.services.report_pack.build(DatePeriod.for_month(2025, 3), months=3)

    def sheet(self, title: str):
        return next(sheet for sheet in self.pack.sheets if sheet.title == title)

    def summary_values(self) -> dict[str, object]:
        return {row[0]: row[1] for row in self.sheet("Summary").rows}

    def test_contains_the_expected_sheets_in_order(self) -> None:
        self.assertEqual(self.pack.sheet_titles(), SHEET_TITLES)
        self.assertEqual(self.pack.sheet_count, 9)
        self.assertEqual(self.pack.title, "Centsible report pack")

    def test_summary_carries_the_key_figures(self) -> None:
        values = self.summary_values()
        self.assertEqual(values["Period"], "2025-03")
        self.assertEqual(values["Currency"], "CNY")
        self.assertEqual(values["Accounts"], 23)
        self.assertEqual(values["Transactions"], 2)
        self.assertEqual(values["Net income"], Decimal("3800.00"))
        self.assertEqual(values["Total assets"], Decimal("12600.00"))
        self.assertEqual(values["Balance sheet"], "balanced")

    def test_income_sheet_has_sections_and_the_bottom_line(self) -> None:
        rows = self.sheet("Income statement").rows
        self.assertIn(("Income", "4000", "Sales Revenue", Decimal("5000.00")), rows)
        self.assertIn(("Income", "", "Total income", Decimal("5000.00")), rows)
        self.assertIn(("Expenses", "5100", "Rent", Decimal("1200.00")), rows)
        self.assertIn(("", "", "Net income", Decimal("3800.00")), rows)

    def test_balance_sheet_sheet_reconciles(self) -> None:
        sheet = self.sheet("Balance sheet")
        rows = sheet.rows
        self.assertIn(("Assets", "", "Total assets", Decimal("12600.00")), rows)
        self.assertIn(("Equity", "", "Total equity", Decimal("12600.00")), rows)
        self.assertEqual(sheet.footer, ("", "", "Balance sheet balanced", "yes"))

    def test_journal_sheet_has_one_row_per_posting(self) -> None:
        sheet = self.sheet("Journal")
        self.assertEqual(sheet.row_count, 4)
        self.assertEqual(sheet.rows[0][0], 3)  # transaction id | 凭证 ID
        self.assertEqual(sheet.rows[0][1], date(2025, 3, 5))
        self.assertEqual(sheet.rows[0][8], Decimal("5000.00"))

    def test_budgets_sheet_lists_planned_activity(self) -> None:
        rows = self.sheet("Budgets").rows
        rent_row = next(row for row in rows if row[0] == "5100")
        self.assertEqual(rent_row[2], Decimal("1500.00"))
        self.assertEqual(rent_row[3], Decimal("1200.00"))
        self.assertEqual(rent_row[4], Decimal("300.00"))
        self.assertEqual(rent_row[5], "ok")

    def test_accounts_sheet_has_balances(self) -> None:
        rows = self.sheet("Accounts").rows
        self.assertEqual(len(rows), 23)
        cash = next(row for row in rows if row[0] == "1010")
        self.assertEqual(cash[5], Decimal("12600.00"))

    def test_monthly_activity_has_the_requested_history(self) -> None:
        rows = self.sheet("Monthly activity").rows
        self.assertEqual([row[0] for row in rows], ["2025-01", "2025-02", "2025-03"])
        self.assertEqual(rows[2][3], Decimal("3800.00"))

    def test_defaults_to_the_current_month(self) -> None:
        default = self.services.report_pack.build()
        summary = {row[0]: row[1] for row in default.sheets[0].rows}
        self.assertEqual(summary["Period"], "2025-03")

    def test_month_window_is_validated(self) -> None:
        with self.assertRaises(ValidationError):
            self.services.report_pack.build(months=0)

    def test_metadata_and_summary_shape(self) -> None:
        payload = self.pack.to_dict()
        self.assertEqual(payload["title"], "Centsible report pack")
        self.assertEqual(len(payload["sheets"]), 9)
        self.assertGreater(payload["total_rows"], 0)
        self.assertIn(("currency", "CNY"), self.pack.metadata)

    def test_a_workbook_can_be_written_and_read_back(self) -> None:
        from pathlib import Path

        from centsible.infrastructure.xlsx import read_worksheet, write_workbook

        path = Path(self._tempdir.name) / "pack.xlsx"
        write_workbook(path, self.pack)
        sheet = read_worksheet(path, sheet="Balance sheet")
        # Empty string cells are not written, so they come back as None.
        # 空字符串单元格不会被写入，因此读回时是 None。
        self.assertIn(
            ("Equity", None, "Total equity", Decimal("12600.00")), sheet.rows
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
