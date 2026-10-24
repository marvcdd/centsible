"""Journal entries and the double entry invariant.

Sign convention
---------------
A :class:`Posting` amount is **signed**: positive means *debit*, negative means
*credit*, and the postings of one transaction must sum to exactly zero.  Keeping
one signed number instead of two columns (debit/credit) makes the central
invariant a plain sum and makes reversal a plain negation.

Posted transactions are immutable.  A mistake is *voided*: the row stays in the
journal with status ``void`` and a reason, but no longer contributes to any
balance.  Nothing is ever deleted, and the audit log records who voided what and
when, so the correction remains traceable without rewriting history twice.

日记账凭证与复式记账不变量。

符号约定
--------
:class:`Posting` 的金额是**带符号**的：正数表示*借方*，负数表示*贷方*，
同一张凭证的各笔分录之和必须恰好为零。用一个带符号的数字代替两列
（借方/贷方），可以让核心不变量简化为一次求和，让冲销简化为一次取负。

已过账的凭证不可修改。发现错误时将其*作废*：该行仍停留在日记账中，
状态为 ``void`` 并附带原因，但不再计入任何余额。数据永远不会被删除，
审计日志会记录谁在何时作废了什么，因此更正过程始终可追溯，
也不必二次改写历史。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable, Sequence

from centsible.domain.accounts import normalize_code
from centsible.domain.money import coerce_decimal
from centsible.errors import UnbalancedEntryError, ValidationError

__all__ = [
    "MAX_DESCRIPTION_LENGTH",
    "MAX_MEMO_LENGTH",
    "MAX_TAGS",
    "Posting",
    "Transaction",
    "TransactionStatus",
    "check_balanced",
    "normalize_tags",
    "sum_credits",
    "sum_debits",
]

MAX_DESCRIPTION_LENGTH = 200
MAX_MEMO_LENGTH = 500
MAX_TAG_LENGTH = 32
MAX_TAGS = 10
MAX_REFERENCE_LENGTH = 64
MAX_PAYEE_LENGTH = 120

_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class TransactionStatus(str, Enum):
    """Lifecycle state of a journal entry.

    ``VOID`` entries stay in the journal - they are the audit record of a
    correction - but no longer contribute to any balance.

    凭证的生命周期状态。

    ``VOID`` 状态的凭证仍保留在日记账中——它们是更正动作的审计记录——
    但不再计入任何余额。
    """
    POSTED = "posted"
    VOID = "void"

    @classmethod
    def parse(cls, value: "TransactionStatus | str") -> "TransactionStatus":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValidationError("transaction status must be 'posted' or 'void'", field="status")
        try:
            return cls(value.strip().lower())
        except ValueError:
            raise ValidationError(
                f"{value!r} is not a transaction status; use 'posted' or 'void'", field="status"
            ) from None

    def __str__(self) -> str:  # pragma: no cover - display helper | 仅用于显示的辅助方法
        return self.value


@dataclass(frozen=True, slots=True)
class Posting:
    """One leg of a journal entry.

    ``amount`` is positive for a debit and negative for a credit; it must not be
    zero, because a zero leg carries no information and is almost always a
    data-entry mistake.

    凭证中的一条分录。

    ``amount`` 为借方时是正数，为贷方时是负数；它不能为零，
    因为零金额的分录不携带任何信息，几乎总是录入时的笔误。
    """

    account_code: str
    amount: Decimal
    memo: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_code", normalize_code(self.account_code))
        amount = coerce_decimal(self.amount)
        if amount == 0:
            raise ValidationError(
                "posting amount must not be zero", field="amount", account=self.account_code
            )
        object.__setattr__(self, "amount", amount)

        memo = str(self.memo or "").strip()
        if len(memo) > MAX_MEMO_LENGTH:
            raise ValidationError(
                f"posting memo must be at most {MAX_MEMO_LENGTH} characters", field="memo"
            )
        object.__setattr__(self, "memo", memo)

    @property
    def is_debit(self) -> bool:
        return self.amount > 0

    @property
    def is_credit(self) -> bool:
        return self.amount < 0

    @property
    def debit_amount(self) -> Decimal:
        return self.amount if self.amount > 0 else Decimal(0)

    @property
    def credit_amount(self) -> Decimal:
        return -self.amount if self.amount < 0 else Decimal(0)

    def negated(self) -> "Posting":
        """The mirror image used when voiding a transaction.

        作废凭证时使用的镜像分录。
        """

        return Posting(self.account_code, -self.amount, self.memo)

    def to_dict(self) -> dict[str, Any]:
        return {
            "account": self.account_code,
            "amount": f"{self.amount:f}",
            "debit": f"{self.debit_amount:f}",
            "credit": f"{self.credit_amount:f}",
            "memo": self.memo,
        }

    def __str__(self) -> str:  # pragma: no cover - display helper | 仅用于显示的辅助方法
        return f"{self.account_code} {self.amount:+f}"


@dataclass(frozen=True, slots=True)
class Transaction:
    """A balanced, dated journal entry.

    Construction validates everything that can be validated without a database:
    the entry must have at least two postings, they must sum to zero, the
    description must be present and the date must be a real calendar date.
    Whether the accounts exist, are postable and whether the period is locked is
    the job of :class:`~centsible.application.ledger_service.LedgerService`.

    一张已平衡、带日期的日记账凭证。

    构造时会校验一切无需数据库即可校验的内容：凭证至少要有两笔分录、
    各笔分录之和必须为零、摘要必须存在，且日期必须是真实存在的日历日期。
    科目是否存在、是否可记账、会计期间是否已锁账，则由
    :class:`~centsible.application.ledger_service.LedgerService` 负责。
    """

    date: date
    description: str
    postings: tuple[Posting, ...]
    payee: str = ""
    reference: str | None = None
    tags: tuple[str, ...] = ()
    status: TransactionStatus = TransactionStatus.POSTED
    id: int | None = None
    import_batch: int | None = None
    void_reason: str = ""
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.date, date) or isinstance(self.date, datetime):
            raise ValidationError("transaction date must be a date", field="date")

        description = str(self.description).strip()
        if not description:
            raise ValidationError("transaction description is required", field="description")
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValidationError(
                f"transaction description must be at most {MAX_DESCRIPTION_LENGTH} characters",
                field="description",
            )
        object.__setattr__(self, "description", description)

        postings = tuple(self.postings)
        for posting in postings:
            if not isinstance(posting, Posting):
                raise TypeError(
                    "transaction postings must be Posting instances, "
                    f"got {type(posting).__name__}"
                )
        if len(postings) < 2:
            raise ValidationError(
                "a transaction needs at least two postings (one debit, one credit)",
                field="postings",
            )
        object.__setattr__(self, "postings", postings)

        residual = check_balanced(postings)
        if residual != 0:
            raise UnbalancedEntryError(
                "postings do not balance: debits "
                f"{sum_debits(postings):f} vs credits {sum_credits(postings):f} "
                f"(difference {residual:f})",
                field="postings",
            )

        payee = str(self.payee or "").strip()
        if len(payee) > MAX_PAYEE_LENGTH:
            raise ValidationError(
                f"payee must be at most {MAX_PAYEE_LENGTH} characters", field="payee"
            )
        object.__setattr__(self, "payee", payee)

        reference = self.reference
        if reference is not None:
            reference = str(reference).strip() or None
            if reference is not None and len(reference) > MAX_REFERENCE_LENGTH:
                raise ValidationError(
                    f"reference must be at most {MAX_REFERENCE_LENGTH} characters",
                    field="reference",
                )
        object.__setattr__(self, "reference", reference)

        object.__setattr__(self, "tags", normalize_tags(self.tags))
        object.__setattr__(self, "status", TransactionStatus.parse(self.status))

        void_reason = str(self.void_reason or "").strip()
        object.__setattr__(self, "void_reason", void_reason)

    # ------------------------------------------------------------- properties
    # ------------------------------------------------------------- 属性
    @property
    def total_debits(self) -> Decimal:
        return sum_debits(self.postings)

    @property
    def total_credits(self) -> Decimal:
        return sum_credits(self.postings)

    @property
    def amount(self) -> Decimal:
        """The size of the entry - the sum of its debits.

        凭证的金额——即其借方金额之和。
        """

        return self.total_debits

    @property
    def accounts(self) -> tuple[str, ...]:
        """Distinct account codes touched, sorted for stable output.

        涉及的科目代码已去重并排序，以保证输出稳定。
        """

        return tuple(sorted({posting.account_code for posting in self.postings}))

    @property
    def is_void(self) -> bool:
        return self.status is TransactionStatus.VOID

    @property
    def tag_text(self) -> str:
        return ", ".join(self.tags)

    def postings_for(self, account_code: str) -> tuple[Posting, ...]:
        code = normalize_code(account_code)
        return tuple(posting for posting in self.postings if posting.account_code == code)

    # ---------------------------------------------------------------- actions
    # ---------------------------------------------------------------- 操作
    def voided(self, *, reason: str) -> "Transaction":
        """Return a copy marked void, keeping every original field for audit.

        返回一份标记为作废的副本，保留全部原始字段以供审计。
        """

        text = str(reason or "").strip()
        if not text:
            raise ValidationError(
                "a reason is required when voiding a transaction", field="reason"
            )
        return replace(self, status=TransactionStatus.VOID, void_reason=text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "description": self.description,
            "payee": self.payee,
            "reference": self.reference,
            "tags": list(self.tags),
            "status": self.status.value,
            "import_batch": self.import_batch,
            "void_reason": self.void_reason,
            "postings": [posting.to_dict() for posting in self.postings],
            "total": f"{self.total_debits:f}",
        }

    def __str__(self) -> str:  # pragma: no cover - display helper | 仅用于显示的辅助方法
        return f"#{self.id or '?'} {self.date.isoformat()} {self.description}"


# ----------------------------------------------------------------- pure helpers
# ----------------------------------------------------------------- 纯辅助函数
def sum_debits(postings: Iterable[Posting]) -> Decimal:
    total = Decimal(0)
    for posting in postings:
        if posting.amount > 0:
            total += posting.amount
    return total


def sum_credits(postings: Iterable[Posting]) -> Decimal:
    total = Decimal(0)
    for posting in postings:
        if posting.amount < 0:
            total -= posting.amount
    return total


def check_balanced(postings: Sequence[Posting]) -> Decimal:
    """Return ``debits - credits``; the entry is balanced when this is zero.

    返回 ``debits - credits``；该值为零即表示凭证是平衡的。
    """

    residual = Decimal(0)
    for posting in postings:
        residual += posting.amount
    return residual


def normalize_tags(tags: Iterable[str] | str | None) -> tuple[str, ...]:
    """Canonicalise tags: lower case, de-duplicated, sorted, ``#`` stripped.

    Accepts ``"a,b"``, ``["a", "b"]`` or ``("a b",)`` and never returns
    duplicates, so tag based filtering is predictable.

    把标签规范化：转小写、去重、排序，并去掉 ``#`` 前缀。

    接受 ``"a,b"``、``["a", "b"]`` 或 ``("a b",)`` 等写法，且永不返回重复项，
    因此按标签过滤的结果是稳定可预期的。
    """

    if tags is None:
        return ()
    if isinstance(tags, str):
        parts: Iterable[str] = (tags,)
    else:
        parts = tags

    collected: list[str] = []
    for raw in parts:
        if raw is None:
            continue
        for chunk in str(raw).replace(",", " ").split():
            text = chunk.strip().lower().lstrip("#")
            if not text:
                continue
            if len(text) > MAX_TAG_LENGTH:
                raise ValidationError(
                    f"tag {text!r} must be at most {MAX_TAG_LENGTH} characters", field="tag"
                )
            if not _TAG_RE.match(text):
                raise ValidationError(
                    f"tag {text!r} must start with a letter or digit and contain only "
                    "letters, digits, '-' and '_'",
                    field="tag",
                )
            if text not in collected:
                collected.append(text)

    if len(collected) > MAX_TAGS:
        raise ValidationError(
            f"a transaction may carry at most {MAX_TAGS} tags", field="tag"
        )
    return tuple(sorted(collected))
