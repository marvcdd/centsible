"""The SQLite schema, versioned in Python.

Migrations are applied in order and recorded in ``PRAGMA user_version``.  They
live in code (not in a ``.sql`` file) so the tool stays a single, portable
Python package, and so a migration is always versioned together with the
repositories that use it.

Design notes
------------
* Every monetary column stores **integer minor units** (cents), never floats.
* Dates are ISO-8601 ``TEXT`` so they compare lexicographically in SQL.
* Postings reference the account *code* rather than the surrogate id: reports
  join on it and a human reading the database sees meaningful rows.
* ``transactions.status`` is ``posted`` or ``void``; nothing is ever deleted,
  so the journal is append-only apart from the ``void`` flag.

在 Python 代码中做版本管理的 SQLite 表结构。

迁移按顺序执行，并记录在 ``PRAGMA user_version`` 中。它们以代码形式存在
（而不是放在 ``.sql`` 文件里），这样本工具就仍然是一个单一、可移植的 Python
包，而每次迁移也总能与使用它的仓储一起做版本管理。

设计说明
--------
* 每个金额列都存储**最小货币单位**的整数值（分），绝不使用浮点数。
* 日期为 ISO-8601 ``TEXT``，因此在 SQL 中可以按字典序比较。
* 分录引用科目 *代码* 而不是代理主键：报表据此联接，人工查看数据库时看到的也是
  有意义的行。
* ``transactions.status`` 的取值为 ``posted`` 或 ``void``；任何数据都不会被删除，
  因此除 ``void`` 标记之外，日记账是只追加的。
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["MIGRATIONS", "Migration", "SCHEMA_VERSION"]


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    description: str
    statements: tuple[str, ...]


_MIGRATION_1 = (
    """
    CREATE TABLE accounts (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        code        TEXT    NOT NULL UNIQUE,
        name        TEXT    NOT NULL,
        type        TEXT    NOT NULL
                    CHECK (type IN ('asset','liability','equity','income','expense')),
        parent_code TEXT    REFERENCES accounts(code) ON DELETE RESTRICT ON UPDATE CASCADE,
        description TEXT    NOT NULL DEFAULT '',
        archived    INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
        created_at  TEXT    NOT NULL
    )
    """,
    "CREATE INDEX idx_accounts_parent ON accounts(parent_code)",
    """
    CREATE TABLE import_batches (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        source        TEXT    NOT NULL,
        filename      TEXT    NOT NULL,
        checksum      TEXT    NOT NULL,
        imported_at   TEXT    NOT NULL,
        rows_read     INTEGER NOT NULL DEFAULT 0,
        rows_imported INTEGER NOT NULL DEFAULT 0,
        rows_skipped  INTEGER NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE transactions (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        date         TEXT    NOT NULL,
        description  TEXT    NOT NULL,
        payee        TEXT    NOT NULL DEFAULT '',
        reference    TEXT,
        tags         TEXT    NOT NULL DEFAULT '[]',
        status       TEXT    NOT NULL DEFAULT 'posted'
                     CHECK (status IN ('posted','void')),
        import_batch INTEGER REFERENCES import_batches(id) ON DELETE SET NULL,
        void_reason  TEXT    NOT NULL DEFAULT '',
        created_at   TEXT    NOT NULL
    )
    """,
    "CREATE INDEX idx_transactions_date ON transactions(date)",
    "CREATE INDEX idx_transactions_status ON transactions(status)",
    "CREATE UNIQUE INDEX idx_transactions_reference ON transactions(reference) "
    "WHERE reference IS NOT NULL",
    """
    CREATE TABLE postings (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
        position       INTEGER NOT NULL,
        account_code   TEXT    NOT NULL REFERENCES accounts(code) ON DELETE RESTRICT
                               ON UPDATE CASCADE,
        amount_minor   INTEGER NOT NULL CHECK (amount_minor <> 0),
        memo           TEXT    NOT NULL DEFAULT '',
        UNIQUE (transaction_id, position)
    )
    """,
    "CREATE INDEX idx_postings_account ON postings(account_code)",
    "CREATE INDEX idx_postings_transaction ON postings(transaction_id)",
    """
    CREATE TABLE budgets (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        account_code TEXT    NOT NULL REFERENCES accounts(code) ON DELETE CASCADE
                             ON UPDATE CASCADE,
        month        TEXT    NOT NULL,
        amount_minor INTEGER NOT NULL CHECK (amount_minor >= 0),
        note         TEXT    NOT NULL DEFAULT '',
        UNIQUE (account_code, month)
    )
    """,
    """
    CREATE TABLE recurring_rules (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT    NOT NULL UNIQUE,
        description TEXT    NOT NULL,
        payee       TEXT    NOT NULL DEFAULT '',
        frequency   TEXT    NOT NULL
                    CHECK (frequency IN ('daily','weekly','monthly','quarterly','yearly')),
        interval    INTEGER NOT NULL DEFAULT 1 CHECK (interval >= 1),
        start_date  TEXT    NOT NULL,
        next_date   TEXT    NOT NULL,
        end_date    TEXT,
        tags        TEXT    NOT NULL DEFAULT '[]',
        active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
        created_at  TEXT    NOT NULL
    )
    """,
    "CREATE INDEX idx_recurring_next_date ON recurring_rules(next_date)",
    """
    CREATE TABLE recurring_postings (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id      INTEGER NOT NULL REFERENCES recurring_rules(id) ON DELETE CASCADE,
        position     INTEGER NOT NULL,
        account_code TEXT    NOT NULL REFERENCES accounts(code) ON DELETE RESTRICT
                             ON UPDATE CASCADE,
        amount_minor INTEGER NOT NULL CHECK (amount_minor <> 0),
        memo         TEXT    NOT NULL DEFAULT '',
        UNIQUE (rule_id, position)
    )
    """,
    """
    CREATE TABLE import_entries (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id       INTEGER NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
        fingerprint    TEXT    NOT NULL UNIQUE,
        transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
        created_at     TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE audit_log (
        id        INTEGER PRIMARY KEY AUTOINCREMENT,
        at        TEXT NOT NULL,
        actor     TEXT NOT NULL DEFAULT 'cli',
        action    TEXT NOT NULL,
        entity    TEXT NOT NULL,
        entity_id TEXT,
        detail    TEXT NOT NULL DEFAULT ''
    )
    """,
    "CREATE INDEX idx_audit_at ON audit_log(at)",
    "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)

MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=1, description="initial journal schema", statements=_MIGRATION_1),
)

SCHEMA_VERSION = MIGRATIONS[-1].version
