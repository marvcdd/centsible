"""Composition root.

The only place that knows about *all* layers: it reads the configuration,
opens the database, applies migrations and wires the services together.  Every
other module receives its collaborators through its constructor, which is what
makes the whole thing testable.

组合根（启动装配）。

这是唯一了解*所有*分层的模块：它读取配置、打开数据库、执行迁移，并把各项服务
装配到一起。其他每个模块都通过构造函数接收自己的协作者，这正是整套设计可测试
的原因。
"""

from __future__ import annotations

from dataclasses import dataclass

from centsible.application.account_service import AccountService
from centsible.application.budget_service import BudgetService
from centsible.application.export_service import ExportService
from centsible.application.import_service import ImportService
from centsible.application.ledger_service import CURRENCY_KEY, LedgerService
from centsible.application.ports import Clock, UnitOfWorkFactory
from centsible.application.recurring_service import RecurringService
from centsible.application.report_pack import ReportPackService
from centsible.application.report_service import ReportService
from centsible.application.settings import LedgerSettings
from centsible.config import Config
from centsible.infrastructure.clock import SystemClock
from centsible.infrastructure.database import Database
from centsible.infrastructure.logging_setup import get_logger
from centsible.infrastructure.statements import StatementReaderRegistry
from centsible.infrastructure.uow import SqliteUnitOfWorkFactory

__all__ = ["AppServices", "build_services"]

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class AppServices:
    """The assembled object graph.

    装配完成的对象图。"""

    config: Config
    settings: LedgerSettings
    database: Database
    clock: Clock
    unit_of_work: UnitOfWorkFactory
    accounts: AccountService
    ledger: LedgerService
    reports: ReportService
    budgets: BudgetService
    recurring: RecurringService
    imports: ImportService
    exports: ExportService
    report_pack: ReportPackService

    def close(self) -> None:
        self.database.close()


def build_services(
    config: Config, *, clock: Clock | None = None, migrate: bool = True
) -> AppServices:
    """Wire the application for one ledger database.

    为一本账簿数据库装配整个应用。"""

    settings = LedgerSettings.from_config(config)
    active_clock: Clock = clock or SystemClock()
    database = Database(config.resolved_database_path())
    if migrate:
        version = database.migrate()
        _log.debug("ledger %s at schema version %s", database.display_path, version)

    # The currency decides what the stored integer amounts mean, so an existing
    # ledger is authoritative: a stale config must not reinterpret its numbers.
    # 币种决定了所存整数金额的含义，因此已存在的账簿具有权威性：
    # 过时的配置不得重新解释这些数字。
    probe = SqliteUnitOfWorkFactory(database, active_clock, settings)
    with probe() as uow:
        recorded = uow.meta.get(CURRENCY_KEY)
    if recorded is not None:
        if recorded != settings.currency:
            _log.warning(
                "ledger %s was created in %s; using %s instead of the configured %s",
                database.display_path,
                recorded,
                recorded,
                settings.currency,
            )
            settings = LedgerSettings(
                currency=recorded,
                fiscal_year_start_month=settings.fiscal_year_start_month,
            )

    factory = SqliteUnitOfWorkFactory(database, active_clock, settings)

    ledger = LedgerService(factory, active_clock, settings)
    accounts = AccountService(factory, active_clock, settings)
    reports = ReportService(factory, active_clock, settings)
    budgets = BudgetService(factory, active_clock, settings)
    recurring = RecurringService(factory, active_clock, settings, ledger=ledger)
    imports = ImportService(
        factory,
        active_clock,
        settings,
        reader=StatementReaderRegistry(),
        ledger=ledger,
    )
    exports = ExportService(factory, active_clock, settings)
    report_pack = ReportPackService(
        factory,
        active_clock,
        settings,
        reports=reports,
        budgets=budgets,
        recurring=recurring,
        exports=exports,
    )

    return AppServices(
        config=config,
        settings=settings,
        database=database,
        clock=active_clock,
        unit_of_work=factory,
        accounts=accounts,
        ledger=ledger,
        reports=reports,
        budgets=budgets,
        recurring=recurring,
        imports=imports,
        exports=exports,
        report_pack=report_pack,
    )
