"""Formatting helpers used by the text commands.

文本命令使用的格式化辅助函数。"""

from __future__ import annotations

from datetime import date

from centsible.domain.money import Money
from centsible.domain.periods import DatePeriod, month_key
from centsible.domain.transactions import TransactionStatus

__all__ = [
    "money",
    "money_with_currency",
    "month_text",
    "period_text",
    "status_text",
]


def money(value: Money) -> str:
    """Grouped amount without a currency code (tables carry the code in a header).

    不带币种代码、按千分位分组的金额（表格把币种代码放在表头里）。"""

    return value.format()


def money_with_currency(value: Money) -> str:
    return value.format(with_currency=True)


def month_text(day: date) -> str:
    return month_key(day)


def period_text(period: DatePeriod | None) -> str:
    return period.label if period is not None else "all time"


def status_text(status: TransactionStatus | str) -> str:
    value = status.value if isinstance(status, TransactionStatus) else str(status)
    return value.upper()
