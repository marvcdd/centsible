"""The default chart of accounts shipped with ``centsible init``.

It is deliberately small and conventional: a handful of asset, liability,
equity, income and expense accounts that suit a freelancer or a small shop.  It
is *data*, not policy - users are free to delete it and build their own.

``centsible init`` 附带的默认科目表。

它刻意保持精简且符合惯例：少量资产、负债、权益、收入与费用科目，适合自由职业者
或小店。它只是*数据*，而非策略 —— 用户可以自由删除，另建自己的科目表。
"""

from __future__ import annotations

from centsible.domain.accounts import Account, AccountType

__all__ = ["CHART_STYLES", "chart_for", "default_chart"]

_ROWS: tuple[tuple[str, str, AccountType], ...] = (
    ("1000", "Cash", AccountType.ASSET),
    ("1010", "Bank Account", AccountType.ASSET),
    ("1100", "Accounts Receivable", AccountType.ASSET),
    ("1200", "Inventory", AccountType.ASSET),
    ("1500", "Equipment", AccountType.ASSET),
    ("2000", "Accounts Payable", AccountType.LIABILITY),
    ("2100", "Credit Card", AccountType.LIABILITY),
    ("2200", "Sales Tax Payable", AccountType.LIABILITY),
    ("3000", "Owner's Equity", AccountType.EQUITY),
    ("3100", "Retained Earnings", AccountType.EQUITY),
    ("4000", "Sales Revenue", AccountType.INCOME),
    ("4100", "Service Revenue", AccountType.INCOME),
    ("4200", "Other Income", AccountType.INCOME),
    ("5000", "Cost of Goods Sold", AccountType.EXPENSE),
    ("5100", "Rent", AccountType.EXPENSE),
    ("5200", "Salaries", AccountType.EXPENSE),
    ("5300", "Utilities", AccountType.EXPENSE),
    ("5400", "Marketing", AccountType.EXPENSE),
    ("5500", "Office Supplies", AccountType.EXPENSE),
    ("5600", "Bank Fees", AccountType.EXPENSE),
    ("5700", "Travel", AccountType.EXPENSE),
    ("5800", "Meals", AccountType.EXPENSE),
    ("5900", "Software Subscriptions", AccountType.EXPENSE),
)

CHART_STYLES = ("default", "empty")


def default_chart() -> tuple[Account, ...]:
    """Fresh ``Account`` objects for the default chart (no database ids).

    默认科目表的全新 ``Account`` 对象（不含数据库 id）。"""

    return tuple(Account(code=code, name=name, type=kind) for code, name, kind in _ROWS)


def chart_for(style: str = "default") -> tuple[Account, ...]:
    """Return the chart matching ``style`` (``default`` or ``empty``).

    返回与 ``style`` 匹配的科目表（``default`` 或 ``empty``）。"""

    normalized = str(style).strip().lower()
    if normalized == "default":
        return default_chart()
    if normalized == "empty":
        return ()
    raise ValueError(f"unknown chart style {style!r}; choose from {', '.join(CHART_STYLES)}")
