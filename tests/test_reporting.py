"""Tests for the pure report builders (no database involved).

纯报表构建器的测试（不涉及数据库）。"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from centsible.application.ports import AccountTotals, LedgerLine
from centsible.application.reporting import (
    build_account_ledger,
    build_activity,
    build_balance_sheet,
    build_budget_report,
    build_income_statement,
    build_trial_balance,
)
from centsible.domain.accounts import Account, AccountType
from centsible.domain.budgets import Budget
from centsible.domain.periods import DatePeriod

CASH = Account(code="1000", name="Cash", type=AccountType.ASSET)
RECEIVABLE = Account(code="1100", name="Receivable", type=AccountType.ASSET)
PAYABLE = Account(code="2000", name="Payable", type=AccountType.LIABILITY)
EQUITY = Account(code="3000", name="Equity", type=AccountType.EQUITY)
SALES = Account(code="4000", name="Sales", type=AccountType.INCOME)
RENT = Account(code="5100", name="Rent", type=AccountType.EXPENSE)
ACCOUNTS = (CASH, RECEIVABLE, PAYABLE, EQUITY, SALES, RENT)
MARCH = DatePeriod.for_month(2025, 3)


class TrialBalanceTests(unittest.TestCase):
    def test_rows_totals_and_natural_signs(self) -> None:
        totals = {
            "1000": AccountTotals(Decimal("500"), Decimal("0"), 1),
            "4000": AccountTotals(Decimal("0"), Decimal("500"), 1),
            "5100": AccountTotals(Decimal("0"), Decimal("0"), 0),
        }
        report = build_trial_balance(
            ACCOUNTS, totals, period=MARCH, currency="CNY"
        )
        self.assertEqual([row.code for row in report.rows], ["1000", "4000"])
        self.assertEqual(report.rows[0].balance.amount, Decimal("500.00"))
        self.assertEqual(report.rows[1].balance.amount, Decimal("500.00"))
        self.assertTrue(report.balanced)
        self.assertEqual(report.total_debit.amount, Decimal("500.00"))
        self.assertEqual(report.total_credit.amount, Decimal("500.00"))

    def test_imbalance_is_reported(self) -> None:
        totals = {"1000": AccountTotals(Decimal("500"), Decimal("0"), 1)}
        report = build_trial_balance(ACCOUNTS, totals, period=MARCH, currency="CNY")
        self.assertFalse(report.balanced)
        self.assertEqual(report.difference.amount, Decimal("500.00"))

    def test_empty_ledger(self) -> None:
        report = build_trial_balance(ACCOUNTS, {}, period=MARCH, currency="CNY")
        self.assertEqual(report.rows, ())
        self.assertTrue(report.balanced)

    def test_json_shape(self) -> None:
        totals = {"1000": AccountTotals(Decimal("1"), Decimal("0"), 1)}
        payload = build_trial_balance(ACCOUNTS, totals, period=MARCH, currency="CNY").to_dict()
        self.assertEqual(payload["kind"], "trial-balance")
        self.assertEqual(payload["rows"][0]["balance"]["amount"], "1.00")


class IncomeStatementTests(unittest.TestCase):
    def test_revenue_minus_expenses(self) -> None:
        totals = {
            "4000": AccountTotals(Decimal("0"), Decimal("600"), 1),
            "5100": AccountTotals(Decimal("100"), Decimal("0"), 1),
        }
        report = build_income_statement(ACCOUNTS, totals, period=MARCH, currency="CNY")
        self.assertEqual([item.code for item in report.revenue.items], ["4000"])
        self.assertEqual(report.revenue.total.amount, Decimal("600.00"))
        self.assertEqual(report.expenses.total.amount, Decimal("100.00"))
        self.assertEqual(report.net_income.amount, Decimal("500.00"))

    def test_accounts_without_activity_are_skipped(self) -> None:
        report = build_income_statement(ACCOUNTS, {}, period=MARCH, currency="CNY")
        self.assertEqual(report.revenue.items, ())
        self.assertEqual(report.expenses.items, ())
        self.assertTrue(report.net_income.is_zero)
        self.assertEqual(report.revenue.total.amount, Decimal("0.00"))

    def test_loss_is_negative_net_income(self) -> None:
        totals = {"5100": AccountTotals(Decimal("80"), Decimal("0"), 1)}
        report = build_income_statement(ACCOUNTS, totals, period=MARCH, currency="CNY")
        self.assertEqual(report.net_income.amount, Decimal("-80.00"))


class BalanceSheetTests(unittest.TestCase):
    def test_assets_equal_liabilities_plus_equity(self) -> None:
        cumulative = {
            "1000": AccountTotals(Decimal("500"), Decimal("0"), 1),
            "4000": AccountTotals(Decimal("0"), Decimal("600"), 1),
            "5100": AccountTotals(Decimal("100"), Decimal("0"), 1),
        }
        report = build_balance_sheet(
            ACCOUNTS,
            cumulative,
            cumulative,
            as_of=date(2025, 3, 31),
            currency="CNY",
        )
        self.assertEqual(report.assets.total.amount, Decimal("500.00"))
        self.assertEqual(report.liabilities.total.amount, Decimal("0.00"))
        self.assertEqual(report.current_earnings.amount, Decimal("500.00"))
        self.assertEqual(report.retained_earnings.amount, Decimal("0.00"))
        self.assertEqual(report.equity.total.amount, Decimal("500.00"))
        self.assertTrue(report.balanced)

    def test_earlier_years_become_retained_earnings(self) -> None:
        cumulative = {
            "1000": AccountTotals(Decimal("900"), Decimal("0"), 2),
            "4000": AccountTotals(Decimal("0"), Decimal("1000"), 2),
            "5100": AccountTotals(Decimal("100"), Decimal("0"), 2),
        }
        current_year = {
            "1000": AccountTotals(Decimal("400"), Decimal("0"), 1),
            "4000": AccountTotals(Decimal("0"), Decimal("500"), 1),
            "5100": AccountTotals(Decimal("100"), Decimal("0"), 1),
        }
        report = build_balance_sheet(
            ACCOUNTS, cumulative, current_year, as_of=date(2025, 6, 30), currency="CNY"
        )
        # prior: income 500, expense 0 -> retained 500; current: income 500 - expense 100
        # 以前年度：收入 500，支出 0 -> 留存收益 500；本年：收入 500 - 支出 100
        self.assertEqual(report.retained_earnings.amount, Decimal("500.00"))
        self.assertEqual(report.current_earnings.amount, Decimal("400.00"))
        self.assertEqual(report.assets.total.amount, Decimal("900.00"))
        self.assertEqual(report.total_liabilities_and_equity.amount, Decimal("900.00"))
        self.assertTrue(report.balanced)

    def test_liabilities_and_equity_accounts_show_naturally(self) -> None:
        cumulative = {
            "1000": AccountTotals(Decimal("300"), Decimal("0"), 1),
            "2000": AccountTotals(Decimal("0"), Decimal("100"), 1),
            "3000": AccountTotals(Decimal("0"), Decimal("200"), 1),
        }
        report = build_balance_sheet(
            ACCOUNTS, cumulative, {}, as_of=date(2025, 3, 31), currency="CNY"
        )
        self.assertEqual(report.liabilities.total.amount, Decimal("100.00"))
        self.assertEqual(report.equity.items[0].code, "3000")
        self.assertEqual(report.equity.total.amount, Decimal("200.00"))
        self.assertTrue(report.balanced)

    def test_imbalance_is_detected(self) -> None:
        cumulative = {"1000": AccountTotals(Decimal("100"), Decimal("0"), 1)}
        report = build_balance_sheet(
            ACCOUNTS, cumulative, {}, as_of=date(2025, 3, 31), currency="CNY"
        )
        self.assertFalse(report.balanced)
        self.assertEqual(report.difference.amount, Decimal("100.00"))


class AccountLedgerTests(unittest.TestCase):
    @staticmethod
    def lines() -> list[LedgerLine]:
        return [
            LedgerLine(date(2025, 3, 2), 1, "Opening", "", "", Decimal("100")),
            LedgerLine(date(2025, 3, 4), 2, "Spend", "", "", Decimal("-30")),
        ]

    def test_running_balance_and_totals(self) -> None:
        report = build_account_ledger(
            CASH,
            self.lines(),
            opening_net_debit=Decimal("50"),
            period=MARCH,
            currency="CNY",
        )
        self.assertEqual(report.opening_balance.amount, Decimal("50.00"))
        self.assertEqual([entry.balance.amount for entry in report.entries],
                         [Decimal("150.00"), Decimal("120.00")])
        self.assertEqual(report.closing_balance.amount, Decimal("120.00"))
        self.assertEqual(report.total_debit.amount, Decimal("100.00"))
        self.assertEqual(report.total_credit.amount, Decimal("30.00"))
        self.assertEqual(report.entry_count, 2)

    def test_credit_normal_account_flips_sign(self) -> None:
        report = build_account_ledger(
            PAYABLE,
            [LedgerLine(date(2025, 3, 5), 9, "Bill", "", "", Decimal("-40"))],
            opening_net_debit=Decimal("0"),
            period=MARCH,
            currency="CNY",
        )
        self.assertEqual(report.closing_balance.amount, Decimal("40.00"))
        self.assertEqual(report.entries[0].credit.amount, Decimal("40.00"))

    def test_empty_ledger(self) -> None:
        report = build_account_ledger(
            CASH, [], opening_net_debit=Decimal("0"), period=MARCH, currency="CNY"
        )
        self.assertEqual(report.entries, ())
        self.assertTrue(report.closing_balance.is_zero)


class BudgetReportTests(unittest.TestCase):
    def test_expense_under_budget_is_favourable(self) -> None:
        budgets = [Budget(account_code="5100", month="2025-03", amount=Decimal("500"))]
        actuals = {"5100": AccountTotals(Decimal("400"), Decimal("0"), 1)}
        report = build_budget_report(
            ACCOUNTS, budgets, actuals, period=MARCH, currency="CNY"
        )
        row = report.rows[0]
        self.assertEqual(row.budgeted.amount, Decimal("500.00"))
        self.assertEqual(row.actual.amount, Decimal("400.00"))
        self.assertEqual(row.variance.amount, Decimal("100.00"))
        self.assertTrue(row.favorable)

    def test_expense_over_budget_is_not_favourable(self) -> None:
        budgets = [Budget(account_code="5100", month="2025-03", amount=Decimal("500"))]
        actuals = {"5100": AccountTotals(Decimal("650"), Decimal("0"), 1)}
        report = build_budget_report(
            ACCOUNTS, budgets, actuals, period=MARCH, currency="CNY"
        )
        self.assertEqual(report.rows[0].variance.amount, Decimal("-150.00"))
        self.assertFalse(report.rows[0].favorable)

    def test_income_under_target_is_not_favourable(self) -> None:
        budgets = [Budget(account_code="4000", month="2025-03", amount=Decimal("1000"))]
        actuals = {"4000": AccountTotals(Decimal("0"), Decimal("600"), 1)}
        report = build_budget_report(
            ACCOUNTS, budgets, actuals, period=MARCH, currency="CNY"
        )
        row = report.rows[0]
        self.assertEqual(row.actual.amount, Decimal("600.00"))
        self.assertFalse(row.favorable)

    def test_unbudgeted_activity_is_listed(self) -> None:
        actuals = {"5100": AccountTotals(Decimal("40"), Decimal("0"), 1)}
        report = build_budget_report(
            ACCOUNTS, [], actuals, period=MARCH, currency="CNY"
        )
        self.assertEqual(len(report.rows), 1)
        self.assertEqual(report.rows[0].budgeted.amount, Decimal("0.00"))

    def test_balance_sheet_accounts_are_ignored(self) -> None:
        actuals = {"1000": AccountTotals(Decimal("40"), Decimal("0"), 1)}
        budgets = [Budget(account_code="1000", month="2025-03", amount=Decimal("10"))]
        report = build_budget_report(
            ACCOUNTS, budgets, actuals, period=MARCH, currency="CNY"
        )
        self.assertEqual(report.rows, ())


class ActivityTests(unittest.TestCase):
    def test_monthly_income_and_expenses(self) -> None:
        totals = {
            "2025-03": {
                "income": AccountTotals(Decimal("0"), Decimal("900"), 1),
                "expense": AccountTotals(Decimal("400"), Decimal("0"), 1),
            }
        }
        report = build_activity(["2025-02", "2025-03"], totals, currency="CNY")
        self.assertEqual([month.month for month in report.months], ["2025-02", "2025-03"])
        self.assertEqual(report.months[0].income.amount, Decimal("0.00"))
        self.assertEqual(report.months[1].income.amount, Decimal("900.00"))
        self.assertEqual(report.months[1].expenses.amount, Decimal("400.00"))
        self.assertEqual(report.months[1].net.amount, Decimal("500.00"))
        self.assertEqual(report.total_net.amount, Decimal("500.00"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
