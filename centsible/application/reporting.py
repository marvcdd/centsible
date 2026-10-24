"""Pure report builders.

Every function here takes plain data (accounts plus aggregated totals) and
returns a DTO.  No database, no clock, no formatting - which is exactly why the
financial statements can be tested exhaustively with a handful of dictionaries.

Sign conventions are handled in one place: :meth:`AccountType.natural_balance`.
Reports therefore show a credit balance in a liability or income account as a
positive number, the way an accountant reads them.

纯报表构建函数。

这里的每个函数都只接收普通数据（科目加上汇总数字），返回一个 DTO。它不访问数据库、
不读取时钟、也不做格式化——正因如此，几张字典就足以把财务报表测试得面面俱到。

符号约定只在一处处理：:meth:`AccountType.natural_balance`。因此报表把负债或收入
科目的贷方余额显示为正数，这正是会计人员阅读报表的方式。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from decimal import Decimal

from centsible.application.dto import (
    AccountLedger,
    ActivityReport,
    BalanceSheet,
    BudgetReport,
    BudgetRow,
    IncomeStatement,
    LedgerEntry,
    LineItem,
    MonthActivity,
    Section,
    TrialBalance,
    TrialBalanceRow,
)
from centsible.application.ports import AccountTotals, LedgerLine
from centsible.domain.accounts import Account, AccountType, sort_accounts
from centsible.domain.budgets import Budget, Variance
from centsible.domain.money import Money, sum_decimals
from centsible.domain.periods import DatePeriod

__all__ = [
    "build_account_ledger",
    "build_activity",
    "build_balance_sheet",
    "build_budget_report",
    "build_income_statement",
    "build_trial_balance",
]

_ZERO = AccountTotals.zero()


def build_trial_balance(
    accounts: Sequence[Account],
    totals: Mapping[str, AccountTotals],
    *,
    period: DatePeriod,
    currency: str,
) -> TrialBalance:
    """Classic trial balance: every account with activity, debits vs credits.

    经典试算平衡表：列出所有有发生额的科目，以及各自的借方与贷方。"""

    by_code = {account.code: account for account in accounts}
    rows: list[TrialBalanceRow] = []

    for code in sorted(totals):
        account = by_code.get(code)
        if account is None:  # pragma: no cover - guarded by the service | 由服务层兜底
            continue
        account_totals = totals[code]
        if account_totals.is_empty:
            continue
        rows.append(
            TrialBalanceRow(
                code=account.code,
                name=account.name,
                type=account.type,
                debit=Money(account_totals.debit, currency),
                credit=Money(account_totals.credit, currency),
                balance=Money(account.natural_balance(account_totals.net_debit), currency),
            )
        )

    total_debit = Money(sum_decimals(row.debit.amount for row in rows), currency)
    total_credit = Money(sum_decimals(row.credit.amount for row in rows), currency)
    return TrialBalance(
        period=period,
        currency=currency,
        rows=tuple(rows),
        total_debit=total_debit,
        total_credit=total_credit,
    )


def build_income_statement(
    accounts: Sequence[Account],
    totals: Mapping[str, AccountTotals],
    *,
    period: DatePeriod,
    currency: str,
) -> IncomeStatement:
    """Revenue and expenses for a period, with net income at the bottom.

    某个会计期间内的收入与费用，净利润列在最下方。"""

    revenue_items: list[LineItem] = []
    expense_items: list[LineItem] = []

    for account in sort_accounts(accounts):
        if not account.type.is_income_statement:
            continue
        account_totals = totals.get(account.code, _ZERO)
        if account_totals.entries == 0:
            continue
        amount = account.natural_balance(account_totals.net_debit)
        item = LineItem(code=account.code, name=account.name, amount=Money(amount, currency))
        if account.type is AccountType.INCOME:
            revenue_items.append(item)
        else:
            expense_items.append(item)

    revenue = _section("Income", revenue_items, currency)
    expenses = _section("Expenses", expense_items, currency)
    net_income = revenue.total - expenses.total
    return IncomeStatement(
        period=period,
        currency=currency,
        revenue=revenue,
        expenses=expenses,
        net_income=net_income,
    )


def build_balance_sheet(
    accounts: Sequence[Account],
    cumulative: Mapping[str, AccountTotals],
    current_fiscal_year: Mapping[str, AccountTotals],
    *,
    as_of: date,
    currency: str,
) -> BalanceSheet:
    """Assets, liabilities and equity as of a date.

    ``cumulative`` holds totals from the beginning of time, ``current_fiscal_year``
    the totals since the fiscal year start.  Their difference is what belongs to
    earlier years, so the equity section can split retained earnings from the
    result of the current year - without needing closing entries.

    截至某日的资产、负债与权益。

    ``cumulative`` 保存自建账以来的累计数，``current_fiscal_year`` 保存本会计年度
    开始以来的数字。两者之差属于更早的年度，因此权益部分可以把留存收益与本年度
    利润分开列示——无需任何结账分录。
    """

    asset_items: list[LineItem] = []
    liability_items: list[LineItem] = []
    equity_items: list[LineItem] = []

    current_income = Decimal(0)
    current_expense = Decimal(0)
    prior_income = Decimal(0)
    prior_expense = Decimal(0)

    for account in sort_accounts(accounts):
        total = cumulative.get(account.code, _ZERO)
        current = current_fiscal_year.get(account.code, _ZERO)
        prior = total - current
        natural = account.natural_balance(total.net_debit)

        if account.type is AccountType.ASSET:
            if total.entries:
                asset_items.append(
                    LineItem(account.code, account.name, Money(natural, currency))
                )
        elif account.type is AccountType.LIABILITY:
            if total.entries:
                liability_items.append(
                    LineItem(account.code, account.name, Money(natural, currency))
                )
        elif account.type is AccountType.EQUITY:
            if total.entries:
                equity_items.append(
                    LineItem(account.code, account.name, Money(natural, currency))
                )
        elif account.type is AccountType.INCOME:
            current_income += account.natural_balance(current.net_debit)
            prior_income += account.natural_balance(prior.net_debit)
        else:  # expense | 费用
            current_expense += account.natural_balance(current.net_debit)
            prior_expense += account.natural_balance(prior.net_debit)

    retained_earnings = Money(prior_income - prior_expense, currency)
    current_earnings = Money(current_income - current_expense, currency)

    equity_items.append(
        LineItem("", "Retained earnings (earlier periods)", retained_earnings)
    )
    equity_items.append(LineItem("", "Current period earnings", current_earnings))

    return BalanceSheet(
        as_of=as_of,
        currency=currency,
        assets=_section("Assets", asset_items, currency),
        liabilities=_section("Liabilities", liability_items, currency),
        equity=_section("Equity", equity_items, currency),
        retained_earnings=retained_earnings,
        current_earnings=current_earnings,
    )


def build_account_ledger(
    account: Account,
    lines: Sequence[LedgerLine],
    *,
    opening_net_debit: Decimal,
    period: DatePeriod,
    currency: str,
) -> AccountLedger:
    """A running register for one account.

    单个科目的序时明细账。"""

    running = opening_net_debit
    total_debit = Decimal(0)
    total_credit = Decimal(0)
    entries: list[LedgerEntry] = []

    for line in lines:
        running += line.amount
        debit = line.amount if line.amount > 0 else Decimal(0)
        credit = -line.amount if line.amount < 0 else Decimal(0)
        total_debit += debit
        total_credit += credit
        entries.append(
            LedgerEntry(
                entry_date=line.entry_date,
                transaction_id=line.transaction_id,
                description=line.description,
                memo=line.memo,
                debit=Money(debit, currency),
                credit=Money(credit, currency),
                balance=Money(account.natural_balance(running), currency),
            )
        )

    return AccountLedger(
        account=account,
        period=period,
        opening_balance=Money(account.natural_balance(opening_net_debit), currency),
        entries=tuple(entries),
        closing_balance=Money(account.natural_balance(running), currency),
        total_debit=Money(total_debit, currency),
        total_credit=Money(total_credit, currency),
    )


def build_budget_report(
    accounts: Sequence[Account],
    budgets: Sequence[Budget],
    actuals: Mapping[str, AccountTotals],
    *,
    period: DatePeriod,
    currency: str,
) -> BudgetReport:
    """Budget versus actual for every account planned in ``period``.

    ``period`` 内所有已做预算科目的预算与实际对比。"""

    by_code = {account.code: account for account in accounts}
    planned: dict[str, Decimal] = {}
    for budget in budgets:
        planned[budget.account_code] = planned.get(budget.account_code, Decimal(0)) + budget.amount

    codes = set(planned)
    codes.update(code for code, total in actuals.items() if total.entries)

    rows: list[BudgetRow] = []
    for code in sorted(codes):
        account = by_code.get(code)
        if account is None or not account.type.is_income_statement:
            continue
        budgeted = planned.get(code, Decimal(0))
        actual = account.natural_balance(actuals.get(code, _ZERO).net_debit)
        variance = Variance(budgeted=budgeted, actual=actual)
        rows.append(
            BudgetRow(
                account=account,
                budgeted=Money(budgeted, currency),
                actual=Money(actual, currency),
                variance=Money(variance.variance, currency),
                favorable=variance.is_favorable(account.type),
            )
        )

    total_budgeted = Money(sum_decimals(row.budgeted.amount for row in rows), currency)
    total_actual = Money(sum_decimals(row.actual.amount for row in rows), currency)
    total_variance = Money(sum_decimals(row.variance.amount for row in rows), currency)
    return BudgetReport(
        period=period,
        currency=currency,
        rows=tuple(rows),
        total_budgeted=total_budgeted,
        total_actual=total_actual,
        total_variance=total_variance,
    )


def build_activity(
    months: Sequence[str],
    totals_by_month: Mapping[str, Mapping[str, AccountTotals]],
    *,
    currency: str,
) -> ActivityReport:
    """Month by month income, expenses and net result.

    逐月的收入、费用与净结果。"""

    rows: list[MonthActivity] = []
    for month in months:
        month_totals = totals_by_month.get(month, {})
        income = -month_totals.get(AccountType.INCOME.value, _ZERO).net_debit
        expenses = month_totals.get(AccountType.EXPENSE.value, _ZERO).net_debit
        rows.append(
            MonthActivity(
                month=month,
                income=Money(income, currency),
                expenses=Money(expenses, currency),
                net=Money(income - expenses, currency),
            )
        )

    total_income = Money(sum_decimals(row.income.amount for row in rows), currency)
    total_expenses = Money(sum_decimals(row.expenses.amount for row in rows), currency)
    return ActivityReport(
        months=tuple(rows),
        total_income=total_income,
        total_expenses=total_expenses,
        total_net=total_income - total_expenses,
    )


# ------------------------------------------------------------------- helpers
# ------------------------------------------------------------------- 辅助函数
def _section(title: str, items: Iterable[LineItem], currency: str) -> Section:
    collected = tuple(items)
    total = Money(sum_decimals(item.amount.amount for item in collected), currency)
    return Section(title=title, items=collected, total=total)
