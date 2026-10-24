"""Recurring rules: schedule templates and materialise them on demand.

Running the schedule is deliberately *idempotent per rule*: after a run the
rule's ``next_date`` has moved past every occurrence that was created, so a
second run creates nothing.  A rule that fails (closed period, archived account,
...) still advances, and the failure is reported - otherwise a single broken
rule would block the queue forever.

周期规则：排期模板，并按需生成凭证。

执行排期时有意保证*每条规则自身幂等*：一次运行之后，规则的 ``next_date`` 已经
推进到所有已创建凭证之后，因此第二次运行不会再创建任何凭证。执行失败的规则
（会计期间已锁账、科目已归档……）同样会向前推进，并记录失败原因——否则只要有一条
规则坏掉，整个队列就会被永久堵住。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Sequence

from centsible.application.base import ServiceBase
from centsible.application.dto import RecurrenceIssue, RecurrenceRunResult
from centsible.application.ledger_service import LedgerService
from centsible.application.validators import require_postable_accounts
from centsible.domain.recurrence import Frequency, RecurringRule
from centsible.domain.transactions import Posting, normalize_tags
from centsible.errors import CentsibleError, ConflictError, NotFoundError

__all__ = ["RecurringService"]


class RecurringService(ServiceBase):
    """Manage standing entries and run their schedules.

    管理周期分录并执行各自的排期。"""

    def __init__(self, uow_factory, clock, settings=None, *, ledger: LedgerService | None = None) -> None:
        super().__init__(uow_factory, clock, settings)
        self._ledger = ledger or LedgerService(uow_factory, clock, settings)

    # ------------------------------------------------------------------- write
    # -------------------------------------------------------------------- 写入
    def create(
        self,
        *,
        name: str,
        description: str,
        frequency: Frequency | str,
        postings: Sequence[Posting],
        start_date: date,
        interval: int = 1,
        end_date: date | None = None,
        payee: str = "",
        tags: Sequence[str] | str | None = None,
    ) -> RecurringRule:
        rule = RecurringRule(
            name=name,
            description=description,
            frequency=Frequency.parse(frequency),
            postings=tuple(postings),
            start_date=start_date,
            next_date=start_date,
            interval=interval,
            end_date=end_date,
            payee=payee,
            tags=normalize_tags(tags),
        )

        with self._transaction() as (uow, _owned):
            if uow.recurring.find_by_name(rule.name) is not None:
                raise ConflictError(f"a recurring rule named {rule.name!r} already exists")
            require_postable_accounts(uow, [posting.account_code for posting in rule.postings])
            stored = uow.recurring.add(rule)
            uow.audit.record(
                action="recurring.create",
                entity="recurring_rule",
                entity_id=stored.id,
                detail=f"{stored.name} ({stored.schedule_label}, from {stored.start_date.isoformat()})",
            )
        return stored

    def pause(self, rule_id: int) -> RecurringRule:
        return self._set_active(rule_id, False)

    def resume(self, rule_id: int) -> RecurringRule:
        return self._set_active(rule_id, True)

    def delete(self, rule_id: int) -> None:
        with self._transaction() as (uow, _owned):
            rule = uow.recurring.get(rule_id)
            if rule is None:
                raise NotFoundError(f"recurring rule #{rule_id} does not exist")
            uow.recurring.delete(rule_id)
            uow.audit.record(
                action="recurring.delete",
                entity="recurring_rule",
                entity_id=rule_id,
                detail=rule.name,
            )

    def _set_active(self, rule_id: int, active: bool) -> RecurringRule:
        with self._transaction() as (uow, _owned):
            rule = uow.recurring.get(rule_id)
            if rule is None:
                raise NotFoundError(f"recurring rule #{rule_id} does not exist")
            if rule.active == active:
                return rule
            updated = rule.resumed() if active else rule.paused()
            stored = uow.recurring.update(updated)
            uow.audit.record(
                action="recurring.resume" if active else "recurring.pause",
                entity="recurring_rule",
                entity_id=rule_id,
                detail=rule.name,
            )
        return stored

    # ------------------------------------------------------------------- query
    # -------------------------------------------------------------------- 查询
    def list(self) -> list[RecurringRule]:
        with self._uow_factory() as uow:
            return uow.recurring.list()

    def get(self, rule_id: int) -> RecurringRule:
        with self._uow_factory() as uow:
            rule = uow.recurring.get(rule_id)
        if rule is None:
            raise NotFoundError(f"recurring rule #{rule_id} does not exist")
        return rule

    # --------------------------------------------------------------------- run
    # -------------------------------------------------- 运行（run / schedule）
    def run(self, *, until: date | None = None, dry_run: bool = False) -> RecurrenceRunResult:
        """Create every due occurrence up to ``until`` (default: today).

        创建截至 ``until``（默认今天）的所有到期凭证。"""

        horizon = until or self._clock.today()
        created: list[Any] = []
        issues: list[RecurrenceIssue] = []
        processed = 0

        with self._transaction() as (uow, _owned):
            for rule in uow.recurring.list():
                if not rule.active:
                    continue
                due = rule.due_dates(horizon)
                if not due:
                    if rule.end_date is not None and rule.next_date > rule.end_date:
                        uow.recurring.update(rule.paused())
                    continue

                processed += 1
                for day in due:
                    template = rule.transaction_for(day)
                    if dry_run:
                        created.append(template)
                        continue
                    try:
                        stored = self._ledger.record(
                            date=template.date,
                            description=template.description,
                            postings=template.postings,
                            payee=template.payee,
                            tags=template.tags,
                            # A standing entry is sometimes deliberately posted
                            # ahead of time (rent due on the 1st).  The closed
                            # period guard still applies.
                            # 周期分录有时会被有意提前入账（比如 1 号到期的房租），
                            # 锁账期间的校验依然生效。
                            allow_future=True,
                            uow=uow,
                        )
                    except CentsibleError as exc:
                        issues.append(
                            RecurrenceIssue(
                                rule_id=rule.id,
                                rule_name=rule.name,
                                occurrence=day,
                                message=str(exc),
                            )
                        )
                        continue
                    created.append(stored)

                if not dry_run:
                    advanced = rule.advance(len(due))
                    if advanced.end_date is not None and advanced.next_date > advanced.end_date:
                        advanced = advanced.paused()
                    uow.recurring.update(advanced)
                    uow.audit.record(
                        action="recurring.run",
                        entity="recurring_rule",
                        entity_id=rule.id,
                        detail=f"{len(due)} occurrence(s) up to {horizon.isoformat()}",
                    )

        return RecurrenceRunResult(
            rules_processed=processed,
            transactions_created=tuple(created),
            issues=tuple(issues),
            dry_run=dry_run,
        )

    # ----------------------------------------------------------------- helpers
    # -------------------------------------------------------------------- 辅助
    def upcoming(self, *, days: int = 30) -> Sequence[tuple[RecurringRule, date]]:
        """Next due date per active rule inside the coming ``days`` days.

        每条启用规则在未来 ``days`` 天内的下一个到期日。"""

        horizon = self._clock.today() + timedelta(days=days)
        upcoming: list[tuple[RecurringRule, date]] = []
        for rule in self.list():
            if not rule.active or rule.next_date > horizon:
                continue
            if rule.end_date is not None and rule.next_date > rule.end_date:
                continue
            upcoming.append((rule, rule.next_date))
        return sorted(upcoming, key=lambda item: (item[1], item[0].name))
