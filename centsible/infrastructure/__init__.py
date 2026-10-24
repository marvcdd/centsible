"""Adapters to the outside world.

Everything that touches SQLite, the file system, the system clock or logging
lives here.  The layer depends on the application and domain layers, never the
other way round, so swapping SQLite for another store means writing new
repositories - not touching business rules.

与外部世界打交道的适配器。

凡是涉及 SQLite、文件系统、系统时钟或日志的代码都放在这里。本层依赖应用层与
领域层，绝不反向依赖，所以把 SQLite 换成别的存储只需编写新的仓储——不必改动
业务规则。
"""

__all__ = [
    "clock",
    "csvio",
    "database",
    "jsonio",
    "logging_setup",
    "repositories",
    "schema",
    "statements",
    "uow",
    "xlsx",
]
