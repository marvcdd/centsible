"""The execution context shared by every CLI command.

Services are built lazily, so read-only commands such as ``config show`` never
open (or create) a database.  The context also centralises output: a command
either prints text or emits JSON, never both.

每个 CLI 命令共用的执行上下文。

各服务按需构建，因此像 ``config show`` 这样的只读命令永远不会打开（或创建）
数据库。该上下文还统一管理输出：一个命令要么打印文本，要么输出 JSON，绝不两者都输出。
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any, Callable, TextIO

from centsible.application.settings import LedgerSettings
from centsible.bootstrap import AppServices, build_services
from centsible.config import Config
from centsible.domain.money import Money
from centsible.infrastructure.jsonio import dumps
__all__ = ["CliContext"]

ServicesFactory = Callable[..., AppServices]


@dataclass
class CliContext:
    config: Config
    out: TextIO = field(default_factory=lambda: sys.stdout)
    err: TextIO = field(default_factory=lambda: sys.stderr)
    json_output: bool = False
    services_factory: ServicesFactory = build_services
    clock: Any = None
    _services: AppServices | None = field(default=None, init=False, repr=False)

    # --------------------------------------------------------------- services
    # --------------------------------------------------------------- 服务
    @property
    def services(self) -> AppServices:
        services = self._services
        if services is None:
            services = self.services_factory(self.config, clock=self.clock)
            self._services = services
        return services

    @property
    def services_ready(self) -> bool:
        return self._services is not None

    @property
    def settings(self) -> LedgerSettings:
        """Effective ledger settings.

        Once the ledger is open these come from the services, because an existing
        ledger's recorded currency wins over the configuration.  Before that they
        are derived from the configuration - which is why opening the ledger is
        lazy but always authoritative.

        当前生效的账簿设置。

        账簿一旦打开，设置便来自服务层，因为已有账簿中记录的币种优先于配置。
        在此之前，它们由配置推导而来——这就是为什么打开账簿虽然是惰性的，却始终具有权威性。
        """

        return self.services.settings

    def money(self, value: Any) -> Money:
        return self.settings.money(value)

    def reconfigure(self, **changes: Any) -> None:
        """Adjust the configuration before any service was built.

        在任何服务被构建之前调整配置。"""

        if self._services is not None:
            raise RuntimeError("cannot reconfigure after the services were built")
        self.config = self.config.with_overrides(**changes)

    def close(self) -> None:
        if self._services is not None:
            self._services.close()
            self._services = None

    # ----------------------------------------------------------------- output
    # ----------------------------------------------------------------- 输出
    def print(self, *lines: object) -> None:
        for line in lines:
            print(line, file=self.out)

    def emit_json(self, payload: Any) -> None:
        self.print(dumps(payload).rstrip("\n"))
