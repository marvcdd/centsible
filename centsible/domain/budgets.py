"""Budgets and variance analysis.

A budget is a *planned positive amount per account per month*, expressed in the
account's own natural direction: 1,200 of planned groceries, not 1,200 of
credits.  Keeping budgets unsigned makes them comparable with report figures,
which are also presented naturally (see
:meth:`~centsible.domain.accounts.AccountType.natural_balance`).

Variance is ``budgeted - actual``.  For an expense account a positive variance
means money not spent; for an income account it means income missed, so
"favourable" is type dependent and encoded here rather than in the reports.

预算与差异分析。

预算是*每个科目每月计划的正数金额*，按科目自身的自然方向表达：计划 1,200 的
食品杂货，而不是 1,200 的贷方。预算保持不带符号，才能与报表数字直接比较，
报表数字同样按自然方向列示（见
:meth:`~centsible.domain.accounts.AccountType.natural_balance`）。

差异为 ``budgeted - actual``。对费用科目而言，正差异意味着钱没花出去；
对收入科目而言则意味着收入没赚到，所以“是否有利”取决于科目类型，
这一判断放在这里而不是放在报表里。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from centsible.domain.accounts import AccountType, normalize_code
from centsible.domain.money import coerce_decimal
from centsible.domain.periods import month_bounds, parse_month_key
from centsible.errors import ValidationError

__all__ = ["MAX_NOTE_LENGTH", "Budget", "Variance"]

MAX_NOTE_LENGTH = 200


@dataclass(frozen=True, slots=True)
class Budget:
    """A monthly plan for one account.

    单个科目的月度计划。"""

    account_code: str
    month: str
    amount: Decimal
    note: str = ""
    id: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_code", normalize_code(self.account_code))

        month = str(self.month).strip()
        parse_month_key(month)  # raises ValidationError when malformed | 格式不合法时抛出 ValidationError
        object.__setattr__(self, "month", month)

        amount = coerce_decimal(self.amount)
        if amount < 0:
            raise ValidationError(
                "budget amounts are positive plans; use the account direction instead of a "
                "negative number",
                field="amount",
            )
        object.__setattr__(self, "amount", amount)

        note = str(self.note or "").strip()
        if len(note) > MAX_NOTE_LENGTH:
            raise ValidationError(
                f"budget note must be at most {MAX_NOTE_LENGTH} characters", field="note"
            )
        object.__setattr__(self, "note", note)

    @property
    def period_label(self) -> str:
        return self.month

    @property
    def period(self):
        """The calendar month this budget covers.

        该预算覆盖的日历月份。"""

        return month_bounds(self.month)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "account": self.account_code,
            "month": self.month,
            "amount": f"{self.amount:f}",
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class Variance:
    """Budgeted versus actual for one account in one month.

    单个科目在单个月份内的预算数与实际数对比。"""

    budgeted: Decimal
    actual: Decimal

    @property
    def variance(self) -> Decimal:
        """``budgeted - actual``; positive means "less than planned".

        ``budgeted - actual``；正数表示“低于计划”。"""

        return self.budgeted - self.actual

    @property
    def usage_ratio(self) -> Decimal | None:
        """Actual as a fraction of the budget, or ``None`` when nothing was planned.

        实际发生额占预算的比例；若未做预算则返回 ``None``。"""

        if self.budgeted == 0:
            return None
        return self.actual / self.budgeted

    def is_favorable(self, account_type: AccountType | str) -> bool:
        """Whether the deviation helps the entity.

        Under-spending an expense budget is good; under-earning an income budget
        is not.

        该偏差对主体是否有利。

        费用预算花得比计划少是好事；收入预算赚得比计划少则不是。
        """

        kind = AccountType.parse(account_type)
        if kind is AccountType.INCOME:
            return self.actual >= self.budgeted
        return self.actual <= self.budgeted
