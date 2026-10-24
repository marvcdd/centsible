"""Clock implementations.

Time is a dependency: tests freeze it, reports default to "today", and the
void operation dates its reversal entry.  Injecting it keeps all of that
deterministic.

时钟实现。

时间是依赖项：测试会冻结它，报表默认使用“今天”，作废操作会用它给冲正分录
定日期。把时钟注入进来，才能让上述这一切保持确定性。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

__all__ = ["FixedClock", "SystemClock"]


class SystemClock:
    """The real clock, in UTC for timestamps and local time for dates.

    真实的时钟：时间戳用 UTC，日期用本地时间。"""

    @staticmethod
    def today() -> date:
        return date.today()

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """A clock stuck at one instant, with optional manual advance.

    固定在同一时刻的时钟，可选择手动向前推进。"""

    def __init__(self, today: date, *, now: datetime | None = None) -> None:
        self._today = today
        if now is None:
            now = datetime(today.year, today.month, today.day, 12, 0, tzinfo=timezone.utc)
        self._now = now

    def today(self) -> date:
        return self._today

    def now(self) -> datetime:
        return self._now

    def advance(self, days: int) -> "FixedClock":
        self._today = self._today + timedelta(days=days)
        self._now = self._now + timedelta(days=days)
        return self

