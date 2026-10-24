"""The transactional boundary.

One unit of work is one SQLite transaction.  Repositories are created lazily on
the same connection, so a use case that touches several of them either commits
completely or not at all.  Forgetting to call :meth:`SqliteUnitOfWork.commit`
is safe: leaving the block without a commit rolls back.

事务边界。

一个工作单元就是一次 SQLite 事务。各仓储都在这同一条连接上按需创建，因此某个
用例即使同时访问多个仓储，要么整体提交，要么完全不提交。忘记调用
:meth:`SqliteUnitOfWork.commit` 也不会有问题：没有提交就离开代码块即会回滚。
"""

from __future__ import annotations

import sqlite3
from typing import Any, Literal

from centsible.application.ports import Clock, UnitOfWork
from centsible.application.settings import LedgerSettings
from centsible.errors import DataError
from centsible.infrastructure.database import Database
from centsible.infrastructure.repositories import (
    SqliteAccountRepository,
    SqliteAuditRepository,
    SqliteBudgetRepository,
    SqliteImportRepository,
    SqliteMetaRepository,
    SqliteRecurringRepository,
    SqliteTransactionRepository,
)

__all__ = ["SqliteUnitOfWork", "SqliteUnitOfWorkFactory"]


class SqliteUnitOfWork:
    """A single transaction exposing every repository.

    单个事务，对外暴露全部仓储。"""

    def __init__(
        self, database: Database, clock: Clock, settings: LedgerSettings
    ) -> None:
        self._database = database
        self._clock = clock
        self._settings = settings
        self._conn: sqlite3.Connection | None = None
        self._committed = False
        self._repositories: dict[str, Any] = {}

    # ---------------------------------------------------------- transaction
    # ---------------------------------------------------------- 事务
    def __enter__(self) -> "SqliteUnitOfWork":
        if self._conn is not None:
            raise DataError("this unit of work is already active")
        connection = self._database.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            connection.close()
            raise DataError(f"cannot start a transaction: {exc}") from exc
        self._conn = connection
        self._committed = False
        self._repositories.clear()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> Literal[False]:
        try:
            if exc_type is not None or not self._committed:
                self.rollback()
        finally:
            if self._conn is not None:
                self._conn.close()
            self._conn = None
            self._repositories.clear()
        return False

    def commit(self) -> None:
        connection = self._connection()
        try:
            connection.execute("COMMIT")
        except sqlite3.Error as exc:
            raise DataError(f"cannot commit the transaction: {exc}") from exc
        self._committed = True

    def rollback(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute("ROLLBACK")
        except sqlite3.Error:  # pragma: no cover - already rolled back / no transaction | 已回滚 / 无事务
            pass
        self._committed = False

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            raise DataError(
                "the unit of work is not active; use 'with uow_factory() as uow:'"
            )
        return self._conn

    # --------------------------------------------------------- repositories
    # --------------------------------------------------------- 仓储
    @property
    def accounts(self) -> SqliteAccountRepository:
        if "accounts" not in self._repositories:
            self._repositories["accounts"] = SqliteAccountRepository(
                self._connection(), exponent=self._settings.exponent, clock=self._clock
            )
        return self._repositories["accounts"]

    @property
    def transactions(self) -> SqliteTransactionRepository:
        if "transactions" not in self._repositories:
            self._repositories["transactions"] = SqliteTransactionRepository(
                self._connection(), exponent=self._settings.exponent, clock=self._clock
            )
        return self._repositories["transactions"]

    @property
    def budgets(self) -> SqliteBudgetRepository:
        if "budgets" not in self._repositories:
            self._repositories["budgets"] = SqliteBudgetRepository(
                self._connection(), exponent=self._settings.exponent, clock=self._clock
            )
        return self._repositories["budgets"]

    @property
    def recurring(self) -> SqliteRecurringRepository:
        if "recurring" not in self._repositories:
            self._repositories["recurring"] = SqliteRecurringRepository(
                self._connection(), exponent=self._settings.exponent, clock=self._clock
            )
        return self._repositories["recurring"]

    @property
    def imports(self) -> SqliteImportRepository:
        if "imports" not in self._repositories:
            self._repositories["imports"] = SqliteImportRepository(
                self._connection(), clock=self._clock
            )
        return self._repositories["imports"]

    @property
    def meta(self) -> SqliteMetaRepository:
        if "meta" not in self._repositories:
            self._repositories["meta"] = SqliteMetaRepository(
                self._connection(), clock=self._clock
            )
        return self._repositories["meta"]

    @property
    def audit(self) -> SqliteAuditRepository:
        if "audit" not in self._repositories:
            self._repositories["audit"] = SqliteAuditRepository(
                self._connection(), clock=self._clock
            )
        return self._repositories["audit"]


class SqliteUnitOfWorkFactory:
    """Callable factory handed to the application services.

    交给应用服务调用的可调用工厂。"""

    def __init__(self, database: Database, clock: Clock, settings: LedgerSettings) -> None:
        self._database = database
        self._clock = clock
        self._settings = settings

    def __call__(self) -> UnitOfWork:
        return SqliteUnitOfWork(self._database, self._clock, self._settings)
