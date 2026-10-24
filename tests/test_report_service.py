"""Tests for the financial statements produced from a real ledger.

基于真实账簿生成的财务报表的测试。"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from centsible.domain.periods import DatePeriod
from centsible.errors import NotFoundError
from tests.support import LedgerTestCase


class ReportServiceTests(LedgerTestCase):
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

    def test_trial_balance_for_a_month(self) -> None:
        report = self.services.reports.trial_balance(DatePeriod.for_month(2025, 3))
        self.assertTrue(report.balanced)
        self.assertEqual([row.code for row in report.rows], ["1010", "4000", "5100"])
        self.assertEqual(report.total_debit.amount, Decimal("6200.00"))
        self.assertEqual(report.total_credit.amount, Decimal("6200.00"))

    def test_trial_balance_defaults_to_all_time(self) -> None:
        report = self.services.reports.trial_balance()
        self.assertTrue(report.period.is_all_time)
        self.assertEqual(len(report.rows), 4)
        self.assertTrue(report.balanced)

    def test_income_statement(self) -> None:
        report = self.services.reports.income_statement(DatePeriod.for_month(2025, 3))
        self.assertEqual(report.revenue.total.amount, Decimal("5000.00"))
        self.assertEqual(report.expenses.total.amount, Decimal("1200.00"))
        self.assertEqual(report.net_income.amount, Decimal("3800.00"))
        self.assertEqual([item.code for item in report.revenue.items], ["4000"])

    def test_income_statement_defaults_to_the_current_month(self) -> None:
        report = self.services.reports.income_statement()
        self.assertEqual(report.period, DatePeriod.for_month(2025, 3))

    def test_balance_sheet_reconciles(self) -> None:
        report = self.services.reports.balance_sheet(date(2025, 3, 31))
        self.assertTrue(report.balanced)
        self.assertEqual(report.assets.total.amount, Decimal("12600.00"))
        self.assertEqual(report.liabilities.total.amount, Decimal("0.00"))
        # 10,000 capital + (5,000 income - 2,400 expenses)
        # 资本 10,000 +（收入 5,000 - 支出 2,400）
        self.assertEqual(report.current_earnings.amount, Decimal("2600.00"))
        self.assertEqual(report.equity.total.amount, Decimal("12600.00"))

    def test_balance_sheet_respects_the_as_of_date(self) -> None:
        report = self.services.reports.balance_sheet(date(2025, 2, 28))
        self.assertTrue(report.balanced)
        self.assertEqual(report.assets.total.amount, Decimal("8800.00"))
        self.assertEqual(report.current_earnings.amount, Decimal("-1200.00"))

    def test_account_ledger(self) -> None:
        report = self.services.reports.account_ledger("1010", DatePeriod.for_month(2025, 3))
        self.assertEqual(report.opening_balance.amount, Decimal("8800.00"))
        self.assertEqual(report.closing_balance.amount, Decimal("12600.00"))
        self.assertEqual(report.entry_count, 2)
        self.assertEqual(report.total_debit.amount, Decimal("5000.00"))
        self.assertEqual(report.total_credit.amount, Decimal("1200.00"))

    def test_account_ledger_for_an_unknown_account(self) -> None:
        with self.assertRaises(NotFoundError):
            self.services.reports.account_ledger("9999")

    def test_account_balance_is_natural(self) -> None:
        self.assertEqual(self.balance("5100"), Decimal("2400"))
        self.assertEqual(self.balance("4000"), Decimal("5000"))
        self.assertEqual(self.balance("3000"), Decimal("10000"))

    def test_activity_summary(self) -> None:
        report = self.services.reports.activity(months=2, until=date(2025, 3, 31))
        self.assertEqual([month.month for month in report.months], ["2025-02", "2025-03"])
        self.assertEqual(report.months[0].expenses.amount, Decimal("1200.00"))
        self.assertEqual(report.months[1].income.amount, Decimal("5000.00"))
        self.assertEqual(report.months[1].net.amount, Decimal("3800.00"))
        self.assertEqual(report.total_net.amount, Decimal("2600.00"))

    def test_activity_validates_the_window(self) -> None:
        with self.assertRaises(Exception):
            self.services.reports.activity(months=0)

    def test_current_month_helpers(self) -> None:
        self.assertEqual(
            self.services.reports.current_month(), DatePeriod.for_month(2025, 3)
        )
        self.assertEqual(self.services.reports.month_label(), "2025-03")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
