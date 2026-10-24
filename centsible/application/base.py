"""Shared plumbing for application services.

Every service receives a :data:`~centsible.application.ports.UnitOfWorkFactory`
and a :class:`~centsible.application.ports.Clock`, never a database handle.  The
helper here implements the rule that makes composition safe: a service may join
an existing unit of work (so several services can cooperate inside one
transaction) or own a new one, and an owned unit of work is committed only when
the use case completes without an exception.

应用服务共用的基础设施。

每个服务拿到的是一个 :data:`~centsible.application.ports.UnitOfWorkFactory` 和
一个 :class:`~centsible.application.ports.Clock`，绝不会拿到数据库连接句柄。这里的
辅助代码实现了让组合安全的那条规则：一个服务既可以加入已有的工作单元（于是多个服务
能在同一个事务里协作），也可以自己持有一个新的工作单元，而自己持有的工作单元只有在
用例无异常跑完时才会提交。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from centsible.application.ports import Clock, UnitOfWork, UnitOfWorkFactory
from centsible.application.settings import LedgerSettings

__all__ = ["ServiceBase"]


class ServiceBase:
    """Constructor and transaction helper shared by all services.

    所有服务共用的构造函数与事务辅助逻辑。"""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        clock: Clock,
        settings: LedgerSettings | None = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._settings = settings or LedgerSettings()

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def settings(self) -> LedgerSettings:
        return self._settings

    @property
    def uow_factory(self) -> UnitOfWorkFactory:
        return self._uow_factory

    @contextmanager
    def _transaction(
        self, uow: UnitOfWork | None = None
    ) -> Iterator[tuple[UnitOfWork, bool]]:
        """Yield ``(unit_of_work, owned)``.

        When ``uow`` is given the caller owns the transaction and nothing is
        committed here.  Otherwise a fresh unit of work is opened and committed
        on success; leaving the block with an exception rolls back.

        产出 ``(unit_of_work, owned)``。

        传入 ``uow`` 时，事务归调用方所有，此处不做任何提交。否则会新开一个
        工作单元并在成功时提交；若带着异常离开代码块则回滚。
        """

        if uow is not None:
            yield uow, False
            return

        with self._uow_factory() as owned:
            yield owned, True
            owned.commit()
