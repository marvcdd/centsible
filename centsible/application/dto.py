"""Report and operation data transfer objects.

These are immutable, presentation friendly snapshots.  They carry :class:`Money`
values (so a report cannot be accidentally summed with another currency) and
know how to serialise themselves for ``--json`` output.

报表与操作的数据传输对象。

这些对象是不可变、便于展示的快照。它们携带 :class:`Money` 金额（因此报表不会
意外地与另一币种相加），并且知道如何为 ``--json`` 输出序列化自身。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from centsible.domain.accounts import Account, AccountType
from centsible.domain.money import Money
from centsible.domain.periods import DatePeriod
from centsible.domain.transactions import Transaction

__all__ = [
    "AccountLedger",
    "ActivityReport",
    "BalanceSheet",
    "BudgetReport",
    "BudgetRow",
    "ImportResult",
    "ImportRowIssue",
    "IncomeStatement",
    "LedgerEntry",
    "LineItem",
    "MonthActivity",
    "RecurrenceIssue",
    "RecurrenceRunResult",
    "Section",
    "TrialBalance",
    "TrialBalanceRow",
]


@dataclass(frozen=True, slots=True)
class LineItem:
    """A single labelled amount inside a financial statement section.

    财务报表某一分节中带标签的单个金额。"""

    code: str
    name: str
    amount: Money

    @property
    def label(self) -> str:
        return f"{self.code} {self.name}" if self.code else self.name

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "amount": self.amount.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class Section:
    """A titled group of line items with a total.

    有标题、带合计的一组明细项。"""

    title: str
    items: tuple[LineItem, ...]
    total: Money

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "items": [item.to_dict() for item in self.items],
            "total": self.total.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class TrialBalanceRow:
    code: str
    name: str
    type: AccountType
    debit: Money
    credit: Money
    balance: Money

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "type": self.type.value,
            "debit": self.debit.to_dict(),
            "credit": self.credit.to_dict(),
            "balance": self.balance.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class TrialBalance:
    period: DatePeriod
    currency: str
    rows: tuple[TrialBalanceRow, ...]
    total_debit: Money
    total_credit: Money

    @property
    def balanced(self) -> bool:
        return self.total_debit.amount == self.total_credit.amount

    @property
    def difference(self) -> Money:
        return self.total_debit - self.total_credit

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "trial-balance",
            "period": self.period.to_dict(),
            "currency": self.currency,
            "rows": [row.to_dict() for row in self.rows],
            "total_debit": self.total_debit.to_dict(),
            "total_credit": self.total_credit.to_dict(),
            "balanced": self.balanced,
        }


@dataclass(frozen=True, slots=True)
class IncomeStatement:
    period: DatePeriod
    currency: str
    revenue: Section
    expenses: Section
    net_income: Money

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "income-statement",
            "period": self.period.to_dict(),
            "currency": self.currency,
            "revenue": self.revenue.to_dict(),
            "expenses": self.expenses.to_dict(),
            "net_income": self.net_income.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class BalanceSheet:
    as_of: date
    currency: str
    assets: Section
    liabilities: Section
    equity: Section
    retained_earnings: Money
    current_earnings: Money

    @property
    def total_liabilities_and_equity(self) -> Money:
        return self.liabilities.total + self.equity.total

    @property
    def balanced(self) -> bool:
        return self.assets.total.amount == self.total_liabilities_and_equity.amount

    @property
    def difference(self) -> Money:
        return self.assets.total - self.total_liabilities_and_equity

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "balance-sheet",
            "as_of": self.as_of.isoformat(),
            "currency": self.currency,
            "assets": self.assets.to_dict(),
            "liabilities": self.liabilities.to_dict(),
            "equity": self.equity.to_dict(),
            "retained_earnings": self.retained_earnings.to_dict(),
            "current_earnings": self.current_earnings.to_dict(),
            "balanced": self.balanced,
        }


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    entry_date: date
    transaction_id: int
    description: str
    memo: str
    debit: Money
    credit: Money
    balance: Money

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.entry_date.isoformat(),
            "transaction_id": self.transaction_id,
            "description": self.description,
            "memo": self.memo,
            "debit": self.debit.to_dict(),
            "credit": self.credit.to_dict(),
            "balance": self.balance.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class AccountLedger:
    account: Account
    period: DatePeriod
    opening_balance: Money
    entries: tuple[LedgerEntry, ...]
    closing_balance: Money
    total_debit: Money
    total_credit: Money

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "account-ledger",
            "account": self.account.to_dict(),
            "period": self.period.to_dict(),
            "opening_balance": self.opening_balance.to_dict(),
            "entries": [entry.to_dict() for entry in self.entries],
            "closing_balance": self.closing_balance.to_dict(),
            "total_debit": self.total_debit.to_dict(),
            "total_credit": self.total_credit.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class BudgetRow:
    account: Account
    budgeted: Money
    actual: Money
    variance: Money
    favorable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.account.code,
            "name": self.account.name,
            "type": self.account.type.value,
            "budgeted": self.budgeted.to_dict(),
            "actual": self.actual.to_dict(),
            "variance": self.variance.to_dict(),
            "favorable": self.favorable,
        }


@dataclass(frozen=True, slots=True)
class BudgetReport:
    period: DatePeriod
    currency: str
    rows: tuple[BudgetRow, ...]
    total_budgeted: Money
    total_actual: Money
    total_variance: Money

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "budget-report",
            "period": self.period.to_dict(),
            "currency": self.currency,
            "rows": [row.to_dict() for row in self.rows],
            "total_budgeted": self.total_budgeted.to_dict(),
            "total_actual": self.total_actual.to_dict(),
            "total_variance": self.total_variance.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class MonthActivity:
    month: str
    income: Money
    expenses: Money
    net: Money

    def to_dict(self) -> dict[str, Any]:
        return {
            "month": self.month,
            "income": self.income.to_dict(),
            "expenses": self.expenses.to_dict(),
            "net": self.net.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ActivityReport:
    months: tuple[MonthActivity, ...]
    total_income: Money
    total_expenses: Money
    total_net: Money

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "activity",
            "months": [month.to_dict() for month in self.months],
            "total_income": self.total_income.to_dict(),
            "total_expenses": self.total_expenses.to_dict(),
            "total_net": self.total_net.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class ImportRowIssue:
    line_number: int
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"line": self.line_number, "message": self.message}


@dataclass(frozen=True, slots=True)
class ImportResult:
    source: str
    filename: str
    rows_read: int
    imported: int
    duplicates: int
    failed: int
    issues: tuple[ImportRowIssue, ...]
    transaction_ids: tuple[int, ...]
    batch_id: int | None
    dry_run: bool
    repeated: int = 0
    matched: int = 0
    rule_usage: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.failed == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "statement-import",
            "source": self.source,
            "filename": self.filename,
            "rows_read": self.rows_read,
            "imported": self.imported,
            "duplicates": self.duplicates,
            "repeated": self.repeated,
            "matched": self.matched,
            "rule_usage": dict(self.rule_usage),
            "failed": self.failed,
            "issues": [issue.to_dict() for issue in self.issues],
            "transaction_ids": list(self.transaction_ids),
            "batch_id": self.batch_id,
            "dry_run": self.dry_run,
        }


@dataclass(frozen=True, slots=True)
class RecurrenceIssue:
    rule_id: int | None
    rule_name: str
    occurrence: date | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "occurrence": self.occurrence.isoformat() if self.occurrence else None,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class RecurrenceRunResult:
    rules_processed: int
    transactions_created: tuple[Transaction, ...]
    issues: tuple[RecurrenceIssue, ...]
    dry_run: bool

    @property
    def created_count(self) -> int:
        return len(self.transactions_created)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "recurrence-run",
            "rules_processed": self.rules_processed,
            "transactions_created": [
                {"id": transaction.id, "date": transaction.date.isoformat()}
                for transaction in self.transactions_created
            ],
            "issues": [issue.to_dict() for issue in self.issues],
            "dry_run": self.dry_run,
        }
