"""Ports: the abstractions the application layer depends on.

These protocols are the seam between business logic and storage.  Services are
written against them, the SQLite implementations live in
:mod:`centsible.infrastructure.repositories`, and unit tests may substitute an
in-memory double without touching a file.

The small read models at the top (``AccountTotals``, ``LedgerLine``) exist so a
repository never has to return SQL rows or domain objects it cannot build.

端口接口：应用层所依赖的抽象。

这些协议是业务逻辑与存储之间的接缝。服务是针对它们编写的，
SQLite 实现位于 :mod:`centsible.infrastructure.repositories`，
单元测试也可以在不接触任何文件的情况下换成内存替身。

顶部的几个只读小模型（``AccountTotals``、``LedgerLine``）之所以存在，
是为了让仓储永远不必返回它无法构造的 SQL 行或领域对象。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Callable, Protocol, Sequence

from centsible.domain.accounts import Account, normalize_code
from centsible.domain.budgets import Budget
from centsible.domain.periods import DatePeriod
from centsible.domain.recurrence import RecurringRule
from centsible.domain.transactions import Transaction, TransactionStatus
from centsible.errors import ValidationError

__all__ = [
    "AccountRepository",
    "AccountTotals",
    "AuditEntry",
    "AuditRepository",
    "BudgetRepository",
    "Clock",
    "ImportBatch",
    "ImportRepository",
    "LedgerLine",
    "MatchRule",
    "MetaRepository",
    "RecurringRepository",
    "StatementIssue",
    "StatementMapping",
    "StatementReader",
    "StatementRow",
    "TransactionQuery",
    "TransactionRepository",
    "UnitOfWork",
    "UnitOfWorkFactory",
]


@dataclass(frozen=True, slots=True)
class AccountTotals:
    """Signed totals for one account: debits, credits and posting count.

    单个科目的带符号合计：借方、贷方与分录笔数。
    """

    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    entries: int = 0

    @classmethod
    def zero(cls) -> "AccountTotals":
        return cls()

    @property
    def net_debit(self) -> Decimal:
        """``debits - credits`` - the raw signed balance.

        ``debits - credits``，即未经加工的带符号余额。
        """

        return self.debit - self.credit

    @property
    def is_empty(self) -> bool:
        return self.entries == 0 and self.debit == 0 and self.credit == 0

    def __add__(self, other: "AccountTotals") -> "AccountTotals":
        return AccountTotals(
            debit=self.debit + other.debit,
            credit=self.credit + other.credit,
            entries=self.entries + other.entries,
        )

    def __sub__(self, other: "AccountTotals") -> "AccountTotals":
        return AccountTotals(
            debit=self.debit - other.debit,
            credit=self.credit - other.credit,
            entries=self.entries - other.entries,
        )


@dataclass(frozen=True, slots=True)
class LedgerLine:
    """One posting enriched with the context needed for an account register.

    一条分录，附带科目明细账所需的上下文信息。
    """

    entry_date: date
    transaction_id: int
    description: str
    payee: str
    memo: str
    amount: Decimal


@dataclass(frozen=True, slots=True)
class TransactionQuery:
    """Filter object used by listings and searches.

    列表与搜索功能使用的过滤对象。
    """

    period: DatePeriod | None = None
    account_code: str | None = None
    tag: str | None = None
    text: str | None = None
    status: TransactionStatus | None = None
    limit: int | None = None
    offset: int = 0


@dataclass(frozen=True, slots=True)
class ImportBatch:
    """Audit record of one statement import run.

    一次银行对账单导入运行的审计记录。
    """

    source: str
    filename: str
    checksum: str
    imported_at: datetime
    rows_read: int = 0
    rows_imported: int = 0
    rows_skipped: int = 0
    id: int | None = None


@dataclass(frozen=True, slots=True)
class StatementMapping:
    """How to read one bank/card export layout.

    A column is identified either by its header name or by a zero-based index
    ("0", "1", ...).  Either a single signed ``amount_column`` or a pair of
    ``debit_column``/``credit_column`` must be configured.  ``sheet`` selects a
    worksheet in a spreadsheet file and is ignored for delimited text.

    如何解析某一种银行/银行卡导出的版式。

    列既可以用表头名称标识，也可以用从零开始的下标（"0"、"1"……）标识。
    必须配置一个带符号的 ``amount_column``，或者配置
    ``debit_column``/``credit_column`` 这一对。``sheet`` 用于在表格文件中选择
    工作表，对分隔文本则会被忽略。
    """

    date_column: str = "0"
    amount_column: str | None = "1"
    debit_column: str | None = None
    credit_column: str | None = None
    description_column: str | None = "2"
    payee_column: str | None = None
    id_column: str | None = None
    date_format: str | None = None
    delimiter: str = ","
    encoding: str = "utf-8-sig"
    has_header: bool = True
    amount_sign: int = 1
    skip_rows: int = 0
    sheet: str | None = None

    def __post_init__(self) -> None:
        if not str(self.date_column).strip():
            raise ValidationError("a date column must be configured", field="date_column")
        split_columns = bool(self.debit_column) or bool(self.credit_column)
        if self.amount_column and split_columns:
            raise ValidationError(
                "configure either an amount column or a debit/credit pair, not both",
                field="amount_column",
            )
        if not self.amount_column and not (self.debit_column and self.credit_column):
            raise ValidationError(
                "configure an amount column, or both a debit and a credit column",
                field="amount_column",
            )
        if self.amount_sign not in (1, -1):
            raise ValidationError("amount_sign must be 1 or -1", field="amount_sign")
        if not self.delimiter or len(self.delimiter) > 1:
            raise ValidationError("delimiter must be a single character", field="delimiter")
        if self.skip_rows < 0:
            raise ValidationError("skip_rows must not be negative", field="skip_rows")
        if self.sheet is not None and not str(self.sheet).strip():
            raise ValidationError("sheet must be a name or an index", field="sheet")


@dataclass(frozen=True, slots=True)
class MatchRule:
    """One description pattern and the counter account it maps rows to.

    A row matches when ``pattern`` appears (case-insensitively) in its
    description or payee.  Rules are tried in order and the first match wins, so
    "rent" can be listed before "office" to keep the two apart.

    一条描述匹配模式，以及匹配行应记入的对方科目。

    当 ``pattern``（忽略大小写）出现在行的描述或收款方中时该行即匹配。规则按顺序
    尝试、先匹配者生效，因此把 "rent" 排在 "office" 之前就能把两者区分开。
    """

    pattern: str
    account_code: str

    def __post_init__(self) -> None:
        pattern = str(self.pattern or "").strip().lower()
        if not pattern:
            raise ValidationError(
                "a match rule needs a non empty pattern", field="pattern"
            )
        object.__setattr__(self, "pattern", pattern)
        object.__setattr__(self, "account_code", normalize_code(self.account_code))


@dataclass(frozen=True, slots=True)
class StatementRow:
    """One successfully parsed statement line.

    ``occurrence`` says how many times this exact reference has already appeared
    earlier in the same file (1 for the first).  Two look-alike lines are two
    real transactions unless the caller decides otherwise, so the reader never
    collapses them; the importer uses this number to tell a repeated line apart
    from a line that is already in the ledger.

    一条成功解析出的对账单记录行。

    ``occurrence`` 表示完全相同的引用号在本文件中此前已出现过几次（首次为 1）。
    两行长得一模一样的记录通常是两笔真实业务，是否合并由调用方决定，因此读取器
    从不擅自合并；导入器借助这个数字区分“文件内重复的行”与“账本中已有的行”。
    """

    line_number: int
    date: date
    amount: Decimal
    description: str
    payee: str
    external_id: str
    occurrence: int = 1


@dataclass(frozen=True, slots=True)
class StatementIssue:
    """One statement line that could not be parsed.

    一条无法解析的对账单记录行。
    """

    line_number: int
    message: str


class StatementReader(Protocol):
    """Adapter that turns a statement file into rows plus per-line problems.

    把对账单文件转换成记录行，同时逐行汇报问题的适配器。
    """

    def read(
        self, path: Path, mapping: StatementMapping
    ) -> tuple[list[StatementRow], list[StatementIssue]]: ...


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One immutable line of the audit trail.

    审计轨迹中不可变的一行。
    """

    at: datetime
    actor: str
    action: str
    entity: str
    entity_id: str | None
    detail: str

    def to_dict(self) -> dict[str, str | None]:
        return {
            "at": self.at.isoformat(),
            "actor": self.actor,
            "action": self.action,
            "entity": self.entity,
            "entity_id": self.entity_id,
            "detail": self.detail,
        }


class Clock(Protocol):
    """Source of "now", injected so that tests can freeze time.

    “当前时间”的来源，通过注入让测试可以冻结时间。
    """

    def today(self) -> date: ...

    def now(self) -> datetime: ...


class AccountRepository(Protocol):
    def add(self, account: Account) -> Account: ...

    def update(self, account: Account) -> Account: ...

    def get(self, code: str) -> Account | None: ...

    def list(self, *, include_archived: bool = True) -> list[Account]: ...

    def children_of(self, code: str) -> Sequence[Account]: ...

    def has_children(self, code: str) -> bool: ...

    def posting_count(self, code: str) -> int: ...

    def delete(self, code: str) -> None: ...

    def count(self) -> int: ...


class TransactionRepository(Protocol):
    def add(self, transaction: Transaction) -> Transaction: ...

    def get(self, transaction_id: int) -> Transaction | None: ...

    def find_by_reference(self, reference: str) -> Transaction | None: ...

    def search(self, query: TransactionQuery) -> list[Transaction]: ...

    def totals(
        self, *, start: date | None = None, end: date | None = None
    ) -> dict[str, AccountTotals]: ...

    def type_totals_by_month(
        self, *, start: date | None = None, end: date | None = None
    ) -> dict[str, dict[str, AccountTotals]]: ...

    def postings(
        self, account_code: str, *, start: date | None = None, end: date | None = None
    ) -> list[LedgerLine]: ...

    def mark_void(self, transaction_id: int, *, reason: str) -> None: ...

    def mark_posted(self, transaction_id: int) -> None: ...

    def count(self, *, include_void: bool = True) -> int: ...

    def unbalanced_transaction_ids(self) -> list[int]: ...

    def date_bounds(self) -> tuple[date | None, date | None]: ...


class BudgetRepository(Protocol):
    def upsert(self, budget: Budget) -> Budget: ...

    def get(self, account_code: str, month: str) -> Budget | None: ...

    def list(
        self, *, start_month: str | None = None, end_month: str | None = None
    ) -> list[Budget]: ...

    def delete(self, account_code: str, month: str) -> bool: ...

    def count(self) -> int: ...


class RecurringRepository(Protocol):
    def add(self, rule: RecurringRule) -> RecurringRule: ...

    def update(self, rule: RecurringRule) -> RecurringRule: ...

    def get(self, rule_id: int) -> RecurringRule | None: ...

    def find_by_name(self, name: str) -> RecurringRule | None: ...

    def list(self) -> list[RecurringRule]: ...

    def delete(self, rule_id: int) -> bool: ...


class ImportRepository(Protocol):
    def add_batch(self, batch: ImportBatch) -> ImportBatch: ...

    def has_fingerprint(self, fingerprint: str) -> bool: ...

    def add_fingerprint(
        self, fingerprint: str, *, batch_id: int, transaction_id: int | None
    ) -> None: ...

    def finish_batch(
        self, batch_id: int, *, rows_read: int, rows_imported: int, rows_skipped: int
    ) -> None: ...

    def recent_batches(self, limit: int = 10) -> list[ImportBatch]: ...


class MetaRepository(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str) -> None: ...

    def delete(self, key: str) -> None: ...

    def all(self) -> dict[str, str]: ...


class AuditRepository(Protocol):
    def record(
        self,
        *,
        action: str,
        entity: str,
        entity_id: str | int | None = None,
        detail: str = "",
        actor: str = "cli",
    ) -> None: ...

    def recent(self, limit: int = 20) -> list[AuditEntry]: ...

    def count(self) -> int: ...


class UnitOfWork(Protocol):
    """One transactional scope with access to every repository.

    Usage::

        with uow_factory() as uow:
            ...
            uow.commit()

    Leaving the block without calling ``commit`` rolls back, which makes
    "forgot to commit" a safe failure instead of a silent partial write.

    一个事务范围，可以访问所有仓储。

    用法::

        with uow_factory() as uow:
            ...
            uow.commit()

    离开代码块而未调用 ``commit`` 会回滚，因此“忘记提交”会安全地失败，
    而不会悄无声息地只写入一部分数据。
    """

    @property
    def accounts(self) -> AccountRepository: ...

    @property
    def transactions(self) -> TransactionRepository: ...

    @property
    def budgets(self) -> BudgetRepository: ...

    @property
    def recurring(self) -> RecurringRepository: ...

    @property
    def imports(self) -> ImportRepository: ...

    @property
    def meta(self) -> MetaRepository: ...

    @property
    def audit(self) -> AuditRepository: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def __enter__(self) -> "UnitOfWork": ...

    def __exit__(self, exc_type, exc, tb) -> bool | None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
