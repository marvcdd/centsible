"""Recurring transactions (standing entries).

A rule stores a *balanced template* plus a schedule.  Running the schedule
materialises ordinary transactions, so recurring entries are indistinguishable
from hand-written ones afterwards - which keeps every report and query simple.

Scheduling is anchored on the rule's start date rather than on the previous
occurrence.  Without an anchor, a monthly rule starting on the 31st would drift
backwards (31 Jan -> 28 Feb -> 28 Mar) instead of returning to month end.

周期凭证（常设分录）。

一条规则保存一份*平衡的模板*外加一套排期。执行排期会生成普通凭证，
因此周期分录事后与手工录入的分录毫无区别——这让每份报表、每次查询都保持简单。

排期以规则的开始日期为锚点，而不是以上一次发生日为准。若没有锚点，
从 31 日开始的月度规则会逐月提前（1 月 31 日 -> 2 月 28 日 -> 3 月 28 日），
而不是回到月末。
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

from centsible.domain.periods import shift_months
from centsible.domain.transactions import (
    MAX_DESCRIPTION_LENGTH,
    Posting,
    Transaction,
    check_balanced,
    normalize_tags,
)
from centsible.errors import ValidationError

__all__ = [
    "Frequency",
    "MAX_OCCURRENCES",
    "RecurringRule",
    "next_occurrence",
]

MAX_OCCURRENCES = 5000


class Frequency(str, Enum):
    """How often a standing entry repeats.

    常设分录的重复频率。"""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"

    @property
    def months_per_step(self) -> int:
        return {
            Frequency.MONTHLY: 1,
            Frequency.QUARTERLY: 3,
            Frequency.YEARLY: 12,
        }[self]

    @property
    def is_month_based(self) -> bool:
        return self in (Frequency.MONTHLY, Frequency.QUARTERLY, Frequency.YEARLY)

    @classmethod
    def parse(cls, value: "Frequency | str") -> "Frequency":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValidationError(
                "frequency must be one of " + ", ".join(item.value for item in cls),
                field="frequency",
            )
        text = value.strip().lower()
        aliases = {
            "d": cls.DAILY,
            "day": cls.DAILY,
            "daily": cls.DAILY,
            "w": cls.WEEKLY,
            "week": cls.WEEKLY,
            "weekly": cls.WEEKLY,
            "m": cls.MONTHLY,
            "month": cls.MONTHLY,
            "monthly": cls.MONTHLY,
            "q": cls.QUARTERLY,
            "quarter": cls.QUARTERLY,
            "quarterly": cls.QUARTERLY,
            "y": cls.YEARLY,
            "year": cls.YEARLY,
            "yearly": cls.YEARLY,
            "annually": cls.YEARLY,
        }
        try:
            return aliases[text]
        except KeyError:
            allowed = ", ".join(item.value for item in cls)
            raise ValidationError(
                f"{value!r} is not a frequency; choose one of {allowed}", field="frequency"
            ) from None

    def __str__(self) -> str:  # pragma: no cover - display helper | 仅用于显示的辅助方法
        return self.value


def next_occurrence(
    current: date,
    frequency: Frequency | str,
    interval: int = 1,
    *,
    anchor: date | None = None,
) -> date:
    """The first occurrence strictly after ``current``.

    ``anchor`` preserves the intended day of month: a monthly rule anchored on
    the 31st yields 28/29 February and then 31 March again.

    严格晚于 ``current`` 的第一个发生日。

    ``anchor`` 用来保留期望的“每月第几日”：以 31 日为锚点的月度规则
    会先落在 2 月 28/29 日，随后又回到 3 月 31 日。
    """

    freq = Frequency.parse(frequency)
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 1:
        raise ValidationError("interval must be a positive integer", field="interval")

    if freq is Frequency.DAILY:
        return current + timedelta(days=interval)
    if freq is Frequency.WEEKLY:
        return current + timedelta(weeks=interval)

    base_day = (anchor or current).day
    target = shift_months(date(current.year, current.month, 1), freq.months_per_step * interval)
    last_day = calendar.monthrange(target.year, target.month)[1]
    return date(target.year, target.month, min(base_day, last_day))


@dataclass(frozen=True, slots=True)
class RecurringRule:
    """A scheduled entry template.

    The template is validated exactly like a real transaction: it must be
    balanced and have at least two postings, so a rule can never produce
    invalid entries later on.

    一条带排期的分录模板。

    模板与真实凭证按完全相同的规则校验：它必须平衡，且至少包含两条分录，
    因此规则日后不可能产出不合法的分录。
    """

    name: str
    description: str
    frequency: Frequency
    postings: tuple[Posting, ...]
    start_date: date
    next_date: date
    interval: int = 1
    end_date: date | None = None
    payee: str = ""
    tags: tuple[str, ...] = ()
    active: bool = True
    id: int | None = None

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        if not name:
            raise ValidationError("recurring rule name is required", field="name")
        if len(name) > 100:
            raise ValidationError("recurring rule name must be at most 100 characters", field="name")
        object.__setattr__(self, "name", name)

        description = str(self.description).strip()
        if not description:
            raise ValidationError("recurring rule description is required", field="description")
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValidationError(
                f"description must be at most {MAX_DESCRIPTION_LENGTH} characters",
                field="description",
            )
        object.__setattr__(self, "description", description)

        object.__setattr__(self, "frequency", Frequency.parse(self.frequency))

        postings = tuple(self.postings)
        if len(postings) < 2:
            raise ValidationError("a rule template needs at least two postings", field="postings")
        for posting in postings:
            if not isinstance(posting, Posting):
                raise TypeError("rule postings must be Posting instances")
        residual = check_balanced(postings)
        if residual != 0:
            raise ValidationError(
                f"rule template does not balance (difference {residual:f})", field="postings"
            )
        object.__setattr__(self, "postings", postings)

        if not isinstance(self.start_date, date) or hasattr(self.start_date, "hour"):
            raise ValidationError("start date must be a calendar date", field="start_date")
        if not isinstance(self.next_date, date) or hasattr(self.next_date, "hour"):
            raise ValidationError("next date must be a calendar date", field="next_date")
        if self.next_date < self.start_date:
            raise ValidationError(
                "next occurrence cannot be before the start date", field="next_date"
            )
        if self.end_date is not None:
            if not isinstance(self.end_date, date) or hasattr(self.end_date, "hour"):
                raise ValidationError("end date must be a calendar date", field="end_date")
            if self.end_date < self.start_date:
                raise ValidationError(
                    "end date cannot be before the start date", field="end_date"
                )

        if isinstance(self.interval, bool) or not isinstance(self.interval, int) or self.interval < 1:
            raise ValidationError("interval must be a positive integer", field="interval")

        payee = str(self.payee or "").strip()
        if len(payee) > 120:
            raise ValidationError("payee must be at most 120 characters", field="payee")
        object.__setattr__(self, "payee", payee)
        object.__setattr__(self, "tags", normalize_tags(self.tags))
        object.__setattr__(self, "active", bool(self.active))

    # ------------------------------------------------------------- scheduling
    # ------------------------------------------------------------- 排期
    def due_dates(self, until: date) -> list[date]:
        """Occurrence dates that are due on or before ``until``.

        Returns an empty list for paused rules and for rules whose window has
        already closed.

        截止到 ``until``（含当天）应发生的各个日期。

        对于已暂停的规则，以及时间窗口已经关闭的规则，返回空列表。
        """

        if not self.active:
            return []
        if self.end_date is not None and self.next_date > self.end_date:
            return []
        horizon = until if self.end_date is None else min(until, self.end_date)

        dates: list[date] = []
        cursor = self.next_date
        while cursor <= horizon:
            dates.append(cursor)
            if len(dates) >= MAX_OCCURRENCES:  # pragma: no cover - defensive guard | 防御性防护
                raise ValidationError("too many occurrences to materialise", field="frequency")
            cursor = next_occurrence(
                cursor, self.frequency, self.interval, anchor=self.start_date
            )
        return dates

    def advance(self, count: int = 1) -> "RecurringRule":
        """Return a copy whose ``next_date`` has moved ``count`` occurrences on.

        返回一个副本，其 ``next_date`` 已向后推进 ``count`` 个发生日。"""

        if count < 0:
            raise ValidationError("count must not be negative", field="count")
        cursor = self.next_date
        for _ in range(count):
            cursor = next_occurrence(
                cursor, self.frequency, self.interval, anchor=self.start_date
            )
        return replace(self, next_date=cursor)

    def paused(self) -> "RecurringRule":
        return replace(self, active=False)

    def resumed(self) -> "RecurringRule":
        return replace(self, active=True)

    def with_changes(self, **changes: Any) -> "RecurringRule":
        """Return an updated copy; validation runs again.

        返回更新后的副本；校验会重新执行。"""

        return replace(self, **changes)

    def transaction_for(self, day: date) -> Transaction:
        """Build the concrete journal entry for one occurrence date.

        为某一个发生日构建具体的凭证。"""

        return Transaction(
            date=day,
            description=self.description,
            postings=self.postings,
            payee=self.payee,
            tags=tuple(sorted({*self.tags, "recurring"})),
        )

    @property
    def schedule_label(self) -> str:
        unit = {
            Frequency.DAILY: "day",
            Frequency.WEEKLY: "week",
            Frequency.MONTHLY: "month",
            Frequency.QUARTERLY: "quarter",
            Frequency.YEARLY: "year",
        }[self.frequency]
        if self.interval == 1:
            return f"every {unit}"
        return f"every {self.interval} {unit}s"

    @property
    def template_total(self) -> Decimal:
        """Sum of the debit legs - the size of one occurrence.

        各借方分录之和——单次发生的金额规模。"""

        return sum(
            (posting.amount for posting in self.postings if posting.amount > 0), Decimal(0)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "frequency": self.frequency.value,
            "interval": self.interval,
            "start_date": self.start_date.isoformat(),
            "next_date": self.next_date.isoformat(),
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "payee": self.payee,
            "tags": list(self.tags),
            "active": self.active,
            "postings": [posting.to_dict() for posting in self.postings],
        }
