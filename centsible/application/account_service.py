"""Chart of accounts use cases.

The rules encoded here are the ones that keep reports meaningful:

* an account code is unique and immutable;
* a child account must have the same type as its parent;
* parent accounts are headings and cannot receive postings, so a hierarchy can
  never double count in a report;
* an account with a non-zero balance cannot be archived - transfer the money
  first;
* an account that was ever posted to can be archived but never deleted.

科目表的用例。

这里固化的规则，正是让报表保持意义的关键：

* 科目代码唯一且不可变更；
* 子科目的类型必须与父科目一致；
* 父科目只是分类标题，不能挂分录，因此层级结构在报表中永远不会重复计算；
* 余额不为零的科目不能停用——请先将余额转出；
* 曾经被记过分录的科目可以停用，但永远不能删除。
"""

from __future__ import annotations

from typing import Any, Sequence

from centsible.application.base import ServiceBase
from centsible.application.chart import chart_for
from centsible.domain.accounts import Account, AccountType, normalize_code, sort_accounts
from centsible.errors import ConflictError, DataError, NotFoundError, ValidationError

__all__ = ["AccountService"]

_UNSET: Any = object()


class AccountService(ServiceBase):
    """Create, update, retire and inspect accounts.

    创建、修改、停用与查看科目。"""

    # ------------------------------------------------------------------ create
    # ------------------------------------------------------------------ 创建
    def create(
        self,
        *,
        code: str,
        name: str,
        type: AccountType | str,
        parent_code: str | None = None,
        description: str = "",
    ) -> Account:
        account = Account(
            code=code,
            name=name,
            type=AccountType.parse(type),
            parent_code=parent_code,
            description=description,
        )
        with self._transaction() as (uow, _owned):
            if uow.accounts.get(account.code) is not None:
                raise ConflictError(
                    f"account {account.code} already exists", code=account.code
                )
            if account.parent_code is not None:
                parent = self._require(uow, account.parent_code, role="parent ")
                self._assert_parent_compatible(parent, account)
            stored = uow.accounts.add(account)
            uow.audit.record(
                action="account.create",
                entity="account",
                entity_id=stored.code,
                detail=f"{stored.name} ({stored.type.value})",
            )
        return stored

    # ------------------------------------------------------------------ update
    # ------------------------------------------------------------------ 更新
    def update(
        self,
        code: str,
        *,
        name: str | None = None,
        description: str | None = None,
        parent_code: Any = _UNSET,
    ) -> Account:
        """Rename, re-describe or re-parent an account.

        Only presentation fields may change: the code is the stable key used by
        every posting and report, and the type decides the sign convention, so
        both are immutable by design.

        修改科目的名称、说明或上级科目。

        只有展示性字段允许修改：代码是每笔分录和每张报表都在引用的稳定键，而类型决定了
        借贷符号约定，所以这两者从设计上就是不可变的。
        """

        with self._transaction() as (uow, _owned):
            account = self._require(uow, code)
            changes: dict[str, Any] = {}

            if name is not None:
                changes["name"] = name
            if description is not None:
                changes["description"] = description
            if parent_code is not _UNSET:
                new_parent = None if parent_code in (None, "") else normalize_code(parent_code)
                if new_parent == account.code:
                    raise ValidationError(
                        "an account cannot be its own parent", field="parent"
                    )
                if new_parent is not None:
                    parent = self._require(uow, new_parent, role="parent ")
                    self._assert_parent_compatible(parent, account)
                    self._assert_no_cycle(uow, account.code, new_parent)
                changes["parent_code"] = new_parent

            updated = account.with_changes(**changes)
            if updated == account:
                return account

            stored = uow.accounts.update(updated)
            uow.audit.record(
                action="account.update",
                entity="account",
                entity_id=stored.code,
                detail=", ".join(sorted(changes)) or "no-op",
            )
        return stored

    # ------------------------------------------------------------------ retire
    # ------------------------------------------------------------------ 停用
    def archive(self, code: str, *, archived: bool = True) -> Account:
        with self._transaction() as (uow, _owned):
            account = self._require(uow, code)
            if account.archived == archived:
                return account

            if archived:
                active_children = [
                    child
                    for child in uow.accounts.children_of(account.code)
                    if not child.archived
                ]
                if active_children:
                    listed = ", ".join(sorted(child.code for child in active_children))
                    raise ConflictError(
                        f"account {account.code} still has active child account(s): {listed}"
                    )
                totals = uow.transactions.totals(end=None).get(account.code)
                if totals is not None and totals.net_debit != 0:
                    balance = self._settings.money(account.natural_balance(totals.net_debit))
                    raise ConflictError(
                        f"account {account.code} still carries a balance of {balance}; "
                        "transfer or settle it before archiving"
                    )

            stored = uow.accounts.update(account.with_changes(archived=archived))
            uow.audit.record(
                action="account.archive" if archived else "account.restore",
                entity="account",
                entity_id=stored.code,
            )
        return stored

    def delete(self, code: str) -> None:
        """Remove an account that was never used.

        Accounts with history are archived instead: deleting them would orphan
        postings and silently change past reports.

        删除一个从未被使用过的科目。

        有历史记录的科目请改用停用：删除它们会让分录失去挂靠对象，并悄悄改变以往报表
        的结果。
        """

        with self._transaction() as (uow, _owned):
            account = self._require(uow, code)
            children = uow.accounts.children_of(account.code)
            if children:
                raise ConflictError(
                    f"account {account.code} still has {len(children)} child account(s)"
                )
            postings = uow.accounts.posting_count(account.code)
            if postings:
                raise ConflictError(
                    f"account {account.code} is referenced by {postings} posting(s); "
                    "archive it instead of deleting it"
                )
            uow.accounts.delete(account.code)
            uow.audit.record(
                action="account.delete", entity="account", entity_id=account.code
            )

    # ------------------------------------------------------------------- query
    # ------------------------------------------------------------------- 查询
    def get(self, code: str) -> Account | None:
        with self._uow_factory() as uow:
            return uow.accounts.get(code)

    def require(self, code: str) -> Account:
        with self._uow_factory() as uow:
            return self._require(uow, code)

    def list(self, *, include_archived: bool = True) -> list[Account]:
        with self._uow_factory() as uow:
            accounts = uow.accounts.list(include_archived=include_archived)
        return sort_accounts(accounts)

    def tree(self, *, include_archived: bool = True) -> Sequence[tuple[int, Account]]:
        """Accounts as ``(depth, account)`` pairs in display order.

        按展示顺序返回 ``(depth, account)`` 形式的科目序列。"""

        accounts = self.list(include_archived=include_archived)
        known = {account.code for account in accounts}
        by_parent: dict[str | None, list[Account]] = {}
        for account in accounts:
            parent = account.parent_code if account.parent_code in known else None
            by_parent.setdefault(parent, []).append(account)

        ordered: list[tuple[int, Account]] = []
        visiting: set[str] = set()

        def walk(parent: str | None, depth: int) -> None:
            for account in sorted(by_parent.get(parent, []), key=lambda item: item.code):
                if account.code in visiting:  # pragma: no cover - defensive | 防御性兜底
                    raise DataError(f"cycle detected in the chart of accounts at {account.code}")
                visiting.add(account.code)
                ordered.append((depth, account))
                walk(account.code, depth + 1)
                visiting.discard(account.code)

        walk(None, 0)
        return ordered

    def count(self) -> int:
        with self._uow_factory() as uow:
            return uow.accounts.count()

    def ensure_chart(self, style: str = "default") -> Sequence[Account]:
        """Insert any missing account of the requested chart template.

        按指定的科目表模板补齐其中缺失的科目。"""

        chart = chart_for(style)
        created: list[Account] = []
        with self._transaction() as (uow, _owned):
            existing = {account.code for account in uow.accounts.list()}
            for account in chart:
                if account.code in existing:
                    continue
                created.append(uow.accounts.add(account))
            if created:
                uow.audit.record(
                    action="account.import_chart",
                    entity="account",
                    entity_id=None,
                    detail=f"{len(created)} account(s) from the {style} chart",
                )
        return created

    # ----------------------------------------------------------------- helpers
    # ----------------------------------------------------------------- 辅助方法
    @staticmethod
    def _require(uow: Any, code: str, *, role: str = "") -> Account:
        account = uow.accounts.get(code)
        if account is None:
            raise NotFoundError(f"{role}account {normalize_code(code)} does not exist")
        return account

    @staticmethod
    def _assert_parent_compatible(parent: Account, account: Account) -> None:
        if parent.archived:
            raise ConflictError(f"parent account {parent.code} is archived")
        if parent.type is not account.type:
            raise ValidationError(
                f"a {account.type.value} account cannot be a child of the "
                f"{parent.type.value} account {parent.code}",
                field="parent",
            )

    @staticmethod
    def _assert_no_cycle(uow: Any, code: str, parent_code: str) -> None:
        seen: set[str] = set()
        cursor: str | None = parent_code
        while cursor is not None:
            if cursor == code:
                raise ValidationError(
                    f"moving {code} under {parent_code} would create a cycle", field="parent"
                )
            if cursor in seen:  # pragma: no cover - defensive | 防御性兜底
                raise DataError(f"cycle detected in the chart of accounts at {cursor}")
            seen.add(cursor)
            ancestor = uow.accounts.get(cursor)
            if ancestor is None:
                raise NotFoundError(f"parent account {cursor} does not exist")
            cursor = ancestor.parent_code
