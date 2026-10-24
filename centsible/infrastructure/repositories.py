"""SQLite implementations of the repository ports.

Each class is a thin, boring translator between domain objects and rows.  All
business decisions live in the services; the code here is allowed to be dull.

Two conventions are worth remembering:

* money crosses this boundary as integer minor units
  (:func:`~centsible.domain.money.to_minor` / ``from_minor``);
* ``postings`` are always written with a ``position`` so the order a user typed
  the legs in survives a round-trip.

仓储端口接口的 SQLite 实现。

每个类都只是领域对象与数据行之间一层薄而无趣的翻译层。所有业务决策都放在服务里；
这里的代码写得平淡一点是允许的。

有两条约定值得记住：

* 金额跨越这条边界时用最小货币单位的整数表示
  （:func:`~centsible.domain.money.to_minor` / ``from_minor``）；
* ``postings`` 写入时总会带上 ``position``，这样用户录入各条分录的先后顺序
  在往返读写之后依然保持不变。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any, Iterator, Sequence

from centsible.application.ports import (
    AccountTotals,
    AuditEntry,
    ImportBatch,
    LedgerLine,
    TransactionQuery,
)
from centsible.domain.accounts import Account, AccountType, normalize_code
from centsible.domain.budgets import Budget
from centsible.domain.money import from_minor, to_minor
from centsible.domain.recurrence import Frequency, RecurringRule
from centsible.domain.transactions import Posting, Transaction, TransactionStatus
from centsible.errors import ConflictError, DataError, NotFoundError

__all__ = [
    "SqliteAccountRepository",
    "SqliteAuditRepository",
    "SqliteBudgetRepository",
    "SqliteImportRepository",
    "SqliteMetaRepository",
    "SqliteRecurringRepository",
    "SqliteTransactionRepository",
]

_CHUNK_SIZE = 400


# --------------------------------------------------------------------- helpers
# --------------------------------------------------------------------- 辅助函数
def _now(clock: Any) -> str:
    return clock.now().isoformat(timespec="seconds")


def _tags_to_json(tags: Sequence[str]) -> str:
    return json.dumps(list(tags), ensure_ascii=False, separators=(",", ":"))


def _tags_from_json(raw: Any) -> tuple[str, ...]:
    if not raw:
        return ()
    try:
        loaded = json.loads(raw)
    except (TypeError, ValueError) as exc:  # pragma: no cover - only if edited by hand | 仅在手工改动时出现
        raise DataError(f"stored tags are not valid JSON: {raw!r}") from exc
    if not isinstance(loaded, list):  # pragma: no cover - only if edited by hand | 仅在手工改动时出现
        raise DataError(f"stored tags must be a JSON array, found {type(loaded).__name__}")
    return tuple(str(item) for item in loaded)


def _chunks(values: Sequence[int], size: int = _CHUNK_SIZE) -> Iterator[Sequence[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _like_pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _date_bound(column: str, value: date | None, clauses: list[str], params: list[Any], op: str) -> None:
    if value is None:
        return
    clauses.append(f"{column} {op} ?")
    params.append(value.isoformat())


# ------------------------------------------------------------------- accounts
# ------------------------------------------------------------------- 科目
class SqliteAccountRepository:
    """CRUD for the chart of accounts.

    科目表的增删改查。"""

    def __init__(self, connection: sqlite3.Connection, *, exponent: int, clock: Any) -> None:
        self._conn = connection
        self._exponent = exponent
        self._clock = clock

    # ---------------------------------------------------------------- writes
    # ---------------------------------------------------------------- 写入
    def add(self, account: Account) -> Account:
        try:
            cursor = self._conn.execute(
                "INSERT INTO accounts (code, name, type, parent_code, description, archived, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    account.code,
                    account.name,
                    account.type.value,
                    account.parent_code,
                    account.description,
                    int(account.archived),
                    _now(self._clock),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"cannot create account {account.code}: {exc}") from exc
        account_id = cursor.lastrowid
        if account_id is None:
            raise DataError("the database did not report the new account id")
        stored = self._get_by_id(int(account_id))
        if stored is None:  # pragma: no cover - defensive | 防御性代码
            raise DataError(f"account {account.code} disappeared right after insertion")
        return stored

    def update(self, account: Account) -> Account:
        if account.id is None:
            raise DataError("cannot update an account that was never stored")
        try:
            cursor = self._conn.execute(
                "UPDATE accounts SET name = ?, type = ?, parent_code = ?, description = ?, "
                "archived = ? WHERE id = ?",
                (
                    account.name,
                    account.type.value,
                    account.parent_code,
                    account.description,
                    int(account.archived),
                    account.id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"cannot update account {account.code}: {exc}") from exc
        if cursor.rowcount == 0:
            raise NotFoundError(f"account {account.code} does not exist")
        stored = self._get_by_id(account.id)
        if stored is None:  # pragma: no cover - defensive | 防御性代码
            raise DataError(f"account {account.code} disappeared after the update")
        return stored

    def delete(self, code: str) -> None:
        try:
            cursor = self._conn.execute("DELETE FROM accounts WHERE code = ?", (normalize_code(code),))
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"account {code} is still referenced and cannot be deleted") from exc
        if cursor.rowcount == 0:
            raise NotFoundError(f"account {normalize_code(code)} does not exist")

    # ----------------------------------------------------------------- reads
    # ----------------------------------------------------------------- 读取
    def get(self, code: str) -> Account | None:
        row = self._conn.execute(
            "SELECT * FROM accounts WHERE code = ?", (normalize_code(code),)
        ).fetchone()
        return self._to_account(row) if row is not None else None

    def list(self, *, include_archived: bool = True) -> list[Account]:
        sql = "SELECT * FROM accounts"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY code ASC"
        return [self._to_account(row) for row in self._conn.execute(sql)]

    def children_of(self, code: str) -> Sequence[Account]:
        rows = self._conn.execute(
            "SELECT * FROM accounts WHERE parent_code = ? ORDER BY code ASC",
            (normalize_code(code),),
        ).fetchall()
        return [self._to_account(row) for row in rows]

    def has_children(self, code: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM accounts WHERE parent_code = ? LIMIT 1", (normalize_code(code),)
        ).fetchone()
        return row is not None

    def posting_count(self, code: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS total FROM postings WHERE account_code = ?",
            (normalize_code(code),),
        ).fetchone()
        return int(row["total"])

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS total FROM accounts").fetchone()["total"])

    # -------------------------------------------------------------- conversion
    # -------------------------------------------------------------- 转换
    def _get_by_id(self, account_id: int) -> Account | None:
        row = self._conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        return self._to_account(row) if row is not None else None

    @staticmethod
    def _to_account(row: sqlite3.Row) -> Account:
        return Account(
            code=row["code"],
            name=row["name"],
            type=AccountType.parse(row["type"]),
            parent_code=row["parent_code"],
            description=row["description"],
            archived=bool(row["archived"]),
            id=int(row["id"]),
        )


# --------------------------------------------------------------- transactions
# --------------------------------------------------------------- 凭证
class SqliteTransactionRepository:
    """Journal entries, their postings and the aggregates reports need.

    日记账凭证、凭证下的分录，以及报表需要的汇总数据。"""

    def __init__(self, connection: sqlite3.Connection, *, exponent: int, clock: Any) -> None:
        self._conn = connection
        self._exponent = exponent
        self._clock = clock

    # ---------------------------------------------------------------- writes
    # ---------------------------------------------------------------- 写入
    def add(self, transaction: Transaction) -> Transaction:
        try:
            cursor = self._conn.execute(
                "INSERT INTO transactions (date, description, payee, reference, tags, status, "
                "import_batch, void_reason, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    transaction.date.isoformat(),
                    transaction.description,
                    transaction.payee,
                    transaction.reference,
                    _tags_to_json(transaction.tags),
                    transaction.status.value,
                    transaction.import_batch,
                    transaction.void_reason,
                    _now(self._clock),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"cannot store the transaction: {exc}") from exc

        new_transaction_id = cursor.lastrowid
        if new_transaction_id is None:
            raise DataError("the database did not report the new transaction id")
        transaction_id = int(new_transaction_id)
        rows = [
            (
                transaction_id,
                position,
                posting.account_code,
                to_minor(posting.amount, self._exponent),
                posting.memo,
            )
            for position, posting in enumerate(transaction.postings)
        ]
        try:
            self._conn.executemany(
                "INSERT INTO postings (transaction_id, position, account_code, amount_minor, memo) "
                "VALUES (?, ?, ?, ?, ?)",
                rows,
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"cannot store the transaction postings: {exc}") from exc

        stored = self.get(transaction_id)
        if stored is None:  # pragma: no cover - defensive | 防御性代码
            raise DataError(f"transaction #{transaction_id} disappeared right after insertion")
        return stored

    def mark_void(self, transaction_id: int, *, reason: str) -> None:
        cursor = self._conn.execute(
            "UPDATE transactions SET status = 'void', void_reason = ? "
            "WHERE id = ? AND status = 'posted'",
            (reason, transaction_id),
        )
        if cursor.rowcount == 0:
            raise ConflictError(
                f"transaction #{transaction_id} is not an open posted transaction"
            )

    def mark_posted(self, transaction_id: int) -> None:
        cursor = self._conn.execute(
            "UPDATE transactions SET status = 'posted', void_reason = '' "
            "WHERE id = ? AND status = 'void'",
            (transaction_id,),
        )
        if cursor.rowcount == 0:
            raise ConflictError(f"transaction #{transaction_id} is not void")

    # ----------------------------------------------------------------- reads
    # ----------------------------------------------------------------- 读取
    def get(self, transaction_id: int) -> Transaction | None:
        row = self._conn.execute(
            "SELECT * FROM transactions WHERE id = ?", (transaction_id,)
        ).fetchone()
        if row is None:
            return None
        return self._hydrate([row])[0]

    def find_by_reference(self, reference: str) -> Transaction | None:
        row = self._conn.execute(
            "SELECT * FROM transactions WHERE reference = ?", (reference,)
        ).fetchone()
        if row is None:
            return None
        return self._hydrate([row])[0]

    def search(self, query: TransactionQuery) -> list[Transaction]:
        clauses: list[str] = []
        params: list[Any] = []

        if query.period is not None:
            _date_bound("t.date", query.period.start if query.period.start != date.min else None, clauses, params, ">=")
            _date_bound("t.date", query.period.end if query.period.end != date.max else None, clauses, params, "<=")
        if query.status is not None:
            clauses.append("t.status = ?")
            params.append(query.status.value)
        if query.account_code:
            clauses.append(
                "EXISTS (SELECT 1 FROM postings p WHERE p.transaction_id = t.id AND p.account_code = ?)"
            )
            params.append(normalize_code(query.account_code))
        if query.tag:
            clauses.append("t.tags LIKE ? ESCAPE '\\'")
            params.append(f'%"{query.tag.strip().lower().lstrip("#")}"%')
        if query.text:
            needle = _like_pattern(query.text.strip())
            clauses.append(
                "(t.description LIKE ? ESCAPE '\\' OR t.payee LIKE ? ESCAPE '\\' "
                "OR IFNULL(t.reference, '') LIKE ? ESCAPE '\\')"
            )
            params.extend([needle, needle, needle])

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT t.* FROM transactions t{where} ORDER BY t.date DESC, t.id DESC"

        if query.limit is not None:
            sql += " LIMIT ?"
            params.append(int(query.limit))
            if query.offset:
                sql += " OFFSET ?"
                params.append(int(query.offset))
        elif query.offset:
            sql += " LIMIT -1 OFFSET ?"
            params.append(int(query.offset))

        rows = self._conn.execute(sql, params).fetchall()
        return self._hydrate(rows)

    def totals(
        self, *, start: date | None = None, end: date | None = None
    ) -> dict[str, AccountTotals]:
        clauses = ["t.status = 'posted'"]
        params: list[Any] = []
        _date_bound("t.date", start, clauses, params, ">=")
        _date_bound("t.date", end, clauses, params, "<=")

        sql = f"""
            SELECT p.account_code                                   AS code,
                   COALESCE(SUM(CASE WHEN p.amount_minor > 0
                                     THEN p.amount_minor ELSE 0 END), 0)  AS debit_minor,
                   COALESCE(SUM(CASE WHEN p.amount_minor < 0
                                     THEN -p.amount_minor ELSE 0 END), 0) AS credit_minor,
                   COUNT(*)                                          AS entries
            FROM postings p
            JOIN transactions t ON t.id = p.transaction_id
            WHERE {' AND '.join(clauses)}
            GROUP BY p.account_code
        """
        return {
            str(row["code"]): AccountTotals(
                debit=from_minor(int(row["debit_minor"]), self._exponent),
                credit=from_minor(int(row["credit_minor"]), self._exponent),
                entries=int(row["entries"]),
            )
            for row in self._conn.execute(sql, params)
        }

    def type_totals_by_month(
        self, *, start: date | None = None, end: date | None = None
    ) -> dict[str, dict[str, AccountTotals]]:
        clauses = ["t.status = 'posted'"]
        params: list[Any] = []
        _date_bound("t.date", start, clauses, params, ">=")
        _date_bound("t.date", end, clauses, params, "<=")

        sql = f"""
            SELECT substr(t.date, 1, 7)                              AS month,
                   a.type                                            AS account_type,
                   COALESCE(SUM(CASE WHEN p.amount_minor > 0
                                     THEN p.amount_minor ELSE 0 END), 0)  AS debit_minor,
                   COALESCE(SUM(CASE WHEN p.amount_minor < 0
                                     THEN -p.amount_minor ELSE 0 END), 0) AS credit_minor,
                   COUNT(*)                                          AS entries
            FROM postings p
            JOIN transactions t ON t.id = p.transaction_id
            JOIN accounts a     ON a.code = p.account_code
            WHERE {' AND '.join(clauses)}
            GROUP BY month, a.type
        """
        result: dict[str, dict[str, AccountTotals]] = {}
        for row in self._conn.execute(sql, params):
            bucket = result.setdefault(str(row["month"]), {})
            bucket[str(row["account_type"])] = AccountTotals(
                debit=from_minor(int(row["debit_minor"]), self._exponent),
                credit=from_minor(int(row["credit_minor"]), self._exponent),
                entries=int(row["entries"]),
            )
        return result

    def postings(
        self, account_code: str, *, start: date | None = None, end: date | None = None
    ) -> list[LedgerLine]:
        clauses = ["t.status = 'posted'", "p.account_code = ?"]
        params: list[Any] = [normalize_code(account_code)]
        _date_bound("t.date", start, clauses, params, ">=")
        _date_bound("t.date", end, clauses, params, "<=")

        sql = f"""
            SELECT t.date AS entry_date, t.id AS transaction_id, t.description, t.payee,
                   p.memo, p.amount_minor
            FROM postings p
            JOIN transactions t ON t.id = p.transaction_id
            WHERE {' AND '.join(clauses)}
            ORDER BY t.date ASC, t.id ASC, p.position ASC
        """
        return [
            LedgerLine(
                entry_date=date.fromisoformat(str(row["entry_date"])),
                transaction_id=int(row["transaction_id"]),
                description=str(row["description"]),
                payee=str(row["payee"]),
                memo=str(row["memo"]),
                amount=from_minor(int(row["amount_minor"]), self._exponent),
            )
            for row in self._conn.execute(sql, params)
        ]

    def count(self, *, include_void: bool = True) -> int:
        sql = "SELECT COUNT(*) AS total FROM transactions"
        if not include_void:
            sql += " WHERE status = 'posted'"
        return int(self._conn.execute(sql).fetchone()["total"])

    def date_bounds(self) -> tuple[date | None, date | None]:
        row = self._conn.execute(
            "SELECT MIN(date) AS first, MAX(date) AS last FROM transactions WHERE status = 'posted'"
        ).fetchone()
        first = date.fromisoformat(row["first"]) if row["first"] else None
        last = date.fromisoformat(row["last"]) if row["last"] else None
        return first, last

    def unbalanced_transaction_ids(self) -> list[int]:
        """Ids of entries whose postings do not sum to zero.

        The service layer makes this impossible, so a hit here means the file was
        edited outside Centsible.  Voided entries are included on purpose: they
        still have to be internally consistent, or restoring one would corrupt
        the books.

        分录合计不为零的凭证 ID。

        服务层本应让这种情况不可能出现，因此一旦命中就说明文件被 Centsible 之外
        的东西改过。这里故意把已作废的凭证也算进来：它们自身仍必须自洽，否则一旦
        恢复就会把账搞坏。
        """

        rows = self._conn.execute(
            "SELECT transaction_id FROM postings "
            "GROUP BY transaction_id HAVING SUM(amount_minor) <> 0 "
            "ORDER BY transaction_id"
        ).fetchall()
        return [int(row["transaction_id"]) for row in rows]

    # -------------------------------------------------------------- conversion
    # -------------------------------------------------------------- 转换
    def _hydrate(self, rows: Sequence[sqlite3.Row]) -> list[Transaction]:
        if not rows:
            return []
        ids = [int(row["id"]) for row in rows]
        grouped: dict[int, list[Posting]] = {transaction_id: [] for transaction_id in ids}
        for chunk in _chunks(ids):
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self._conn.execute(
                f"SELECT * FROM postings WHERE transaction_id IN ({placeholders}) "
                "ORDER BY transaction_id ASC, position ASC",
                list(chunk),
            )
            for posting_row in cursor:
                grouped[int(posting_row["transaction_id"])].append(self._to_posting(posting_row))
        return [
            self._to_transaction(row, grouped.get(int(row["id"]), [])) for row in rows
        ]

    def _to_posting(self, row: sqlite3.Row) -> Posting:
        return Posting(
            account_code=str(row["account_code"]),
            amount=from_minor(int(row["amount_minor"]), self._exponent),
            memo=str(row["memo"]),
        )

    @staticmethod
    def _to_transaction(row: sqlite3.Row, postings: Sequence[Posting]) -> Transaction:
        return Transaction(
            id=int(row["id"]),
            date=date.fromisoformat(str(row["date"])),
            description=str(row["description"]),
            postings=tuple(postings),
            payee=str(row["payee"]),
            reference=row["reference"],
            tags=_tags_from_json(row["tags"]),
            status=TransactionStatus.parse(str(row["status"])),
            import_batch=row["import_batch"],
            void_reason=str(row["void_reason"] or ""),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )


# -------------------------------------------------------------------- budgets
# -------------------------------------------------------------------- 预算
class SqliteBudgetRepository:
    """Monthly plans, one row per account and month.

    月度计划，每个科目每个月一行。"""

    def __init__(self, connection: sqlite3.Connection, *, exponent: int, clock: Any) -> None:
        self._conn = connection
        self._exponent = exponent
        self._clock = clock

    def upsert(self, budget: Budget) -> Budget:
        self._conn.execute(
            "INSERT INTO budgets (account_code, month, amount_minor, note) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(account_code, month) DO UPDATE SET "
            "amount_minor = excluded.amount_minor, note = excluded.note",
            (
                budget.account_code,
                budget.month,
                to_minor(budget.amount, self._exponent),
                budget.note,
            ),
        )
        stored = self.get(budget.account_code, budget.month)
        if stored is None:  # pragma: no cover - defensive | 防御性代码
            raise DataError(f"budget {budget.account_code}@{budget.month} disappeared after upsert")
        return stored

    def get(self, account_code: str, month: str) -> Budget | None:
        row = self._conn.execute(
            "SELECT * FROM budgets WHERE account_code = ? AND month = ?",
            (normalize_code(account_code), month),
        ).fetchone()
        return self._to_budget(row) if row is not None else None

    def list(
        self, *, start_month: str | None = None, end_month: str | None = None
    ) -> list[Budget]:
        clauses: list[str] = []
        params: list[Any] = []
        if start_month is not None:
            clauses.append("month >= ?")
            params.append(start_month)
        if end_month is not None:
            clauses.append("month <= ?")
            params.append(end_month)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM budgets{where} ORDER BY month ASC, account_code ASC", params
        ).fetchall()
        return [self._to_budget(row) for row in rows]

    def delete(self, account_code: str, month: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM budgets WHERE account_code = ? AND month = ?",
            (normalize_code(account_code), month),
        )
        return cursor.rowcount > 0

    def count(self) -> int:
        return int(self._conn.execute("SELECT COUNT(*) AS total FROM budgets").fetchone()["total"])

    def _to_budget(self, row: sqlite3.Row) -> Budget:
        return Budget(
            id=int(row["id"]),
            account_code=str(row["account_code"]),
            month=str(row["month"]),
            amount=from_minor(int(row["amount_minor"]), self._exponent),
            note=str(row["note"]),
        )


# ------------------------------------------------------------------ recurring
# ------------------------------------------------------------------ 周期规则
class SqliteRecurringRepository:
    """Standing entries: a rule row plus its balanced template postings.

    周期规则记录：一行规则数据，加上与之平衡的模板分录。"""

    def __init__(self, connection: sqlite3.Connection, *, exponent: int, clock: Any) -> None:
        self._conn = connection
        self._exponent = exponent
        self._clock = clock

    def add(self, rule: RecurringRule) -> RecurringRule:
        try:
            cursor = self._conn.execute(
                "INSERT INTO recurring_rules (name, description, payee, frequency, interval, "
                "start_date, next_date, end_date, tags, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                self._rule_params(rule),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"cannot create recurring rule {rule.name!r}: {exc}") from exc
        new_rule_id = cursor.lastrowid
        if new_rule_id is None:
            raise DataError("the database did not report the new recurring rule id")
        rule_id = int(new_rule_id)
        self._replace_postings(rule_id, rule.postings)
        return self._require(rule_id)

    def update(self, rule: RecurringRule) -> RecurringRule:
        if rule.id is None:
            raise DataError("cannot update a recurring rule that was never stored")
        try:
            cursor = self._conn.execute(
                "UPDATE recurring_rules SET name = ?, description = ?, payee = ?, frequency = ?, "
                "interval = ?, start_date = ?, next_date = ?, end_date = ?, tags = ?, active = ? "
                "WHERE id = ?",
                (*self._rule_params(rule)[:10], rule.id),
            )
        except sqlite3.IntegrityError as exc:
            raise ConflictError(f"cannot update recurring rule {rule.name!r}: {exc}") from exc
        if cursor.rowcount == 0:
            raise NotFoundError(f"recurring rule #{rule.id} does not exist")
        self._replace_postings(rule.id, rule.postings)
        return self._require(rule.id)

    def get(self, rule_id: int) -> RecurringRule | None:
        row = self._conn.execute(
            "SELECT * FROM recurring_rules WHERE id = ?", (rule_id,)
        ).fetchone()
        if row is None:
            return None
        postings = self._postings_for(rule_id)
        return self._to_rule(row, postings)

    def find_by_name(self, name: str) -> RecurringRule | None:
        row = self._conn.execute(
            "SELECT * FROM recurring_rules WHERE name = ?", (str(name).strip(),)
        ).fetchone()
        if row is None:
            return None
        return self._to_rule(row, self._postings_for(int(row["id"])))

    def list(self) -> list[RecurringRule]:
        rows = self._conn.execute(
            "SELECT * FROM recurring_rules ORDER BY next_date ASC, name ASC"
        ).fetchall()
        if not rows:
            return []
        grouped = self._postings_for_many([int(row["id"]) for row in rows])
        return [self._to_rule(row, grouped.get(int(row["id"]), [])) for row in rows]

    def delete(self, rule_id: int) -> bool:
        cursor = self._conn.execute("DELETE FROM recurring_rules WHERE id = ?", (rule_id,))
        return cursor.rowcount > 0

    # -------------------------------------------------------------- internals
    # -------------------------------------------------------------- 内部实现
    def _require(self, rule_id: int) -> RecurringRule:
        rule = self.get(rule_id)
        if rule is None:  # pragma: no cover - defensive | 防御性代码
            raise DataError(f"recurring rule #{rule_id} disappeared")
        return rule

    def _rule_params(self, rule: RecurringRule) -> tuple[Any, ...]:
        return (
            rule.name,
            rule.description,
            rule.payee,
            rule.frequency.value,
            rule.interval,
            rule.start_date.isoformat(),
            rule.next_date.isoformat(),
            rule.end_date.isoformat() if rule.end_date else None,
            _tags_to_json(rule.tags),
            int(rule.active),
            _now(self._clock),
        )

    def _replace_postings(self, rule_id: int, postings: Sequence[Posting]) -> None:
        self._conn.execute("DELETE FROM recurring_postings WHERE rule_id = ?", (rule_id,))
        self._conn.executemany(
            "INSERT INTO recurring_postings (rule_id, position, account_code, amount_minor, memo) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (
                    rule_id,
                    position,
                    posting.account_code,
                    to_minor(posting.amount, self._exponent),
                    posting.memo,
                )
                for position, posting in enumerate(postings)
            ],
        )

    def _postings_for(self, rule_id: int) -> Sequence[Posting]:
        rows = self._conn.execute(
            "SELECT * FROM recurring_postings WHERE rule_id = ? ORDER BY position ASC",
            (rule_id,),
        ).fetchall()
        return [self._to_posting(row) for row in rows]

    def _postings_for_many(self, rule_ids: Sequence[int]) -> dict[int, Sequence[Posting]]:
        grouped: dict[int, list[Posting]] = {rule_id: [] for rule_id in rule_ids}
        for chunk in _chunks(list(rule_ids)):
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self._conn.execute(
                f"SELECT * FROM recurring_postings WHERE rule_id IN ({placeholders}) "
                "ORDER BY rule_id ASC, position ASC",
                list(chunk),
            )
            for row in cursor:
                grouped[int(row["rule_id"])].append(self._to_posting(row))
        return {rule_id: tuple(postings) for rule_id, postings in grouped.items()}

    def _to_posting(self, row: sqlite3.Row) -> Posting:
        return Posting(
            account_code=str(row["account_code"]),
            amount=from_minor(int(row["amount_minor"]), self._exponent),
            memo=str(row["memo"]),
        )

    @staticmethod
    def _to_rule(row: sqlite3.Row, postings: Sequence[Posting]) -> RecurringRule:
        return RecurringRule(
            id=int(row["id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            payee=str(row["payee"]),
            frequency=Frequency.parse(str(row["frequency"])),
            interval=int(row["interval"]),
            start_date=date.fromisoformat(str(row["start_date"])),
            next_date=date.fromisoformat(str(row["next_date"])),
            end_date=date.fromisoformat(str(row["end_date"])) if row["end_date"] else None,
            tags=_tags_from_json(row["tags"]),
            active=bool(row["active"]),
            postings=tuple(postings),
        )


# --------------------------------------------------------------------- import
# --------------------------------------------------------------------- 导入
class SqliteImportRepository:
    """Import batches and the fingerprints that make re-imports idempotent.

    导入批次，以及让重复导入保持幂等的指纹。"""

    def __init__(self, connection: sqlite3.Connection, *, clock: Any) -> None:
        self._conn = connection
        self._clock = clock

    def add_batch(self, batch: ImportBatch) -> ImportBatch:
        cursor = self._conn.execute(
            "INSERT INTO import_batches (source, filename, checksum, imported_at, rows_read, "
            "rows_imported, rows_skipped) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                batch.source,
                batch.filename,
                batch.checksum,
                batch.imported_at.isoformat(timespec="seconds"),
                batch.rows_read,
                batch.rows_imported,
                batch.rows_skipped,
            ),
        )
        new_batch_id = cursor.lastrowid
        if new_batch_id is None:
            raise DataError("the database did not report the new import batch id")
        batch_id = int(new_batch_id)
        row = self._conn.execute(
            "SELECT * FROM import_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if row is None:  # pragma: no cover - defensive | 防御性代码
            raise DataError(f"import batch #{batch_id} disappeared")
        return self._to_batch(row)

    def has_fingerprint(self, fingerprint: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM import_entries WHERE fingerprint = ? LIMIT 1", (fingerprint,)
        ).fetchone()
        return row is not None

    def add_fingerprint(
        self, fingerprint: str, *, batch_id: int, transaction_id: int | None
    ) -> None:
        self._conn.execute(
            "INSERT INTO import_entries (batch_id, fingerprint, transaction_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (batch_id, fingerprint, transaction_id, _now(self._clock)),
        )

    def finish_batch(
        self, batch_id: int, *, rows_read: int, rows_imported: int, rows_skipped: int
    ) -> None:
        self._conn.execute(
            "UPDATE import_batches SET rows_read = ?, rows_imported = ?, rows_skipped = ? "
            "WHERE id = ?",
            (rows_read, rows_imported, rows_skipped, batch_id),
        )

    def recent_batches(self, limit: int = 10) -> list[ImportBatch]:
        rows = self._conn.execute(
            "SELECT * FROM import_batches ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [self._to_batch(row) for row in rows]

    @staticmethod
    def _to_batch(row: sqlite3.Row) -> ImportBatch:
        return ImportBatch(
            id=int(row["id"]),
            source=str(row["source"]),
            filename=str(row["filename"]),
            checksum=str(row["checksum"]),
            imported_at=datetime.fromisoformat(str(row["imported_at"])),
            rows_read=int(row["rows_read"]),
            rows_imported=int(row["rows_imported"]),
            rows_skipped=int(row["rows_skipped"]),
        )


# ----------------------------------------------------------------------- meta
# ----------------------------------------------------------------------- 元数据（metadata）
class SqliteMetaRepository:
    """Key/value store for ledger-wide facts (the lock date lives here).

    账簿级事实的键值存储（锁账日期就放在这里）。"""

    def __init__(self, connection: sqlite3.Connection, *, clock: Any) -> None:
        self._conn = connection
        self._clock = clock

    def get(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row is not None else None

    def set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def delete(self, key: str) -> None:
        self._conn.execute("DELETE FROM meta WHERE key = ?", (key,))

    def all(self) -> dict[str, str]:
        return {
            str(row["key"]): str(row["value"])
            for row in self._conn.execute("SELECT key, value FROM meta ORDER BY key")
        }


# ---------------------------------------------------------------------- audit
# ---------------------------------------------------------------------- 审计
class SqliteAuditRepository:
    """Append-only trail of every state change.

    记录每一次状态变化的只追加轨迹。"""

    def __init__(self, connection: sqlite3.Connection, *, clock: Any) -> None:
        self._conn = connection
        self._clock = clock

    def record(
        self,
        *,
        action: str,
        entity: str,
        entity_id: str | int | None = None,
        detail: str = "",
        actor: str = "cli",
    ) -> None:
        self._conn.execute(
            "INSERT INTO audit_log (at, actor, action, entity, entity_id, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                _now(self._clock),
                actor,
                action,
                entity,
                None if entity_id is None else str(entity_id),
                detail,
            ),
        )

    def recent(self, limit: int = 20) -> list[AuditEntry]:
        rows = self._conn.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
        return [
            AuditEntry(
                at=datetime.fromisoformat(str(row["at"])),
                actor=str(row["actor"]),
                action=str(row["action"]),
                entity=str(row["entity"]),
                entity_id=row["entity_id"],
                detail=str(row["detail"]),
            )
            for row in rows
        ]

    def count(self) -> int:
        """Return how many audit lines the ledger holds.

        返回账本中审计记录的条数。"""

        row = self._conn.execute("SELECT COUNT(*) AS total FROM audit_log").fetchone()
        return int(row["total"]) if row is not None else 0
