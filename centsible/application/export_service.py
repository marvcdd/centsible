"""Export data out of the ledger.

The service produces *data* (headers and rows, or a JSON payload) and the
interface layer writes it.  Keeping file handles in the CLI means exports can be
tested by inspecting the returned structure - no temporary files needed.

把数据导出到账簿之外。

本服务只负责产出*数据*（表头与数据行，或一段 JSON 载荷），由界面层负责写出。
把文件句柄留在 CLI 里，意味着测试导出时只需检查返回的结构——无需临时文件。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from centsible.application.base import ServiceBase
from centsible.application.ports import TransactionQuery
from centsible.application.workbook import (
    CellValue,
    Column,
    ColumnKind,
    Sheet,
    Workbook,
)
from centsible.domain.money import format_amount
from centsible.domain.periods import DatePeriod
from centsible.domain.transactions import Transaction

__all__ = ["BackupPayload", "ExportService", "JournalExport"]


@dataclass(frozen=True, slots=True)
class JournalExport:
    """A flattened journal: one row per posting.

    ``transaction_count`` is the number of transactions *represented in the
    export* - when voided entries are filtered out they are not counted.

    展平后的日记账：每条分录占一行。

    ``transaction_count`` 表示*导出内容中所包含的*凭证数量——已作废的凭证被过滤掉时
    不计算在内。
    """

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    transaction_count: int

    @property
    def row_count(self) -> int:
        return len(self.rows)

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_count": self.transaction_count,
            "row_count": self.row_count,
        }


@dataclass(frozen=True, slots=True)
class BackupPayload:
    """A complete, human readable JSON snapshot.

    一份完整、人类可读的 JSON 快照。"""

    data: dict[str, Any]
    counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {"counts": self.counts}


class ExportService(ServiceBase):
    """Build export payloads from the journal.

    从日记账构建导出载荷。"""

    JOURNAL_HEADERS: tuple[str, ...] = (
        "id",
        "date",
        "description",
        "payee",
        "reference",
        "tags",
        "status",
        "account",
        "amount",
        "memo",
    )

    def ordered_transactions(
        self, period: DatePeriod | None = None
    ) -> list[Transaction]:
        """Every transaction (voided ones included), oldest first.

        全部凭证（含已作废的），日期最早的排在最前。"""

        with self._uow_factory() as uow:
            transactions = uow.transactions.search(TransactionQuery(period=period))
        return sorted(transactions, key=_transaction_order)

    def journal(
        self,
        period: DatePeriod | None = None,
        *,
        include_void: bool = True,
    ) -> JournalExport:
        """One row per posting, with the signed amount (positive = debit).

        每条分录占一行，金额带符号（正数表示借方）。"""

        transactions = self.ordered_transactions(period)
        rows: list[tuple[str, ...]] = []
        for transaction in transactions:
            if transaction.is_void and not include_void:
                continue
            for posting in transaction.postings:
                rows.append(
                    (
                        str(transaction.id or ""),
                        transaction.date.isoformat(),
                        transaction.description,
                        transaction.payee,
                        transaction.reference or "",
                        transaction.tag_text,
                        transaction.status.value,
                        posting.account_code,
                        format_amount(
                            posting.amount,
                            exponent=self._settings.exponent,
                            thousands=False,
                        ),
                        posting.memo,
                    )
                )

        return JournalExport(
            headers=self.JOURNAL_HEADERS,
            rows=tuple(rows),
            transaction_count=sum(
                1 for transaction in transactions
                if include_void or not transaction.is_void
            ),
        )

    def journal_workbook(
        self,
        period: DatePeriod | None = None,
        *,
        include_void: bool = True,
    ) -> Workbook:
        """The journal as a single-sheet :class:`Workbook` for Excel export.

        把日记账整理成单张工作表的 :class:`Workbook`，供 Excel 导出使用。"""

        effective = period or DatePeriod.all_time()
        rows: list[tuple[CellValue, ...]] = []
        for transaction in self.ordered_transactions(period):
            if transaction.is_void and not include_void:
                continue
            for posting in transaction.postings:
                rows.append(
                    (
                        transaction.id,
                        transaction.date,
                        transaction.description,
                        transaction.payee,
                        transaction.reference or "",
                        transaction.tag_text,
                        transaction.status.value.upper(),
                        posting.account_code,
                        posting.amount,
                        posting.memo,
                    )
                )

        sheet = Sheet(
            title="Journal",
            heading="Journal",
            subtitle=(
                f"{effective.label}  -  amounts in {self._settings.currency}"
            ),
            columns=(
                Column("id", ColumnKind.NUMBER, width=8),
                Column("date", ColumnKind.DATE, width=12),
                Column("description", ColumnKind.TEXT, width=36),
                Column("payee", ColumnKind.TEXT, width=20),
                Column("reference", ColumnKind.TEXT, width=16),
                Column("tags", ColumnKind.TEXT, width=16),
                Column("status", ColumnKind.TEXT, width=10),
                Column("account", ColumnKind.TEXT, width=12),
                Column("amount", ColumnKind.MONEY, width=14),
                Column("memo", ColumnKind.TEXT, width=24),
            ),
            rows=tuple(rows),
        )
        return Workbook(title="Centsible journal", sheets=(sheet,))

    def backup(self) -> BackupPayload:
        """Everything needed to rebuild the ledger elsewhere.

        在别处重建账簿所需的全部内容。"""

        with self._uow_factory() as uow:
            accounts = uow.accounts.list(include_archived=True)
            transactions = uow.transactions.search(TransactionQuery())
            budgets = uow.budgets.list()
            rules = uow.recurring.list()
            meta = uow.meta.all()

        ordered = sorted(transactions, key=_transaction_order)
        data: dict[str, Any] = {
            "format": "centsible-backup",
            "version": 1,
            "generated_at": self._clock.now().isoformat(timespec="seconds"),
            "ledger": {
                "currency": self._settings.currency,
                "fiscal_year_start_month": self._settings.fiscal_year_start_month,
            },
            "accounts": [account.to_dict() for account in accounts],
            "transactions": [transaction.to_dict() for transaction in ordered],
            "budgets": [budget.to_dict() for budget in budgets],
            "recurring_rules": [rule.to_dict() for rule in rules],
            "meta": meta,
        }
        counts = {
            "accounts": len(accounts),
            "transactions": len(ordered),
            "postings": sum(len(transaction.postings) for transaction in ordered),
            "budgets": len(budgets),
            "recurring_rules": len(rules),
        }
        return BackupPayload(data=data, counts=counts)


def _transaction_order(transaction: Transaction) -> tuple[date, int]:
    return transaction.date, transaction.id or 0
