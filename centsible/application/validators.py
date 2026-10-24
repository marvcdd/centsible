"""Small validation helpers shared by several services.

They are plain functions over a unit of work so they can be reused without
inheritance and tested against a fake repository if needed.

若干服务共用的小型校验辅助函数。

它们都是围绕工作单元的普通函数，因此无需继承即可复用，必要时也能针对一个
伪造的仓储进行测试。
"""

from __future__ import annotations

from collections.abc import Iterable

from centsible.application.ports import UnitOfWork
from centsible.domain.accounts import Account, normalize_code
from centsible.errors import ConflictError, NotFoundError, ValidationError

__all__ = ["require_account", "require_postable_accounts"]


def require_account(uow: UnitOfWork, code: str, *, role: str = "") -> Account:
    """Fetch an account or raise a :class:`NotFoundError`.

    取出一个科目，取不到则抛出 :class:`NotFoundError`。"""

    account = uow.accounts.get(code)
    if account is None:
        raise NotFoundError(f"{role}account {normalize_code(code)} does not exist")
    return account


def require_postable_accounts(uow: UnitOfWork, codes: Iterable[str]) -> list[Account]:
    """Ensure every account exists, is active and is a postable leaf.

    确保每个科目都存在、处于启用状态，并且是可记账的叶子科目。"""

    resolved: list[Account] = []
    for code in dict.fromkeys(normalize_code(item) for item in codes):
        account = require_account(uow, code)
        if account.archived:
            raise ConflictError(f"account {account.code} is archived and cannot receive postings")
        if uow.accounts.has_children(account.code):
            raise ValidationError(
                f"account {account.code} is a heading account; post to one of its children",
                field="account",
            )
        resolved.append(account)
    return resolved
