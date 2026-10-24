"""Financial report commands.

财务报表相关命令。"""

from __future__ import annotations

import argparse

from centsible.interface.cli import formatting, table
from centsible.interface.cli.commands.common import (
    add_period_arguments,
    print_title,
    render_section,
    resolve_period_arg,
)
from centsible.interface.cli.context import CliContext
from centsible.interface.cli.parsing import parse_date_arg

__all__ = ["register"]


def register(subparsers: argparse._SubParsersAction) -> None:
    report = subparsers.add_parser("report", help="financial reports")
    report_sub = report.add_subparsers(dest="report_command", required=True, metavar="REPORT")

    trial = report_sub.add_parser(
        "trial-balance", help="debits and credits per account for a period"
    )
    add_period_arguments(trial)
    trial.set_defaults(handler=cmd_trial_balance)

    income = report_sub.add_parser("income", help="income statement for a period")
    add_period_arguments(income)
    income.set_defaults(handler=cmd_income)

    sheet = report_sub.add_parser(
        "balance-sheet", help="assets, liabilities and equity as of a date"
    )
    sheet.add_argument("--as-of", dest="as_of", metavar="DATE", help="default today")
    sheet.set_defaults(handler=cmd_balance_sheet)

    ledger = report_sub.add_parser("ledger", help="account register with a running balance")
    ledger.add_argument("account", metavar="ACCOUNT")
    add_period_arguments(ledger)
    ledger.set_defaults(handler=cmd_ledger)

    summary = report_sub.add_parser(
        "summary", help="month by month income, expenses and net result"
    )
    summary.add_argument("--months", type=int, default=6, help="how many months (default: %(default)s)")
    summary.add_argument("--until", metavar="DATE", help="last day of the window (default today)")
    summary.set_defaults(handler=cmd_summary)


# ------------------------------------------------------------- trial balance
# ------------------------------------------------------------- 试算平衡表
def cmd_trial_balance(ctx: CliContext, args: argparse.Namespace) -> int:
    period = resolve_period_arg(ctx, args)
    report = ctx.services.reports.trial_balance(period)

    if ctx.json_output:
        ctx.emit_json(report.to_dict())
        return 0

    print_title(
        ctx,
        "Trial balance",
        subtitle=(
            f"{formatting.period_text(report.period)}  -  amounts in {report.currency}"
        ),
    )
    if not report.rows:
        ctx.print("  No postings in this period.")
        return 0

    rows = [
        (
            row.code,
            row.name,
            row.type.value,
            formatting.money(row.debit),
            formatting.money(row.credit),
            formatting.money(row.balance),
        )
        for row in report.rows
    ]
    ctx.print(
        table.render_table(
            ("code", "account", "type", "debit", "credit", "balance"),
            rows,
            aligns=("left", "left", "left", "right", "right", "right"),
            footer=(
                "",
                "",
                "total",
                formatting.money(report.total_debit),
                formatting.money(report.total_credit),
                "",
            ),
        )
    )
    ctx.print("")
    if report.balanced:
        ctx.print("  Balanced: total debits equal total credits.")
    else:
        ctx.print(
            "  OUT OF BALANCE by "
            f"{formatting.money_with_currency(report.difference)} - this is a bug, "
            "run 'centsible db check'"
        )
    return 0


# ------------------------------------------------------------ income statement
# ------------------------------------------------------------ 利润表
def cmd_income(ctx: CliContext, args: argparse.Namespace) -> int:
    period = resolve_period_arg(ctx, args, default=ctx.services.reports.current_month())
    report = ctx.services.reports.income_statement(period)

    if ctx.json_output:
        ctx.emit_json(report.to_dict())
        return 0

    print_title(
        ctx,
        "Income statement",
        subtitle=(
            f"{formatting.period_text(report.period)}  -  amounts in {report.currency}"
        ),
    )
    render_section(
        ctx,
        "Income",
        [(item.code, item.name, item.amount) for item in report.revenue.items],
        report.revenue.total,
    )
    render_section(
        ctx,
        "Expenses",
        [(item.code, item.name, item.amount) for item in report.expenses.items],
        report.expenses.total,
    )
    ctx.print(
        f"  {'Net income':<34}{formatting.money_with_currency(report.net_income):>16}"
    )
    return 0


# -------------------------------------------------------------- balance sheet
# -------------------------------------------------------------- 资产负债表
def cmd_balance_sheet(ctx: CliContext, args: argparse.Namespace) -> int:
    as_of = (
        parse_date_arg(args.as_of, date_format=ctx.config.date_format)
        if args.as_of
        else None
    )
    report = ctx.services.reports.balance_sheet(as_of)

    if ctx.json_output:
        ctx.emit_json(report.to_dict())
        return 0

    print_title(
        ctx,
        "Balance sheet",
        subtitle=f"as of {report.as_of.isoformat()}  -  amounts in {report.currency}",
    )
    render_section(
        ctx,
        "Assets",
        [(item.code, item.name, item.amount) for item in report.assets.items],
        report.assets.total,
    )
    render_section(
        ctx,
        "Liabilities",
        [(item.code, item.name, item.amount) for item in report.liabilities.items],
        report.liabilities.total,
    )
    render_section(
        ctx,
        "Equity",
        [(item.code, item.name, item.amount) for item in report.equity.items],
        report.equity.total,
    )

    ctx.print(
        f"  {'Assets':<34}{formatting.money_with_currency(report.assets.total):>16}"
    )
    ctx.print(
        f"  {'Liabilities + equity':<34}"
        f"{formatting.money_with_currency(report.total_liabilities_and_equity):>16}"
    )
    ctx.print("")
    if report.balanced:
        ctx.print("  Balanced: assets equal liabilities plus equity.")
    else:
        ctx.print(
            "  OUT OF BALANCE by "
            f"{formatting.money_with_currency(report.difference)} - run "
            "'centsible db check'"
        )
    return 0


# --------------------------------------------------------------- account ledger
# --------------------------------------------------------------- 科目明细账
def cmd_ledger(ctx: CliContext, args: argparse.Namespace) -> int:
    period = resolve_period_arg(ctx, args)
    ledger = ctx.services.reports.account_ledger(args.account, period)

    if ctx.json_output:
        ctx.emit_json(ledger.to_dict())
        return 0

    print_title(
        ctx,
        ledger.account.label,
        subtitle=(
            f"{formatting.period_text(ledger.period)}  -  "
            f"amounts in {ctx.settings.currency}"
        ),
    )
    ctx.print(
        f"  Opening balance {formatting.money_with_currency(ledger.opening_balance):>16}",
        "",
    )
    if ledger.entries:
        rows = [
            (
                entry.entry_date.isoformat(),
                entry.transaction_id,
                entry.description,
                entry.memo,
                formatting.money(entry.debit),
                formatting.money(entry.credit),
                formatting.money(entry.balance),
            )
            for entry in ledger.entries
        ]
        ctx.print(
            table.render_table(
                ("date", "id", "description", "memo", "debit", "credit", "balance"),
                rows,
                aligns=("left", "right", "left", "left", "right", "right", "right"),
                footer=(
                    "",
                    "",
                    "",
                    "total",
                    formatting.money(ledger.total_debit),
                    formatting.money(ledger.total_credit),
                    formatting.money(ledger.closing_balance),
                ),
            )
        )
    else:
        ctx.print("  No postings in this period.")
    ctx.print("")
    ctx.print(
        f"  Closing balance {formatting.money_with_currency(ledger.closing_balance):>16}"
    )
    return 0


# --------------------------------------------------------------- month summary
# --------------------------------------------------------------- 月度汇总
def cmd_summary(ctx: CliContext, args: argparse.Namespace) -> int:
    until = (
        parse_date_arg(args.until, date_format=ctx.config.date_format)
        if args.until
        else None
    )
    report = ctx.services.reports.activity(args.months, until=until)

    if ctx.json_output:
        ctx.emit_json(report.to_dict())
        return 0

    print_title(
        ctx,
        "Activity summary",
        subtitle=(
            f"{args.months} month(s)  -  amounts in {ctx.settings.currency}"
        ),
    )
    rows = [
        (
            month.month,
            formatting.money(month.income),
            formatting.money(month.expenses),
            formatting.money(month.net),
        )
        for month in report.months
    ]
    ctx.print(
        table.render_table(
            ("month", "income", "expenses", "net"),
            rows,
            aligns=("left", "right", "right", "right"),
            footer=(
                "total",
                formatting.money(report.total_income),
                formatting.money(report.total_expenses),
                formatting.money(report.total_net),
            ),
        )
    )
    return 0
