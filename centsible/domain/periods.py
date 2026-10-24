"""Calendar periods, month arithmetic and fiscal years.

Reports are always scoped to a :class:`DatePeriod`.  Half-open intervals were
rejected on purpose: an accounting period has an *inclusive* end date, because
"2025-03" must contain the 31st.

日历区间、月份运算与会计年度。

报表始终以 :class:`DatePeriod` 为作用范围。这里刻意排除了半开区间：会计期间的截止
日是包含在内的，因为“2025-03”必须涵盖当月 31 日。
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator

from centsible.errors import ValidationError

__all__ = [
    "DatePeriod",
    "ITER_MONTHS_LIMIT",
    "coerce_date",
    "fiscal_year_period",
    "fiscal_year_start",
    "iter_months",
    "month_bounds",
    "month_key",
    "parse_date",
    "parse_month_key",
    "parse_period",
    "shift_months",
]

ITER_MONTHS_LIMIT = 1200
_MONTH_KEY_RE = re.compile(r"^(\d{4})-(\d{2})$")
_QUARTER_RE = re.compile(r"^(\d{4})-?Q([1-4])$", re.IGNORECASE)


def coerce_date(value: date) -> date:
    """Reject ``datetime`` (a frequent source of timezone confusion).

    拒绝 ``datetime``（时区混淆的常见来源）。"""

    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    raise ValidationError("expected a calendar date", field="date")


@dataclass(frozen=True, slots=True)
class DatePeriod:
    """An inclusive range of calendar days.

    一个首尾均包含在内的日历日期区间。"""

    start: date
    end: date

    def __post_init__(self) -> None:
        coerce_date(self.start)
        coerce_date(self.end)
        if self.start > self.end:
            raise ValidationError(
                f"period start {self.start.isoformat()} is after its end {self.end.isoformat()}",
                field="period",
            )

    # ------------------------------------------------------------- factories
    # ------------------------------------------------------------- 工厂方法
    @classmethod
    def for_month(cls, year: int, month: int) -> "DatePeriod":
        if not 1 <= int(month) <= 12:
            raise ValidationError(f"month must be between 1 and 12, got {month}", field="month")
        if not 1 <= int(year) <= 9999:
            raise ValidationError(f"year out of range: {year}", field="year")
        last_day = calendar.monthrange(int(year), int(month))[1]
        return cls(date(int(year), int(month), 1), date(int(year), int(month), last_day))

    @classmethod
    def for_quarter(cls, year: int, quarter: int) -> "DatePeriod":
        if not 1 <= int(quarter) <= 4:
            raise ValidationError(f"quarter must be between 1 and 4, got {quarter}", field="quarter")
        first = 3 * (int(quarter) - 1) + 1
        return cls(cls.for_month(year, first).start, cls.for_month(year, first + 2).end)

    @classmethod
    def for_year(cls, year: int) -> "DatePeriod":
        if not 1 <= int(year) <= 9999:
            raise ValidationError(f"year out of range: {year}", field="year")
        return cls(date(int(year), 1, 1), date(int(year), 12, 31))

    @classmethod
    def for_month_key(cls, key: str) -> "DatePeriod":
        return month_bounds(key)

    @classmethod
    def all_time(cls) -> "DatePeriod":
        return cls(date.min, date.max)

    @classmethod
    def up_to(cls, day: date) -> "DatePeriod":
        return cls(date.min, day)

    @classmethod
    def from_day(cls, day: date) -> "DatePeriod":
        return cls(day, day)

    # ------------------------------------------------------------ predicates
    # ------------------------------------------------------------ 判定方法
    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def overlaps(self, other: "DatePeriod") -> bool:
        return self.start <= other.end and other.start <= self.end

    @property
    def is_all_time(self) -> bool:
        return self.start == date.min and self.end == date.max

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    @property
    def label(self) -> str:
        """Compact human label such as ``2025-03``, ``2025`` or a date range.

        供人阅读的简明标签，例如 ``2025-03``、``2025`` 或某个日期区间。"""

        if self.is_all_time:
            return "all time"
        if self.start == date.min:
            return f"until {self.end.isoformat()}"
        if self.end == date.max:
            return f"from {self.start.isoformat()}"
        if self.start.day == 1 and self.end == DatePeriod.for_month(self.start.year, self.start.month).end:
            return month_key(self.start)
        if self.start == date(self.start.year, 1, 1) and self.end == date(self.start.year, 12, 31):
            return str(self.start.year)
        if self.start == self.end:
            return self.start.isoformat()
        return f"{self.start.isoformat()}..{self.end.isoformat()}"

    def to_dict(self) -> dict[str, str | None]:
        return {
            "start": None if self.start == date.min else self.start.isoformat(),
            "end": None if self.end == date.max else self.end.isoformat(),
            "label": self.label,
        }

    def __str__(self) -> str:  # pragma: no cover - display helper | 仅用于显示的辅助方法
        return self.label


# ---------------------------------------------------------------- conversions
# ---------------------------------------------------------------- 转换函数
def month_key(day: date) -> str:
    """``date(2025, 3, 17)`` -> ``"2025-03"``.

    取某一天对应的 ``YYYY-MM`` 月份键。"""

    return f"{day.year:04d}-{day.month:02d}"


def parse_month_key(key: str) -> tuple[int, int]:
    """``"2025-03"`` -> ``(2025, 3)`` with validation.

    把 ``"2025-03"`` 解析为 ``(2025, 3)``，并做校验。"""

    if not isinstance(key, str):
        raise ValidationError("month must look like YYYY-MM", field="month")
    match = _MONTH_KEY_RE.match(key.strip())
    if not match:
        raise ValidationError(f"{key!r} is not a month (expected YYYY-MM)", field="month")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise ValidationError(f"{key!r} has an impossible month", field="month")
    if not 1 <= year <= 9999:
        raise ValidationError(f"{key!r} has an impossible year", field="month")
    return year, month


def month_bounds(key: str) -> DatePeriod:
    year, month = parse_month_key(key)
    return DatePeriod.for_month(year, month)


def iter_months(start: date, end: date) -> Iterator[str]:
    """Yield every ``YYYY-MM`` between two dates, inclusive.

    逐个产出两个日期之间（含两端）的每一个 ``YYYY-MM``。"""

    if start > end:
        return
    cursor = date(start.year, start.month, 1)
    limit = date(end.year, end.month, 1)
    produced = 0
    while cursor <= limit:
        yield month_key(cursor)
        produced += 1
        if produced > ITER_MONTHS_LIMIT:  # pragma: no cover - defensive guard | 防御性防护
            raise ValidationError("month range is unreasonably large", field="period")
        cursor = shift_months(cursor, 1)


def shift_months(day: date, months: int) -> date:
    """Add ``months`` to ``day``, clamping to the end of short months.

    ``2025-01-31`` plus one month is ``2025-02-28``, never an invalid date.

    给 ``day`` 加上 ``months`` 个月，遇到天数不足的月份则截断到该月月末。

    ``2025-01-31`` 加一个月得到 ``2025-02-28``，绝不会产生非法日期。
    """

    if isinstance(months, bool) or not isinstance(months, int):
        raise TypeError("months must be an int")
    total = day.year * 12 + (day.month - 1) + months
    year, month_index = divmod(total, 12)
    month = month_index + 1
    if not 1 <= year <= 9999:
        raise ValidationError("resulting date is out of range", field="date")
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(day.day, last_day))


def parse_date(text: str, *, date_format: str | None = None) -> date:
    """Parse ``YYYY-MM-DD`` (or a custom ``strftime`` pattern) into a date.

    把 ``YYYY-MM-DD``（或自定义的 ``strftime`` 格式）解析为日期。"""

    if isinstance(text, date) and not hasattr(text, "hour"):
        return text
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("a date is required", field="date")
    raw = text.strip()

    if date_format and date_format != "%Y-%m-%d":
        from datetime import datetime as _datetime

        try:
            return _datetime.strptime(raw, date_format).date()
        except ValueError as exc:
            raise ValidationError(
                f"{raw!r} does not match the configured date format {date_format}", field="date"
            ) from exc

    try:
        return date.fromisoformat(raw)
    except ValueError:
        pass

    if _MONTH_KEY_RE.match(raw):
        return month_bounds(raw).start

    raise ValidationError(f"{raw!r} is not a date (expected YYYY-MM-DD)", field="date")


def fiscal_year_start(day: date, start_month: int = 1) -> date:
    """First day of the fiscal year that contains ``day``.

    包含 ``day`` 的那个会计年度的第一天。"""

    if isinstance(start_month, bool) or not isinstance(start_month, int) or not 1 <= start_month <= 12:
        raise ValidationError("fiscal year start month must be 1..12", field="fiscal_year_start_month")
    year = day.year if day.month >= start_month else day.year - 1
    if year < 1:
        raise ValidationError("fiscal year starts before year 1", field="date")
    return date(year, start_month, 1)


def fiscal_year_period(day: date, start_month: int = 1) -> DatePeriod:
    """The fiscal year containing ``day``.

    包含 ``day`` 的整个会计年度。"""

    start = fiscal_year_start(day, start_month)
    end = shift_months(start, 12) - timedelta(days=1)
    return DatePeriod(start, end)


def parse_period(
    start_text: str | None,
    end_text: str | None,
    *,
    date_format: str | None = None,
) -> DatePeriod | None:
    """Build a period from CLI style bounds.

    * both empty -> ``None`` (meaning "everything")
    * ``YYYY-MM`` expands to the whole month, on either side
    * an empty end means "open ended", an empty start means "from the beginning"

    根据命令行风格的起止边界构造会计期间。

    * 两端都为空 -> ``None``（表示“全部”）
    * ``YYYY-MM`` 出现在起始或结束任一侧时，都会展开为整月
    * 结束为空表示“不设上限”，起始为空表示“从头开始”
    """

    if not start_text and not end_text:
        return None

    start = _parse_bound(start_text, date_format=date_format, is_end=False)
    end = _parse_bound(end_text, date_format=date_format, is_end=True)
    if start is None and end is None:
        return None
    if start is None:
        return DatePeriod(date.min, end)  # type: ignore[arg-type]
    if end is None:
        return DatePeriod(start, date.max)
    return DatePeriod(start, end)


def _parse_bound(
    text: str | None, *, date_format: str | None, is_end: bool
) -> date | None:
    if not text:
        return None
    raw = text.strip()
    if not raw:
        return None
    if _MONTH_KEY_RE.match(raw):
        bounds = month_bounds(raw)
        return bounds.end if is_end else bounds.start
    quarter = _QUARTER_RE.match(raw)
    if quarter is not None:
        bounds = DatePeriod.for_quarter(int(quarter.group(1)), int(quarter.group(2)))
        return bounds.end if is_end else bounds.start
    return parse_date(raw, date_format=date_format)
