"""SQLite connection handling and schema migration.

Connections are opened in *autocommit* mode (``isolation_level=None``) and
transactions are started explicitly by the unit of work.  That removes the
surprise of a statement silently opening a transaction and gives one obvious
place - :class:`~centsible.infrastructure.uow.SqliteUnitOfWork` - where a
transaction begins and ends.

``:memory:`` databases use a private shared-cache URI plus one long-lived anchor
connection, so the schema survives across the short-lived connections the unit
of work opens.  Each :class:`Database` instance gets its own URI, so parallel
tests never see each other's data.

SQLite 连接管理与表结构迁移。

连接以 *自动提交* 模式（``isolation_level=None``）打开，数据库事务由工作单元
显式开启。这样既消除了某条语句悄悄开启事务的意外，也让事务的开始与结束都收敛到
唯一一处明确的位置——:class:`~centsible.infrastructure.uow.SqliteUnitOfWork`。

``:memory:`` 数据库使用一个私有的共享缓存 URI，外加一个长期存活的锚点连接，
因此表结构能在工作单元反复打开的短生命周期连接之间保留下来。每个
:class:`Database` 实例都拥有自己的 URI，所以并行运行的测试彼此看不到对方的数据。
"""

from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from centsible.errors import DataError, NotFoundError, ValidationError
from centsible.infrastructure.schema import MIGRATIONS, SCHEMA_VERSION

__all__ = ["Database"]

_MEMORY = ":memory:"

# Tables every Centsible ledger must have; a backup that lacks them is a foreign
# SQLite file and must not be allowed to replace the ledger.
# 每个 Centsible 账簿都必须具备的表；缺少其中任何一个的备份就是外来 SQLite 文件，
# 不允许它替换账簿。
_CENTSIBLE_TABLES = ("accounts", "transactions", "postings", "meta")


class Database:
    """A SQLite file (or a private in-memory database) with its migrations.

    一个 SQLite 文件（或私有的内存数据库）及其迁移。"""

    def __init__(self, path: str | Path) -> None:
        raw = str(path)
        self._is_memory = raw == _MEMORY
        self._path = Path(raw)
        self._uri = (
            f"file:centsible-memory-{id(self)}?mode=memory&cache=shared"
            if self._is_memory
            else ""
        )
        self._anchor: sqlite3.Connection | None = None

    # ------------------------------------------------------------- properties
    # ------------------------------------------------------------- 属性
    @property
    def path(self) -> Path:
        return self._path

    @property
    def display_path(self) -> str:
        return _MEMORY if self._is_memory else str(self._path)

    @property
    def is_memory(self) -> bool:
        return self._is_memory

    def exists(self) -> bool:
        return self._is_memory or self._path.exists()

    # -------------------------------------------------------------- connecting
    # -------------------------------------------------------------- 建立连接
    def connect(self) -> sqlite3.Connection:
        """Open a configured connection; the caller must close it.

        打开一条已配置好的连接；调用方必须负责关闭它。"""

        if self._is_memory:
            self._ensure_anchor()
            connection = sqlite3.connect(
                self._uri, uri=True, timeout=10.0, isolation_level=None
            )
            return self._configure(connection)

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self._path, timeout=10.0, isolation_level=None
            )
        except sqlite3.Error as exc:
            raise DataError(f"cannot open the ledger database {self._path}: {exc}") from exc
        return self._configure(connection)

    def _configure(self, connection: sqlite3.Connection) -> sqlite3.Connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        if not self._is_memory:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def _ensure_anchor(self) -> sqlite3.Connection:
        """Return the long-lived connection that keeps an in-memory schema alive.

        返回维持内存表结构存活的长期连接。"""

        anchor = self._anchor
        if anchor is None:
            if not self._is_memory:
                raise DataError(
                    "an anchor connection only exists for in-memory databases"
                )
            anchor = self._configure(
                sqlite3.connect(self._uri, uri=True, timeout=10.0, isolation_level=None)
            )
            self._anchor = anchor
            self._apply_migrations(anchor)
        return anchor

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            yield connection
        finally:
            connection.close()

    def close(self) -> None:
        if self._anchor is not None:
            self._anchor.close()
            self._anchor = None

    # -------------------------------------------------------------- migrations
    # -------------------------------------------------------------- 迁移
    def schema_version(self, connection: sqlite3.Connection | None = None) -> int:
        if connection is not None:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])
        with self.connection() as own:
            return int(own.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> int:
        """Apply pending migrations and return the resulting schema version.

        应用尚未执行的迁移，并返回迁移之后的表结构版本。"""

        if self._is_memory:
            return self.schema_version(self._ensure_anchor())
        with self.connection() as connection:
            return self._apply_migrations(connection)

    def _apply_migrations(self, connection: sqlite3.Connection) -> int:
        current = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current > SCHEMA_VERSION:
            raise DataError(
                f"the database schema is version {current} but this program only knows "
                f"version {SCHEMA_VERSION}; upgrade Centsible"
            )

        for migration in MIGRATIONS:
            if migration.version <= current:
                continue
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in migration.statements:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {int(migration.version)}")
                connection.execute("COMMIT")
            except sqlite3.Error as exc:
                connection.execute("ROLLBACK")
                raise DataError(
                    f"migration {migration.version} ({migration.description}) failed: {exc}"
                ) from exc
        return self.schema_version(connection)

    # --------------------------------------------------------------- diagnosis
    # --------------------------------------------------------------- 诊断
    def integrity_check(self) -> list[str]:
        """Return integrity problems; an empty list means the file is healthy.

        返回完整性检查发现的问题；列表为空表示文件是健康的。"""

        problems: list[str] = []
        with self.connection() as connection:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
            for row in rows:
                message = str(row[0])
                if message.lower() != "ok":
                    problems.append(message)
            for row in connection.execute("PRAGMA foreign_key_check").fetchall():
                problems.append(
                    f"foreign key violation in {row[0]} row {row[1]} -> {row[2]}"
                )
        return problems

    def size_bytes(self) -> int:
        if self._is_memory:
            return 0
        total = 0
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(f"{self._path}{suffix}")
            if candidate.exists():
                total += candidate.stat().st_size
        return total

    def backup_to(self, target: str | Path) -> Path:
        """Write a consistent copy of the database to ``target``.

        把数据库的一致性副本写入 ``target``。"""

        destination = Path(target)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            sink = sqlite3.connect(destination)
        except (OSError, sqlite3.Error) as exc:
            raise ValidationError(
                f"cannot write the backup {destination}: {exc}", field="path"
            ) from exc
        try:
            with self.connection() as source:
                source.backup(sink)
            sink.commit()
        except sqlite3.Error as exc:
            raise DataError(
                f"cannot copy the ledger into {destination}: {exc}"
            ) from exc
        finally:
            sink.close()
        return destination

    def restore_from(self, source: str | Path) -> Path:
        """Replace the ledger with a verified copy of ``source``.

        The file is checked before anything is touched: it must exist, pass
        ``PRAGMA integrity_check``, carry a schema version this program knows,
        and contain the Centsible tables.  Only then are the current ledger file
        (and any stale WAL side files) removed and replaced with a byte copy of
        the backup, which :meth:`backup_to` already wrote as a consistent,
        standalone file.

        用 ``source`` 的经验证副本替换当前账簿。

        动手之前先检查：文件必须存在、通过 ``PRAGMA integrity_check``、表结构版本
        是本程序认识的，并且包含 Centsible 的表。全部通过后，才删除当前账簿文件
        （连同残留的 WAL 附属文件），换成备份的字节副本——:meth:`backup_to` 写出的
        本来就是一份一致、自包含的文件。
        """

        origin = self.validate_backup(source)

        for suffix in ("", "-wal", "-shm"):
            stale = Path(f"{self._path}{suffix}")
            if stale.exists():
                stale.unlink()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, self._path)
        return self._path

    def validate_backup(self, source: str | Path) -> Path:
        """Check, without touching anything, that ``source`` is a usable backup.

        Returns the resolved path so callers can reuse the answer.  This is the
        gate :meth:`restore_from` runs behind, exposed separately so a caller can
        tell a missing or corrupt backup from a ledger that merely needs
        ``--force`` before deciding which refusal to show.

        在不改动任何东西的前提下，检查 ``source`` 是否为可用备份。

        返回解析后的路径供调用方复用。它就是 :meth:`restore_from` 所用的那道门，
        单独暴露是为了让调用方能在决定报哪种拒绝之前，区分“备份缺失或损坏”与
        “账簿只是需要 ``--force``”。
        """

        if self._is_memory:
            raise ValidationError(
                "cannot restore into an in-memory database", field="path"
            )
        origin = Path(source)
        if not origin.exists():
            raise NotFoundError(f"backup file not found: {origin}")
        if not origin.is_file():
            raise ValidationError(f"{origin} is not a file", field="path")
        if origin.resolve() == self._path.resolve():
            raise ValidationError(
                "the backup and the ledger are the same file", field="path"
            )

        probe = sqlite3.connect(origin)
        try:
            try:
                problems = [
                    str(row[0])
                    for row in probe.execute("PRAGMA integrity_check").fetchall()
                ]
            except sqlite3.DatabaseError as exc:
                raise DataError(
                    f"{origin} is not a readable SQLite database: {exc}"
                ) from exc
            if [problem.lower() for problem in problems] != ["ok"]:
                raise DataError(
                    f"{origin} fails its integrity check: "
                    f"{', '.join(problems[:3])}"
                )
            version = int(probe.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise DataError(
                    f"{origin} uses schema version {version} but this program "
                    f"only knows version {SCHEMA_VERSION}"
                )
            tables = {
                str(row[0])
                for row in probe.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            missing = sorted(set(_CENTSIBLE_TABLES) - tables)
            if missing:
                raise DataError(
                    f"{origin} is not a Centsible database "
                    f"(missing tables: {', '.join(missing)})"
                )
        finally:
            probe.close()
        return origin


    def describe(self) -> dict[str, Any]:
        return {
            "path": self.display_path,
            "exists": self.exists(),
            "in_memory": self._is_memory,
            "size_bytes": self.size_bytes(),
            "schema_version": self.migrate() if self.exists() else 0,
        }
