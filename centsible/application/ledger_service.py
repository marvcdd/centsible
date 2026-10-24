"""Recording, voiding and locking journal entries.

This service is the gatekeeper of the journal.  Before a transaction reaches the
database it must be balanced and well formed (domain), dated sensibly (no future
dates by default), inside an open period, and it may only touch accounts that
exist, are not archived and are postable leaves.  A duplicate ``reference`` is
rejected, which gives imports and integrations a natural idempotency key.

Voiding is the only way to take back an entry.  It keeps the row (and its reason)
for the audit trail while removing it from every balance, and it is refused for
closed periods so reported figures cannot be rewritten in place.

日记账凭证的记录、作废与锁账。

本服务是日记账的守门人。凭证在进入数据库之前必须平衡、格式合规（领域层），
日期要合理（默认不允许未来日期），位于未关闭的会计期间内，并且只能涉及存在
的、未归档的可过账叶子科目。重复的 ``reference`` 会被拒绝，这为导入和集成
提供了天然的幂等键。

作废是撤回凭证的唯一方式。它把凭证从所有余额中移除，同时保留该行记录（以及
作废原因）以维护审计轨迹；已关闭的会计期间不允许作废，这样已经报告过的数字
就不会被就地改写。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Sequence

from centsible.application.base import ServiceBase
from centsible.application.ports import TransactionQuery, UnitOfWork
from centsible.domain.accounts import normalize_code
from centsible.domain.money import normalize_currency
from centsible.domain.periods import DatePeriod
from centsible.domain.transactions import (
    Posting,
    Transaction,
    TransactionStatus,
    normalize_tags,
)
from centsible.errors import (
    ConflictError,
    DataError,
    LedgerLockedError,
    NotFoundError,
    ValidationError,
)

__all__ = ["LedgerService", "LOCK_DATE_KEY", "CURRENCY_KEY"]

#: Key in the ``meta`` table holding the last closed day.
#: meta 表中保存最后已关闭日的键。
LOCK_DATE_KEY = "lock_date"

#: Key in the ``meta`` table holding the ledger's currency.
#: meta 表中保存账簿币种的键。
#:
#: Amounts are stored as integer minor units, so the currency decides what a
#: stored number *means*.  Recording it once makes it impossible to silently
#: reinterpret a CNY ledger as if it were USD.
#: 金额以整数最小货币单位存储，因此币种决定了所存数字的“含义”。币种只登记一次，
#: 此后就不可能再悄悄地把一个人民币账簿重新解释成美元账簿。
CURRENCY_KEY = "currency"


class LedgerService(ServiceBase):
    """Write side of the journal.

    日记账的写入侧。"""

    # ------------------------------------------------------------------ record
    # ------------------------------------------------------------------ 记录凭证
    def record(
        self,
        *,
        date: date,
        description: str,
        postings: Sequence[Posting],
        payee: str = "",
        reference: str | None = None,
        tags: Sequence[str] | str | None = None,
        import_batch: int | None = None,
        allow_future: bool = False,
        uow: UnitOfWork | None = None,
    ) -> Transaction:
        """Validate and store one balanced journal entry.

        校验并保存一笔平衡的凭证。"""

        transaction = Transaction(
            date=date,
            description=description,
            postings=tuple(postings),
            payee=payee,
            reference=reference,
            tags=normalize_tags(tags),
            import_batch=import_batch,
        )

        with self._transaction(uow) as (work, _owned):
            self._assert_date_allowed(work, transaction.date, allow_future=allow_future)
            self._require_postable(work, transaction.accounts)

            if transaction.reference is not None:
                existing = work.transactions.find_by_reference(transaction.reference)
                if existing is not None:
                    raise ConflictError(
                        f"reference {transaction.reference!r} is already used by "
                        f"transaction #{existing.id}",
                        reference=transaction.reference,
                        transaction_id=existing.id,
                    )

            stored = work.transactions.add(transaction)
            work.audit.record(
                action="transaction.record",
                entity="transaction",
                entity_id=stored.id,
                detail=(
                    f"{stored.date.isoformat()} {stored.description} "
                    f"({stored.total_debits:f})"
                ),
            )
        return stored

    # -------------------------------------------------------------------- void
    # -------------------------------------------------------------------- 作废 (void)
    def void(self, transaction_id: int, *, reason: str) -> Transaction:
        """Cancel an entry without deleting it.

        The row stays in the journal with status ``void`` and the reason, so the
        decision remains auditable, but it stops contributing to every balance -
        as if it had never been posted.  An entry dated inside a closed period
        cannot be voided, because that would change figures which were already
        reported.

        作废一笔凭证而不删除它。

        该行仍留在日记账中，状态为 ``void`` 并附有作废原因，因此该决定依然可审计；
        但它不再计入任何余额——就像从未入账一样。日期落在已关闭会计期间内的凭证
        不能作废，因为那会改变已经报告过的数字。
        """

        reason = str(reason or "").strip()
        if not reason:
            raise ValidationError(
                "a reason is required when voiding a transaction", field="reason"
            )

        with self._transaction() as (work, _owned):
            original = work.transactions.get(transaction_id)
            if original is None:
                raise NotFoundError(f"transaction #{transaction_id} does not exist")
            if original.is_void:
                raise ConflictError(f"transaction #{transaction_id} is already void")

            self._assert_unlocked(work, original.date)

            work.transactions.mark_void(transaction_id, reason=reason)
            work.audit.record(
                action="transaction.void",
                entity="transaction",
                entity_id=transaction_id,
                detail=reason,
            )
            updated = work.transactions.get(transaction_id)

        if updated is None:  # pragma: no cover - defensive | 防御性兜底
            raise DataError(f"transaction #{transaction_id} disappeared while voiding")
        return updated

    def restore(self, transaction_id: int, *, reason: str = "") -> Transaction:
        """Reinstate a voided entry - the undo of :meth:`void`.

        Only possible while the entry's period is still open: once the books are
        closed, figures that were already reported must not change.  The audit
        trail keeps both the original void reason and this restore.

        恢复一笔已作废的凭证——即 :meth:`void` 的撤销操作。

        只有当该凭证所属的会计期间仍未关闭时才可行：账簿一旦结账，已经报告过的
        数字就不允许改变。审计轨迹会同时保留最初的作废原因和这次的恢复操作。
        """

        with self._transaction() as (work, _owned):
            original = work.transactions.get(transaction_id)
            if original is None:
                raise NotFoundError(f"transaction #{transaction_id} does not exist")
            if not original.is_void:
                raise ConflictError(f"transaction #{transaction_id} is not void")

            self._assert_unlocked(work, original.date)

            previous_reason = original.void_reason
            work.transactions.mark_posted(transaction_id)
            work.audit.record(
                action="transaction.restore",
                entity="transaction",
                entity_id=transaction_id,
                detail=(
                    f"restored (was void: {previous_reason!r})"
                    + (f"; {reason.strip()}" if str(reason or "").strip() else "")
                ),
            )
            updated = work.transactions.get(transaction_id)

        if updated is None:  # pragma: no cover - defensive | 防御性兜底
            raise DataError(f"transaction #{transaction_id} disappeared while restoring")
        return updated

    # -------------------------------------------------------------------- lock
    # -------------------------------------------------------------------- 锁账 (lock)
    def lock_date(self) -> date | None:
        """Last closed day, or ``None`` when the journal is fully open.

        最后已关闭的日期；日记账完全开放时返回 ``None``。"""

        with self._uow_factory() as uow:
            return self._read_lock_date(uow)

    def set_lock_date(self, day: date) -> date:
        """Close every period up to and including ``day``.

        关闭截至 ``day``（含当日）的所有会计期间。"""

        if day > self._clock.today():
            raise ValidationError(
                f"cannot close the books up to {day.isoformat()}: the date is in the future",
                field="date",
            )
        with self._transaction() as (work, _owned):
            current = self._read_lock_date(work)
            if current is not None and day < current:
                raise ConflictError(
                    f"the journal is already closed through {current.isoformat()}; "
                    "closing an earlier date would reopen closed periods"
                )
            work.meta.set(LOCK_DATE_KEY, day.isoformat())
            work.audit.record(
                action="ledger.close",
                entity="ledger",
                entity_id=None,
                detail=f"closed through {day.isoformat()}",
            )
        return day

    def unlock(self, *, force: bool = False) -> date | None:
        """Reopen the journal.  Destructive, therefore explicit.

        重新打开日记账。该操作具有破坏性，因此必须显式请求。"""

        if not force:
            raise ConflictError(
                "reopening a closed journal requires force=True because it can change "
                "already reported figures"
            )
        with self._transaction() as (work, _owned):
            current = self._read_lock_date(work)
            if current is None:
                return None
            work.meta.delete(LOCK_DATE_KEY)
            work.audit.record(
                action="ledger.unlock",
                entity="ledger",
                entity_id=None,
                detail=f"reopened, previously closed through {current.isoformat()}",
            )
        return current

    # ------------------------------------------------------------ ledger facts
    # ------------------------------------------------------------ 账簿事实
    def ledger_currency(self) -> str | None:
        """The currency recorded when the ledger was first initialised.

        账簿首次初始化时记录的币种。"""

        with self._uow_factory() as uow:
            return uow.meta.get(CURRENCY_KEY)

    def record_ledger_currency(self, currency: str) -> str:
        """Stamp the currency on first initialisation; it can never change.

        Idempotent: calling it again with the same currency is a no-op.  Calling
        it with a different currency is refused, because every stored amount is
        expressed in the original currency's minor units.

        首次初始化时把币种写入账簿；此后它永远不会改变。

        幂等：以相同币种再次调用不会产生任何效果；以不同币种调用会被拒绝，因为
        每一笔已存储的金额都是以原币种的最小货币单位表示的。
        """

        normalized = normalize_currency(currency)
        with self._transaction() as (work, _owned):
            current = work.meta.get(CURRENCY_KEY)
            if current is None:
                work.meta.set(CURRENCY_KEY, normalized)
                work.audit.record(
                    action="ledger.initialise",
                    entity="ledger",
                    entity_id=None,
                    detail=f"currency {normalized}",
                )
            elif current != normalized:
                raise ConflictError(
                    f"this ledger was created in {current} and its currency cannot "
                    f"be changed to {normalized}"
                )
        return normalized

    # ------------------------------------------------------------------- query
    # ------------------------------------------------------------------- 查询
    def get(self, transaction_id: int) -> Transaction:
        with self._uow_factory() as uow:
            transaction = uow.transactions.get(transaction_id)
        if transaction is None:
            raise NotFoundError(f"transaction #{transaction_id} does not exist")
        return transaction

    def find(self, transaction_id: int) -> Transaction | None:
        with self._uow_factory() as uow:
            return uow.transactions.get(transaction_id)

    def search(
        self,
        *,
        period: DatePeriod | None = None,
        account_code: str | None = None,
        tag: str | None = None,
        text: str | None = None,
        status: TransactionStatus | str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Transaction]:
        query = TransactionQuery(
            period=period,
            account_code=normalize_code(account_code) if account_code else None,
            tag=tag.strip().lower().lstrip("#") if tag else None,
            text=text,
            status=TransactionStatus.parse(status) if status else None,
            limit=limit,
            offset=offset,
        )
        with self._uow_factory() as uow:
            return uow.transactions.search(query)

    def recent(self, limit: int = 10) -> list[Transaction]:
        return self.search(limit=limit)

    def stats(self) -> dict[str, Any]:
        with self._uow_factory() as uow:
            first, last = uow.transactions.date_bounds()
            lock = self._read_lock_date(uow)
            return {
                "accounts": uow.accounts.count(),
                "transactions": uow.transactions.count(include_void=True),
                "posted_transactions": uow.transactions.count(include_void=False),
                "first_transaction": first.isoformat() if first else None,
                "last_transaction": last.isoformat() if last else None,
                "closed_through": lock.isoformat() if lock else None,
                "ledger_currency": uow.meta.get(CURRENCY_KEY),
                "audit_entries": uow.audit.count(),
            }

    def audit_trail(self, limit: int = 20) -> list[Any]:
        with self._uow_factory() as uow:
            return uow.audit.recent(limit=limit)

    # ----------------------------------------------------------------- helpers
    # ----------------------------------------------------------------- 辅助方法
    def _assert_date_allowed(
        self, uow: UnitOfWork, day: date, *, allow_future: bool
    ) -> None:
        if not allow_future and day > self._clock.today():
            raise ValidationError(
                f"transaction date {day.isoformat()} is in the future; "
                "back-date the entry or pass --allow-future",
                field="date",
            )
        self._assert_unlocked(uow, day)

    def _assert_unlocked(self, uow: UnitOfWork, day: date) -> None:
        closed = self._read_lock_date(uow)
        if closed is not None and day <= closed:
            raise LedgerLockedError(
                f"{day.isoformat()} belongs to a closed period "
                f"(the journal is closed through {closed.isoformat()})"
            )

    @staticmethod
    def _read_lock_date(uow: UnitOfWork) -> date | None:
        raw = uow.meta.get(LOCK_DATE_KEY)
        if raw is None:
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:  # pragma: no cover - only if the file was edited | 仅在文件被手工改动后
            raise DataError(f"stored lock date {raw!r} is not a valid date") from exc

    @staticmethod
    def _require_postable(uow: UnitOfWork, codes: Sequence[str]) -> None:
        for code in codes:
            account = uow.accounts.get(code)
            if account is None:
                raise NotFoundError(
                    f"account {code} does not exist; create it with 'centsible account add'"
                )
            if account.archived:
                raise ConflictError(f"account {code} is archived and cannot receive postings")
            if uow.accounts.has_children(code):
                raise ValidationError(
                    f"account {code} is a heading account; post to one of its children",
                    field="account",
                )
