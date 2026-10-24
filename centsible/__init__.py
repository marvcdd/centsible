"""Centsible - a small, exact double-entry bookkeeping engine.

The package is organised in concentric layers; dependencies only ever point
inwards:

``domain``
    Pure business objects and rules.  No I/O, no SQL, no argument parsing.
``application``
    Use cases.  Orchestrates repositories (through protocols) and the domain.
``infrastructure``
    Adapters: SQLite persistence, CSV/JSON files, system clock, logging.
``interface``
    The command line front end.

Everything is standard library only: the tool must run on a bare Python 3.11+
installation.

Centsible——一个小巧而精确的复式记账引擎。

整个包采用同心圆式分层组织，依赖只允许向内指向：

``domain``
    纯业务对象与规则。不做 I/O，不碰 SQL，不解析命令行参数。
``application``
    用例层。通过端口接口编排仓储与领域对象。
``infrastructure``
    适配器：SQLite 持久化、CSV/JSON 文件、系统时钟、日志。
``interface``
    命令行前端。

全部只使用标准库：该工具必须能在裸装的 Python 3.11+ 环境上运行。
"""

from centsible.config import Config
from centsible.domain.money import DEFAULT_CURRENCY, Money
from centsible.errors import (
    CentsibleError,
    ConfigurationError,
    ConflictError,
    CurrencyMismatchError,
    DataError,
    LedgerLockedError,
    NotFoundError,
    UnbalancedEntryError,
    ValidationError,
)

__version__ = "1.0.0"

__all__ = [
    "CentsibleError",
    "Config",
    "ConfigurationError",
    "ConflictError",
    "CurrencyMismatchError",
    "DEFAULT_CURRENCY",
    "DataError",
    "LedgerLockedError",
    "Money",
    "NotFoundError",
    "UnbalancedEntryError",
    "ValidationError",
    "__version__",
]
