"""Helpers shared by the command modules.

各命令模块共用的辅助函数。"""

from __future__ import annotations

import argparse

from centsible.domain.money import Money
from centsible.domain.periods import DatePeriod
from centsible.interface.cli import formatting
from centsible.interface.cli.context import CliContext
from centsible.interface.cli.parsing import resolve_period
from centsible.interface.cli.table import heading, render_table

__all__ = [
    "add_period_arguments",
    "print_title",
    "render_section",
    "render_transaction",
    "resolve_period_arg",
]


def add_period_arguments(
    parser: argparse.ArgumentParser,
    *,
    month_help: str = "restrict the report to one month (YYYY-MM)",
) -> None:
    parser.add_argument("--month", metavar="YYYY-MM", help=month_help)
    parser.add_argument(
        "--from", dest="from_date", metavar="DATE", help="first day of the range (inclusive)"
    )
    parser.add_argument(
        "--to", dest="to_date", metavar="DATE", help="last day of the range (inclusive)"
    )


def resolve_period_arg(
    ctx: CliContext, args: argparse.Namespace, *, default: DatePeriod | None = None
) -> DatePeriod | None:
    return resolve_period(
        args, default=default, date_format=ctx.config.date_format
    )


def print_title(ctx: CliContext, title: str, *, subtitle: str | None = None) -> None:
    ctx.print(heading(title))
    if subtitle:
        ctx.print(f"  {subtitle}")
    ctx.print("")


def render_section(
    ctx: CliContext,
    title: str,
    items: list[tuple[str, str, Money]],
    total: Money,
    *,
    indent: str = "    ",
) -> None:
    """Render one section of a financial statement.

    渲染财务报表中的一个分节。"""

    ctx.print(f"  {title}")
    if not items:
        ctx.print(f"{indent}(no activity)")
        ctx.print("")
        return
    rows = [(code, name, formatting.money(amount)) for code, name, amount in items]
    ctx.print(
        render_table(
            ("code", "account", "amount"),
            rows,
            aligns=("left", "left", "right"),
            footer=("", f"Total {title.lower()}", formatting.money(total)),
            indent=indent,
        )
    )
    ctx.print("")


def render_transaction(ctx: CliContext, transaction, *, title: str | None = None) -> None:
    """Render a transaction header plus its postings.

    渲染凭证表头及其分录。"""

    heading_text = title or f"Transaction #{transaction.id or '(new)'}"
    ctx.print(f"{heading_text}   {transaction.date.isoformat()}   {transaction.description}")

    pairs: list[tuple[str, object]] = []
    if transaction.payee:
        pairs.append(("payee", transaction.payee))
    if transaction.reference:
        pairs.append(("reference", transaction.reference))
    if transaction.tags:
        pairs.append(("tags", transaction.tag_text))
    if transaction.status.value != "posted":
        pairs.append(("status", transaction.status.value.upper()))
        if transaction.void_reason:
            pairs.append(("void reason", transaction.void_reason))

    if pairs:
        ctx.print(render_table(("field", "value"), pairs, indent="  "))
    ctx.print("")

    currency = ctx.settings.currency
    rows = [
        (
            posting.account_code,
            formatting.money(Money(posting.debit_amount, currency)),
            formatting.money(Money(posting.credit_amount, currency)),
            posting.memo,
        )
        for posting in transaction.postings
    ]
    ctx.print(
        render_table(
            ("account", "debit", "credit", "memo"),
            rows,
            aligns=("left", "right", "right", "left"),
            footer=(
                "total",
                formatting.money(Money(transaction.total_debits, currency)),
                formatting.money(Money(transaction.total_credits, currency)),
                "",
            ),
        )
    )
