"""Read side of the ledger: the financial statements.

Every method here fetches the minimum data it needs (one grouped query per
statement) and delegates the arithmetic to the pure builders in
:mod:`centsible.application.reporting`.  That split is what makes the reports
easy to test and impossible to accidentally couple to SQL.

账簿的读取端：财务报表。

这里的每个方法只取它所需的最少数据（每张报表一次分组查询），
并把算术计算交给 :mod:`centsible.application.reporting` 里的纯函数构建器。
正是这种拆分让报表易于测试，也不可能被意外地耦合到 SQL 上。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from centsible.application.base import ServiceBase
from centsible.application.ports import AccountTotals
from centsible.application.reporting import (
    build_account_ledger,
    build_activity,
    build_balance_sheet,
    build_income_statement,
    build_trial_balance,
)
from centsible.application.dto import (
    AccountLedger,
    ActivityReport,
    BalanceSheet,
    IncomeStatement,
    TrialBalance,
)
from centsible.domain.money import Money
from centsible.domain.periods import (
    DatePeriod,
    fiscal_year_start,
    iter_months,
    month_key,
    shift_months,
)
from centsible.errors import NotFoundError, ValidationError

__all__ = ["ReportService"]


class ReportService(ServiceBase):
    """Produce the four financial statements plus a monthly summary.

    产出四张财务报表以及一份月度汇总。"""

    # ------------------------------------------------------------ statements
    # ------------------------------------------------------------ 报表
    def trial_balance(self, period: DatePeriod | None = None) -> TrialBalance:
        effective = period or DatePeriod.all_time()
        with self._uow_factory() as uow:
            accounts = uow.accounts.list(include_archived=True)
            totals = uow.transactions.totals(
                start=_lower_bound(effective), end=_upper_bound(effective)
            )
        return build_trial_balance(
            accounts, totals, period=effective, currency=self._settings.currency
        )

    def income_statement(self, period: DatePeriod | None = None) -> IncomeStatement:
        effective = period or self.current_month()
        with self._uow_factory() as uow:
            accounts = uow.accounts.list(include_archived=True)
            totals = uow.transactions.totals(
                start=_lower_bound(effective), end=_upper_bound(effective)
            )
        return build_income_statement(
            accounts, totals, period=effective, currency=self._settings.currency
        )

    def balance_sheet(self, as_of: date | None = None) -> BalanceSheet:
        day = as_of or self._clock.today()
        fiscal_start = fiscal_year_start(day, self._settings.fiscal_year_start_month)
        with self._uow_factory() as uow:
            accounts = uow.accounts.list(include_archived=True)
            cumulative = uow.transactions.totals(end=day)
            current = uow.transactions.totals(start=fiscal_start, end=day)
        return build_balance_sheet(
            accounts,
            cumulative,
            current,
            as_of=day,
            currency=self._settings.currency,
        )

    def account_ledger(
        self, account_code: str, period: DatePeriod | None = None
    ) -> AccountLedger:
        effective = period or DatePeriod.all_time()
        with self._uow_factory() as uow:
            account = uow.accounts.get(account_code)
            if account is None:
                raise NotFoundError(f"account {account_code} does not exist")
            opening_net = _opening_net_debit(uow, account.code, effective)
            lines = uow.transactions.postings(
                account.code, start=_lower_bound(effective), end=_upper_bound(effective)
            )
        return build_account_ledger(
            account,
            lines,
            opening_net_debit=opening_net,
            period=effective,
            currency=self._settings.currency,
        )

    def account_balance(self, account_code: str, *, up_to: date | None = None) -> Money:
        with self._uow_factory() as uow:
            account = uow.accounts.get(account_code)
            if account is None:
                raise NotFoundError(f"account {account_code} does not exist")
            totals = uow.transactions.totals(end=up_to).get(account.code, AccountTotals.zero())
        return Money(account.natural_balance(totals.net_debit), self._settings.currency)

    # -------------------------------------------------------------- dashboard
    # -------------------------------------------------------------- 仪表盘
    def activity(self, months: int = 6, *, until: date | None = None) -> ActivityReport:
        """Income, expenses and net result for the last ``months`` months.

        最近 ``months`` 个月的收入、费用与净结果。"""

        if months < 1:
            raise ValidationError("months must be at least 1", field="months")
        if months > 120:
            raise ValidationError("months must be at most 120", field="months")

        day = until or self._clock.today()
        first = shift_months(date(day.year, day.month, 1), -(months - 1))
        with self._uow_factory() as uow:
            by_month = uow.transactions.type_totals_by_month(start=first, end=day)
        keys = list(iter_months(first, day))
        return build_activity(keys, by_month, currency=self._settings.currency)

    def current_month(self) -> DatePeriod:
        today = self._clock.today()
        return DatePeriod.for_month(today.year, today.month)

    def month_to_date(self) -> DatePeriod:
        today = self._clock.today()
        return DatePeriod(DatePeriod.for_month(today.year, today.month).start, today)

    def month_label(self, day: date | None = None) -> str:
        return month_key(day or self._clock.today())


# --------------------------------------------------------------------- helpers
# --------------------------------------------------------------------- 辅助函数
def _lower_bound(period: DatePeriod) -> date | None:
    return None if period.start == date.min else period.start


def _upper_bound(period: DatePeriod) -> date | None:
    return None if period.end == date.max else period.end


def _opening_net_debit(uow, account_code: str, period: DatePeriod) -> Decimal:
    if period.start == date.min:
        return Decimal(0)
    opening = uow.transactions.totals(end=period.start - timedelta(days=1))
    return opening.get(account_code, AccountTotals.zero()).net_debit
