"""Parsing of raw command line strings into domain values.

The CLI syntax for a journal leg is ``ACCOUNT=AMOUNT[:MEMO]``.  The amount is
*signed*, positive being a debit - the same convention the domain uses, so what
you type is what the journal stores.  ``--debit`` and ``--credit`` are offered as
a convenience: they force the direction and let you type unsigned amounts.

Exactly one leg may omit its amount; it is then filled in so the entry balances.
That turns "I know the total and the split except for one leg" into a single
command instead of mental arithmetic.

把命令行原始字符串解析为领域值。

日记账中一笔分录的 CLI 语法是 ``ACCOUNT=AMOUNT[:MEMO]``。金额是*带符号*的，
正数表示借方——这正是领域层使用的约定，因此你输入什么，日记账就存什么。
``--debit`` 与 ``--credit`` 只是为了方便：它们强制指定方向，并允许你输入
无符号金额。

允许且仅允许一笔分录省略金额；它随后会被补齐，使凭证保持平衡。这样就把
“我知道总额以及除某一笔之外的拆分”变成一条命令，而不必心算。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Sequence

from centsible.domain.money import parse_amount
from centsible.domain.periods import DatePeriod, month_bounds, parse_date, parse_period
from centsible.domain.transactions import Posting, normalize_tags
from centsible.errors import ValidationError

__all__ = [
    "parse_date_arg",
    "parse_posting_specs",
    "parse_tags_arg",
    "resolve_period",
]


def parse_date_arg(text: str | None, *, date_format: str | None = None) -> date:
    if not text:
        raise ValidationError("a date is required (YYYY-MM-DD)", field="date")
    return parse_date(text, date_format=date_format)


def parse_tags_arg(values: Sequence[str] | None) -> tuple[str, ...]:
    return normalize_tags(values)


def resolve_period(
    args: object,
    *,
    default: DatePeriod | None = None,
    date_format: str | None = None,
) -> DatePeriod | None:
    """Combine ``--month`` or ``--from``/``--to`` into one period.

    把 ``--month`` 或 ``--from``/``--to`` 组合成一个会计期间。"""

    month = getattr(args, "month", None)
    start = getattr(args, "from_date", None)
    end = getattr(args, "to_date", None)

    if month and (start or end):
        raise ValidationError(
            "use either --month or --from/--to, not both", field="period"
        )
    if month:
        return month_bounds(month)

    period = parse_period(start, end, date_format=date_format)
    return period if period is not None else default


def parse_posting_specs(
    *,
    postings: Sequence[str] | None = None,
    debits: Sequence[str] | None = None,
    credits: Sequence[str] | None = None,
    exponent: int,
) -> list[Posting]:
    """Build balanced postings from ``ACCOUNT=AMOUNT[:MEMO]`` specifications.

    根据 ``ACCOUNT=AMOUNT[:MEMO]`` 形式的写法构造平衡的分录。"""

    parsed: list[tuple[str, Decimal | None, str]] = []
    for spec in postings or ():
        parsed.append(_parse_spec(spec, exponent=exponent, force_sign=0))
    for spec in debits or ():
        parsed.append(_parse_spec(spec, exponent=exponent, force_sign=1))
    for spec in credits or ():
        parsed.append(_parse_spec(spec, exponent=exponent, force_sign=-1))

    if not parsed:
        raise ValidationError(
            "at least two postings are required; use --posting ACCOUNT=AMOUNT "
            "(positive = debit, negative = credit)",
            field="posting",
        )
    if len(parsed) < 2:
        raise ValidationError(
            "a transaction needs at least two postings", field="posting"
        )

    missing = [index for index, (_, amount, _) in enumerate(parsed) if amount is None]
    if len(missing) > 1:
        raise ValidationError(
            "only one posting may omit its amount", field="posting"
        )

    amounts: list[Decimal] = [amount for (_, amount, _) in parsed if amount is not None]  # type: ignore[misc]
    if missing:
        index = missing[0]
        residual = sum(amounts, Decimal(0)) if amounts else Decimal(0)
        implied = -residual
        if implied == 0:
            raise ValidationError(
                "the implied balancing amount is zero; give at least one amount explicitly",
                field="posting",
            )
        parsed[index] = (parsed[index][0], implied, parsed[index][2])
        amounts.append(implied)

    if sum(amounts, Decimal(0)) != 0:
        raise ValidationError(
            "the postings do not balance; add one leg without an amount to auto-balance",
            field="posting",
        )

    return [
        Posting(code, amount, memo)  # type: ignore[arg-type]
        for code, amount, memo in parsed
    ]


def _parse_spec(
    spec: str, *, exponent: int, force_sign: int = 0
) -> tuple[str, Decimal | None, str]:
    text = str(spec).strip()
    if not text:
        raise ValidationError("a posting must not be empty", field="posting")

    account, separator, rest = text.partition("=")
    if not separator:
        raise ValidationError(
            f"{text!r} must look like ACCOUNT=AMOUNT[:MEMO]", field="posting"
        )

    account = account.strip()
    if not account:
        raise ValidationError(f"{text!r} has no account code", field="posting")

    amount_text, _, memo = rest.partition(":")
    amount_text = amount_text.strip()
    if not amount_text:
        return account, None, memo.strip()

    amount = parse_amount(amount_text, exponent=exponent, field="posting")
    if force_sign > 0:
        amount = abs(amount)
    elif force_sign < 0:
        amount = -abs(amount)
    return account, amount, memo.strip()
