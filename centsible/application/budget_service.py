"""Budgets: set monthly plans and compare them with reality.

Only income and expense accounts can be budgeted.  Balance sheet accounts are
not "spent" within a period, so a budget for them would be meaningless.

预算：制定月度计划，并与实际情况对比。

只有收入和费用科目才能编制预算。资产负债表科目在一个会计期间内并不存在“花掉”
的概念，因此为它们编制预算毫无意义。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from centsible.application.base import ServiceBase
from centsible.application.dto import BudgetReport
from centsible.application.reporting import build_budget_report
from centsible.domain.budgets import Budget
from centsible.domain.money import parse_amount
from centsible.domain.periods import DatePeriod, iter_months, parse_month_key
from centsible.errors import ConflictError, NotFoundError, ValidationError

__all__ = ["BudgetService"]


class BudgetService(ServiceBase):
    """Create, list and evaluate budgets.

    创建、列出并评估预算。"""

    # ------------------------------------------------------------------- write
    # ------------------------------------------------------------------- 写入
    def set(
        self,
        *,
        account_code: str,
        month: str,
        amount: str | Decimal | int,
        note: str = "",
    ) -> Budget:
        """Create or replace the plan for one account and month.

        为某个科目的某个月份创建或替换预算计划。"""

        parse_month_key(month)
        quantized = parse_amount(
            amount, exponent=self._settings.exponent, field="amount"
        )

        with self._transaction() as (uow, _owned):
            account = uow.accounts.get(account_code)
            if account is None:
                raise NotFoundError(f"account {account_code} does not exist")
            if not account.type.is_income_statement:
                raise ValidationError(
                    "budgets can only be set on income or expense accounts, "
                    f"but {account.code} is a {account.type.value} account",
                    field="account",
                )
            if account.archived:
                raise ConflictError(f"account {account.code} is archived")

            budget = Budget(
                account_code=account.code, month=month, amount=quantized, note=note
            )
            stored = uow.budgets.upsert(budget)
            uow.audit.record(
                action="budget.set",
                entity="budget",
                entity_id=f"{budget.account_code}@{budget.month}",
                detail=f"{stored.amount:f}",
            )
        return stored

    def clear(self, *, account_code: str, month: str) -> bool:
        """Remove one plan; returns ``False`` when there was nothing to remove.

        删除一条预算计划；没有可删除的内容时返回 ``False``。"""

        parse_month_key(month)
        with self._transaction() as (uow, _owned):
            removed = uow.budgets.delete(account_code, month)
            if removed:
                uow.audit.record(
                    action="budget.clear",
                    entity="budget",
                    entity_id=f"{account_code.upper()}@{month}",
                )
        return removed

    def copy_month(
        self, *, source_month: str, target_month: str, overwrite: bool = False
    ) -> int:
        """Clone every plan of one month onto another.

        把某个月份的全部预算计划克隆到另一个月份。"""

        source_month = str(source_month).strip()
        target_month = str(target_month).strip()
        parse_month_key(source_month)
        parse_month_key(target_month)
        if source_month == target_month:
            raise ValidationError("source and target month are identical", field="target_month")

        with self._transaction() as (uow, _owned):
            source = uow.budgets.list(start_month=source_month, end_month=source_month)
            if not source:
                raise NotFoundError(f"no budgets configured for {source_month}")
            existing = {
                budget.account_code
                for budget in uow.budgets.list(
                    start_month=target_month, end_month=target_month
                )
            }
            copied = 0
            for budget in source:
                if budget.account_code in existing and not overwrite:
                    continue
                uow.budgets.upsert(
                    Budget(
                        account_code=budget.account_code,
                        month=target_month,
                        amount=budget.amount,
                        note=budget.note,
                    )
                )
                copied += 1
            if copied:
                uow.audit.record(
                    action="budget.copy",
                    entity="budget",
                    entity_id=f"{source_month}->{target_month}",
                    detail=f"{copied} plan(s)",
                )
        return copied

    # ------------------------------------------------------------------- query
    # ------------------------------------------------------------------- 查询
    def list(
        self, *, start_month: str | None = None, end_month: str | None = None
    ) -> list[Budget]:
        if start_month is not None:
            parse_month_key(start_month)
        if end_month is not None:
            parse_month_key(end_month)
        with self._uow_factory() as uow:
            return uow.budgets.list(start_month=start_month, end_month=end_month)

    def report(self, period: DatePeriod | None = None) -> BudgetReport:
        """Budget versus actual for a bounded period.

        在限定的日期区间内对比预算与实际发生额。"""

        effective = period or self._current_month()
        if effective.start == date.min or effective.end == date.max:
            raise ValidationError(
                "a budget report needs a bounded period, for example --month 2025-03",
                field="period",
            )

        months = list(iter_months(effective.start, effective.end))
        with self._uow_factory() as uow:
            accounts = uow.accounts.list(include_archived=True)
            budgets = uow.budgets.list(start_month=months[0], end_month=months[-1])
            actuals = uow.transactions.totals(start=effective.start, end=effective.end)

        return build_budget_report(
            accounts,
            budgets,
            actuals,
            period=effective,
            currency=self._settings.currency,
        )

    def _current_month(self) -> DatePeriod:
        today = self._clock.today()
        return DatePeriod.for_month(today.year, today.month)
