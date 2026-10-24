"""Budget commands.

预算命令。"""

from __future__ import annotations

import argparse

from centsible.interface.cli import formatting, table
from centsible.interface.cli.commands.common import (
    add_period_arguments,
    print_title,
    resolve_period_arg,
)
from centsible.interface.cli.context import CliContext

__all__ = ["register"]


def register(subparsers: argparse._SubParsersAction) -> None:
    budget = subparsers.add_parser("budget", help="monthly plans and budget versus actual")
    budget_sub = budget.add_subparsers(dest="budget_command", required=True, metavar="ACTION")

    set_ = budget_sub.add_parser("set", help="create or replace a plan for one account and month")
    set_.add_argument("account", metavar="ACCOUNT")
    set_.add_argument("--month", required=True, metavar="YYYY-MM")
    set_.add_argument("--amount", required=True, help="planned amount (positive)")
    set_.add_argument("--note", default="", help="optional note")
    set_.set_defaults(handler=cmd_set)

    clear = budget_sub.add_parser("clear", help="remove a plan")
    clear.add_argument("account", metavar="ACCOUNT")
    clear.add_argument("--month", required=True, metavar="YYYY-MM")
    clear.set_defaults(handler=cmd_clear)

    listing = budget_sub.add_parser("list", help="list plans")
    listing.add_argument("--from-month", dest="from_month", metavar="YYYY-MM")
    listing.add_argument("--to-month", dest="to_month", metavar="YYYY-MM")
    listing.set_defaults(handler=cmd_list)

    report = budget_sub.add_parser("report", help="budget versus actual for a period")
    add_period_arguments(report)
    report.set_defaults(handler=cmd_report)

    copy = budget_sub.add_parser("copy", help="copy every plan of one month onto another")
    copy.add_argument("--from-month", dest="from_month", required=True, metavar="YYYY-MM")
    copy.add_argument("--to-month", dest="to_month", required=True, metavar="YYYY-MM")
    copy.add_argument("--overwrite", action="store_true", help="replace existing plans")
    copy.set_defaults(handler=cmd_copy)


def cmd_set(ctx: CliContext, args: argparse.Namespace) -> int:
    budget = ctx.services.budgets.set(
        account_code=args.account,
        month=args.month,
        amount=args.amount,
        note=args.note,
    )
    if ctx.json_output:
        ctx.emit_json({"budget": budget.to_dict()})
        return 0
    ctx.print(
        f"Budget for {budget.account_code} in {budget.month}: "
        f"{formatting.money_with_currency(ctx.money(budget.amount))}"
    )
    return 0


def cmd_clear(ctx: CliContext, args: argparse.Namespace) -> int:
    removed = ctx.services.budgets.clear(account_code=args.account, month=args.month)
    if ctx.json_output:
        ctx.emit_json({"removed": removed})
        return 0
    if removed:
        ctx.print(f"Removed the budget for {args.account.upper()} in {args.month}.")
    else:
        ctx.print(f"No budget was set for {args.account.upper()} in {args.month}.")
    return 0


def cmd_list(ctx: CliContext, args: argparse.Namespace) -> int:
    services = ctx.services
    budgets = services.budgets.list(
        start_month=args.from_month, end_month=args.to_month
    )
    names = {account.code: account.name for account in services.accounts.list()}

    if ctx.json_output:
        ctx.emit_json(
            {
                "count": len(budgets),
                "budgets": [
                    {**budget.to_dict(), "name": names.get(budget.account_code, "")}
                    for budget in budgets
                ],
            }
        )
        return 0

    if not budgets:
        ctx.print("No budgets yet. Set one with 'centsible budget set ACCOUNT --month ...'.")
        return 0

    print_title(ctx, "Budgets", subtitle=f"{len(budgets)} plan(s)")
    rows = [
        (
            budget.month,
            budget.account_code,
            names.get(budget.account_code, ""),
            formatting.money(ctx.money(budget.amount)),
            budget.note,
        )
        for budget in budgets
    ]
    ctx.print(
        table.render_table(
            ("month", "account", "name", "planned", "note"),
            rows,
            aligns=("left", "left", "left", "right", "left"),
        )
    )
    return 0


def cmd_report(ctx: CliContext, args: argparse.Namespace) -> int:
    period = resolve_period_arg(ctx, args, default=ctx.services.reports.current_month())
    report = ctx.services.budgets.report(period)

    if ctx.json_output:
        ctx.emit_json(report.to_dict())
        return 0

    print_title(
        ctx,
        "Budget versus actual",
        subtitle=(
            f"{formatting.period_text(report.period)}  -  amounts in {report.currency}"
        ),
    )
    if not report.rows:
        ctx.print("  No budgets or activity in this period.")
        return 0

    rows = [
        (
            row.account.code,
            row.account.name,
            formatting.money(row.budgeted),
            formatting.money(row.actual),
            formatting.money(row.variance),
            "ok" if row.favorable else "watch",
        )
        for row in report.rows
    ]
    ctx.print(
        table.render_table(
            ("code", "account", "planned", "actual", "variance", "status"),
            rows,
            aligns=("left", "left", "right", "right", "right", "left"),
            footer=(
                "",
                "total",
                formatting.money(report.total_budgeted),
                formatting.money(report.total_actual),
                formatting.money(report.total_variance),
                "",
            ),
        )
    )
    ctx.print(
        "",
        "  variance = planned - actual; for expenses, positive means under budget.",
    )
    return 0


def cmd_copy(ctx: CliContext, args: argparse.Namespace) -> int:
    copied = ctx.services.budgets.copy_month(
        source_month=args.from_month,
        target_month=args.to_month,
        overwrite=args.overwrite,
    )
    if ctx.json_output:
        ctx.emit_json({"copied": copied, "from": args.from_month, "to": args.to_month})
        return 0
    if copied:
        ctx.print(f"Copied {copied} plan(s) from {args.from_month} to {args.to_month}.")
    else:
        ctx.print(
            f"Nothing copied: {args.to_month} already has plans for every account "
            "in the source month (use --overwrite)."
        )
    return 0
