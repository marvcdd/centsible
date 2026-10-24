"""Shared helpers for the test suite.

Tests run against a real SQLite file in a temporary directory (never ``:memory:``
alone), because transactions, foreign keys and migrations are part of what we
want to verify.  The clock is frozen so dates never make a test flaky.

测试套件共用的辅助工具。

测试针对临时目录中的真实 SQLite 文件运行（绝不只用 ``:memory:``），因为事务、外键
和迁移本身就是我们要校验的一部分。时钟被冻结，因此日期不会让测试变得不稳定。
"""

from __future__ import annotations

import csv
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Sequence

from centsible.bootstrap import build_services
from centsible.config import Config
from centsible.domain.transactions import Posting, Transaction
from centsible.infrastructure.clock import FixedClock

DEFAULT_TODAY = date(2025, 3, 15)


def posts(*pairs: tuple[str, str | Decimal | int]) -> tuple[Posting, ...]:
    """Build postings from ``(account, amount)`` pairs.

    由 ``(科目, 金额)`` 二元组构建分录。"""

    return tuple(Posting(code, Decimal(str(amount))) for code, amount in pairs)


def write_statement(
    path: Path,
    rows: Iterable[Sequence[object]],
    *,
    header: Sequence[str] | None = None,
    delimiter: str = ",",
    encoding: str = "utf-8",
) -> Path:
    """Write a small statement file for import tests.

    为导入测试写一个小型银行对账单文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding=encoding, newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        if header is not None:
            writer.writerow(list(header))
        for row in rows:
            writer.writerow(list(row))
    return path


class LedgerTestCase(unittest.TestCase):
    """A throw-away ledger with a frozen clock and the default chart.

    一次性账簿，配备冻结的时钟与默认科目表。"""

    today: date = DEFAULT_TODAY
    chart: str | None = "default"
    currency: str = "CNY"

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.clock = FixedClock(self.today)
        self.config = Config(
            database_path=Path(self._tempdir.name) / "ledger.db",
            currency=self.currency,
        )
        self.services = build_services(self.config, clock=self.clock)
        self.addCleanup(self.services.close)
        if self.chart:
            self.services.accounts.ensure_chart(self.chart)

    # ---------------------------------------------------------------- helpers
    # ---------------------------------------------------------------- 辅助方法
    def record(
        self,
        description: str,
        *pairs: tuple[str, str | Decimal | int],
        day: date | None = None,
        **kwargs: Any,
    ) -> Transaction:
        return self.services.ledger.record(
            date=day or self.today,
            description=description,
            postings=posts(*pairs),
            **kwargs,
        )

    def balance(self, code: str) -> Decimal:
        return self.services.reports.account_balance(code).amount
