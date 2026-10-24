"""The chart of accounts and the five accounting classes.

An account type determines *which side increases the account*:

====== ============== ==================================================
type   normal balance what it means
====== ============== ==================================================
asset  debit          what the entity owns (cash, inventory, receivable)
liabil credit         what the entity owes (payable, loan)
equity credit         owners' residual interest
income credit         value earned in a period
expense debit         value consumed in a period
====== ============== ==================================================

Codes are canonicalised to upper case so that ``cash`` and ``CASH`` cannot
become two different accounts.  Parent accounts act as headings: they aggregate
their children in reports but they are not postable, which is enforced by
:class:`~centsible.application.account_service.AccountService`.

科目表与五大会计类别。

科目类型决定*哪一侧会增加该科目*：

====== ============== ==================================================
类型   正常余额       含义
====== ============== ==================================================
资产   借方           主体拥有的资源（现金、存货、应收款项）
负债   贷方           主体欠下的债务（应付款项、借款）
权益   贷方           所有者剩余权益
收入   贷方           会计期间内赚取的价值
费用   借方           会计期间内消耗的价值
====== ============== ==================================================

科目代码统一规范为大写，因此 ``cash`` 与 ``CASH`` 不会成为两个不同的科目。
父科目充当分组标题：它们在报表中汇总下级科目，但本身不可记账，
这一点由 :class:`~centsible.application.account_service.AccountService` 强制执行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable

from centsible.errors import ValidationError

__all__ = ["Account", "AccountType", "MAX_CODE_LENGTH", "normalize_code", "sort_accounts"]

MAX_CODE_LENGTH = 32
MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 500

_CODE_RE = re.compile(r"^[0-9A-Z][0-9A-Z._-]*$")


class AccountType(str, Enum):
    """The five classes of the accounting equation.

    会计等式的五个类别。"""

    ASSET = "asset"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    EXPENSE = "expense"

    @property
    def normal_balance(self) -> str:
        """``"debit"`` or ``"credit"`` - the side that increases the account.

        ``"debit"``（借方）还是 ``"credit"``（贷方）——使该科目增加的一侧。"""

        return "debit" if self.normal_sign > 0 else "credit"

    @property
    def normal_sign(self) -> int:
        """``+1`` for debit-normal accounts, ``-1`` for credit-normal ones.

        借方科目取 ``+1``，贷方科目取 ``-1``。"""

        return 1 if self in (AccountType.ASSET, AccountType.EXPENSE) else -1

    @property
    def is_balance_sheet(self) -> bool:
        return self in (AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY)

    @property
    def is_income_statement(self) -> bool:
        return self in (AccountType.INCOME, AccountType.EXPENSE)

    @property
    def section_title(self) -> str:
        return {
            AccountType.ASSET: "Assets",
            AccountType.LIABILITY: "Liabilities",
            AccountType.EQUITY: "Equity",
            AccountType.INCOME: "Income",
            AccountType.EXPENSE: "Expenses",
        }[self]

    def natural_balance(self, net_debit: Decimal) -> Decimal:
        """Convert a signed ``debit - credit`` amount into the account's own sign.

        Assets and expenses keep the raw value; liabilities, equity and income
        flip it, so a credit balance reads as a positive number.

        把带符号的 ``debit - credit`` 金额换算成该科目自身符号下的金额。

        资产与费用保留原值；负债、权益与收入则将它取反，
        这样贷方余额读出来就是正数。
        """

        return net_debit if self.normal_sign > 0 else -net_debit

    @classmethod
    def parse(cls, value: "AccountType | str") -> "AccountType":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValidationError(
                f"account type must be one of {', '.join(item.value for item in cls)}",
                field="type",
            )
        text = value.strip().lower()
        aliases = {
            "asset": cls.ASSET,
            "assets": cls.ASSET,
            "liability": cls.LIABILITY,
            "liabilities": cls.LIABILITY,
            "equity": cls.EQUITY,
            "income": cls.INCOME,
            "revenue": cls.INCOME,
            "expense": cls.EXPENSE,
            "expenses": cls.EXPENSE,
        }
        try:
            return aliases[text]
        except KeyError:
            allowed = ", ".join(item.value for item in cls)
            raise ValidationError(
                f"{value!r} is not an account type; choose one of {allowed}", field="type"
            ) from None

    def __str__(self) -> str:  # pragma: no cover - display helper | 仅用于显示的辅助方法
        return self.value


@dataclass(frozen=True, slots=True)
class Account:
    """One line of the chart of accounts.

    ``code`` is the natural key: it is stable, human readable and appears in
    every report.  ``parent_code`` builds the two level (or deeper) hierarchy
    used for grouped reporting.

    科目表中的一行。

    ``code`` 是自然键：它稳定、便于人阅读，并且出现在每一张报表中。
    ``parent_code`` 用来构建两级（或更深）的层级，供分组报表使用。
    """

    code: str
    name: str
    type: AccountType
    parent_code: str | None = None
    description: str = ""
    archived: bool = False
    id: int | None = None

    def __post_init__(self) -> None:
        code = normalize_code(self.code)
        object.__setattr__(self, "code", code)

        name = str(self.name).strip()
        if not name:
            raise ValidationError("account name is required", field="name")
        if len(name) > MAX_NAME_LENGTH:
            raise ValidationError(
                f"account name must be at most {MAX_NAME_LENGTH} characters", field="name"
            )
        object.__setattr__(self, "name", name)

        object.__setattr__(self, "type", AccountType.parse(self.type))

        parent = self.parent_code
        if parent is not None:
            parent = normalize_code(parent)
            if parent == code:
                raise ValidationError("an account cannot be its own parent", field="parent")
        object.__setattr__(self, "parent_code", parent)

        description = str(self.description or "").strip()
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValidationError(
                f"description must be at most {MAX_DESCRIPTION_LENGTH} characters",
                field="description",
            )
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "archived", bool(self.archived))

    # ---------------------------------------------------------------- helpers
    # ---------------------------------------------------------------- 辅助方法
    @property
    def label(self) -> str:
        return f"{self.code} {self.name}"

    @property
    def normal_sign(self) -> int:
        return self.type.normal_sign

    def natural_balance(self, net_debit: Decimal) -> Decimal:
        return self.type.natural_balance(net_debit)

    @property
    def is_balance_sheet(self) -> bool:
        return self.type.is_balance_sheet

    def with_changes(self, **changes: Any) -> "Account":
        """Return an updated copy; validation runs again.

        返回更新后的副本；校验会重新执行。"""

        return replace(self, **changes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "type": self.type.value,
            "parent_code": self.parent_code,
            "description": self.description,
            "archived": self.archived,
        }

    def __str__(self) -> str:  # pragma: no cover - display helper | 仅用于显示的辅助方法
        return f"{self.label} ({self.type.value})"


def normalize_code(code: str) -> str:
    """Validate an account code and canonicalise its casing.

    校验科目代码，并把它规范为大写。"""

    if not isinstance(code, str):
        raise ValidationError("account code must be text", field="code")
    text = code.strip().upper()
    if not text:
        raise ValidationError("account code is required", field="code")
    if len(text) > MAX_CODE_LENGTH:
        raise ValidationError(
            f"account code must be at most {MAX_CODE_LENGTH} characters", field="code"
        )
    if not _CODE_RE.match(text):
        raise ValidationError(
            "account code must start with a letter or digit and may contain "
            "letters, digits, '.', '_' and '-'",
            field="code",
        )
    return text


def sort_accounts(accounts: Iterable[Account]) -> list[Account]:
    """Stable ordering used by every report and listing.

    每份报表与每个列表都采用的稳定排序。"""

    return sorted(accounts, key=lambda account: account.code)
