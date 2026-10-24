"""Tests for the budget service.

预算服务的测试。"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from centsible.domain.periods import DatePeriod
from centsible.errors import NotFoundError, ValidationError
from tests.support import LedgerTestCase


class BudgetServiceTests(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.record("March rent", ("5100", "900"), ("1010", "-900"), day=date(2025, 3, 5))

    def test_set_and_read_back(self) -> None:
        budget = self.services.budgets.set(
            account_code="5100", month="2025-03", amount="1200", note="plan"
        )
        self.assertEqual(budget.amount, Decimal("1200.00"))
        self.assertEqual(budget.note, "plan")
        self.assertEqual(self.services.budgets.list()[0].amount, Decimal("1200.00"))

    def test_set_replaces_an_existing_plan(self) -> None:
        self.services.budgets.set(account_code="5100", month="2025-03", amount="1000")
        self.services.budgets.set(account_code="5100", month="2025-03", amount="1500")
        budgets = self.services.budgets.list()
        self.assertEqual(len(budgets), 1)
        self.assertEqual(budgets[0].amount, Decimal("1500.00"))

    def test_budgets_only_apply_to_income_and_expense_accounts(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            self.services.budgets.set(account_code="1000", month="2025-03", amount="10")
        self.assertIn("income or expense", str(caught.exception))

    def test_unknown_account(self) -> None:
        with self.assertRaises(NotFoundError):
            self.services.budgets.set(account_code="9999", month="2025-03", amount="10")

    def test_negative_amounts_are_refused(self) -> None:
        with self.assertRaises(ValidationError):
            self.services.budgets.set(account_code="5100", month="2025-03", amount="-1")

    def test_extra_decimals_are_refused(self) -> None:
        with self.assertRaises(ValidationError):
            self.services.budgets.set(account_code="5100", month="2025-03", amount="10.001")

    def test_archived_account_is_refused(self) -> None:
        self.services.accounts.create(code="TEMP", name="Temp", type="expense")
        self.services.accounts.archive("TEMP")
        with self.assertRaises(Exception):
            self.services.budgets.set(account_code="TEMP", month="2025-03", amount="10")

    def test_clear(self) -> None:
        self.services.budgets.set(account_code="5100", month="2025-03", amount="10")
        self.assertTrue(self.services.budgets.clear(account_code="5100", month="2025-03"))
        self.assertFalse(self.services.budgets.clear(account_code="5100", month="2025-03"))

    def test_list_filters_by_month_range(self) -> None:
        for month in ("2025-01", "2025-02", "2025-03"):
            self.services.budgets.set(account_code="5100", month=month, amount="10")
        filtered = self.services.budgets.list(start_month="2025-02", end_month="2025-03")
        self.assertEqual([budget.month for budget in filtered], ["2025-02", "2025-03"])

    def test_report_shows_plan_actual_and_variance(self) -> None:
        self.services.budgets.set(account_code="5100", month="2025-03", amount="1200")
        report = self.services.budgets.report(DatePeriod.for_month(2025, 3))
        row = next(row for row in report.rows if row.account.code == "5100")
        self.assertEqual(row.budgeted.amount, Decimal("1200.00"))
        self.assertEqual(row.actual.amount, Decimal("900.00"))
        self.assertEqual(row.variance.amount, Decimal("300.00"))
        self.assertTrue(row.favorable)
        self.assertEqual(report.total_budgeted.amount, Decimal("1200.00"))

    def test_report_defaults_to_the_current_month(self) -> None:
        report = self.services.budgets.report()
        self.assertEqual(report.period, DatePeriod.for_month(2025, 3))

    def test_report_needs_a_bounded_period(self) -> None:
        with self.assertRaises(ValidationError):
            self.services.budgets.report(DatePeriod.all_time())

    def test_copy_month(self) -> None:
        self.services.budgets.set(account_code="5100", month="2025-03", amount="1200")
        self.services.budgets.set(account_code="5200", month="2025-03", amount="3500")
        copied = self.services.budgets.copy_month(
            source_month="2025-03", target_month="2025-04"
        )
        self.assertEqual(copied, 2)
        april = self.services.budgets.list(start_month="2025-04", end_month="2025-04")
        self.assertEqual({budget.account_code for budget in april}, {"5100", "5200"})

    def test_copy_month_skips_existing_unless_overwriting(self) -> None:
        self.services.budgets.set(account_code="5100", month="2025-03", amount="1200")
        self.services.budgets.set(account_code="5100", month="2025-04", amount="999")
        self.assertEqual(
            self.services.budgets.copy_month(source_month="2025-03", target_month="2025-04"),
            0,
        )
        self.assertEqual(
            self.services.budgets.copy_month(
                source_month="2025-03", target_month="2025-04", overwrite=True
            ),
            1,
        )
        april = self.services.budgets.list(start_month="2025-04", end_month="2025-04")
        self.assertEqual(april[0].amount, Decimal("1200.00"))

    def test_copy_from_an_empty_month(self) -> None:
        with self.assertRaises(NotFoundError):
            self.services.budgets.copy_month(source_month="1999-01", target_month="2025-04")

    def test_copy_validates_the_target(self) -> None:
        with self.assertRaises(ValidationError):
            self.services.budgets.copy_month(source_month="2025-03", target_month="2025-03")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
