"""Recurring (standing) entry commands.

周期（常设）分录命令。"""

from __future__ import annotations

import argparse

from centsible.interface.cli import formatting, table
from centsible.interface.cli.commands.common import print_title
from centsible.interface.cli.context import CliContext
from centsible.interface.cli.parsing import (
    parse_date_arg,
    parse_posting_specs,
    parse_tags_arg,
)

__all__ = ["register"]

_ADD_EPILOG = """\
the template must balance, exactly like a real transaction.

example:
  centsible recurring add "Monthly rent" --desc "Office rent" --frequency monthly \\
      --start 2025-04-01 --debit 5100=1200 --credit 1010=1200
"""


def register(subparsers: argparse._SubParsersAction) -> None:
    recurring = subparsers.add_parser("recurring", help="standing entries and their schedules")
    recurring_sub = recurring.add_subparsers(
        dest="recurring_command", required=True, metavar="ACTION"
    )

    add = recurring_sub.add_parser(
        "add",
        help="create a scheduled template",
        epilog=_ADD_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add.add_argument("name")
    add.add_argument("--desc", required=True, help="description used for each occurrence")
    add.add_argument(
        "--frequency",
        required=True,
        metavar="FREQ",
        help="daily, weekly, monthly, quarterly or yearly",
    )
    add.add_argument("--start", required=True, metavar="DATE", help="first occurrence")
    add.add_argument("--interval", type=int, default=1, help="every N periods (default: 1)")
    add.add_argument("--end", metavar="DATE", help="last date an occurrence may fall on")
    add.add_argument("--payee")
    add.add_argument("--tag", action="append", metavar="TAG")
    add.add_argument("--posting", action="append", metavar="ACCOUNT=AMOUNT[:MEMO]")
    add.add_argument("--debit", action="append", metavar="ACCOUNT=AMOUNT[:MEMO]")
    add.add_argument("--credit", action="append", metavar="ACCOUNT=AMOUNT[:MEMO]")
    add.set_defaults(handler=cmd_add)

    listing = recurring_sub.add_parser("list", help="list rules and their next due date")
    listing.set_defaults(handler=cmd_list)

    run = recurring_sub.add_parser(
        "run", help="materialise every due occurrence", description="Create the journal entries that are due."
    )
    run.add_argument("--until", metavar="DATE", help="materialise up to this date (default today)")
    run.add_argument("--dry-run", action="store_true", help="show what would be created")
    run.set_defaults(handler=cmd_run)

    pause = recurring_sub.add_parser("pause", help="stop a rule from running")
    pause.add_argument("id", type=int)
    pause.set_defaults(handler=cmd_pause)

    resume = recurring_sub.add_parser("resume", help="re-activate a paused rule")
    resume.add_argument("id", type=int)
    resume.set_defaults(handler=cmd_resume)

    delete = recurring_sub.add_parser("delete", help="delete a rule (created entries stay)")
    delete.add_argument("id", type=int)
    delete.set_defaults(handler=cmd_delete)

    upcoming = recurring_sub.add_parser("upcoming", help="what is due soon")
    upcoming.add_argument("--days", type=int, default=30, help="look-ahead window (default: %(default)s)")
    upcoming.set_defaults(handler=cmd_upcoming)


def cmd_add(ctx: CliContext, args: argparse.Namespace) -> int:
    postings = parse_posting_specs(
        postings=args.posting,
        debits=args.debit,
        credits=args.credit,
        exponent=ctx.settings.exponent,
    )
    rule = ctx.services.recurring.create(
        name=args.name,
        description=args.desc,
        frequency=args.frequency,
        postings=postings,
        start_date=parse_date_arg(args.start, date_format=ctx.config.date_format),
        interval=args.interval,
        end_date=(
            parse_date_arg(args.end, date_format=ctx.config.date_format) if args.end else None
        ),
        payee=args.payee or "",
        tags=parse_tags_arg(args.tag),
    )

    if ctx.json_output:
        ctx.emit_json({"rule": rule.to_dict()})
        return 0

    ctx.print(
        f"Created recurring rule #{rule.id} {rule.name!r}",
        f"  schedule: {rule.schedule_label}, first on {rule.start_date.isoformat()}",
        f"  amount:   {formatting.money_with_currency(ctx.money(rule.template_total))}",
        "",
        "Materialise it with 'centsible recurring run'.",
    )
    return 0


def cmd_list(ctx: CliContext, _args: argparse.Namespace) -> int:
    rules = ctx.services.recurring.list()

    if ctx.json_output:
        ctx.emit_json({"count": len(rules), "rules": [rule.to_dict() for rule in rules]})
        return 0

    if not rules:
        ctx.print("No recurring rules yet. Create one with 'centsible recurring add'.")
        return 0

    print_title(ctx, "Recurring rules", subtitle=f"{len(rules)} rule(s)")
    rows = [
        (
            rule.id,
            rule.name,
            rule.schedule_label,
            rule.next_date.isoformat(),
            rule.end_date.isoformat() if rule.end_date else "-",
            formatting.money(ctx.money(rule.template_total)),
            "active" if rule.active else "paused",
        )
        for rule in rules
    ]
    ctx.print(
        table.render_table(
            ("id", "name", "schedule", "next", "ends", "amount", "state"),
            rows,
            aligns=("right", "left", "left", "left", "left", "right", "left"),
        )
    )
    return 0


def cmd_run(ctx: CliContext, args: argparse.Namespace) -> int:
    until = (
        parse_date_arg(args.until, date_format=ctx.config.date_format)
        if args.until
        else None
    )
    result = ctx.services.recurring.run(until=until, dry_run=args.dry_run)

    if ctx.json_output:
        ctx.emit_json(result.to_dict())
        return 0 if not result.issues else 2

    verb = "Would create" if result.dry_run else "Created"
    ctx.print(
        f"{verb} {result.created_count} transaction(s) from "
        f"{result.rules_processed} rule(s)."
    )
    if result.transactions_created:
        rows = [
            (transaction.date.isoformat(), transaction.description, formatting.money(ctx.money(transaction.total_debits)))
            for transaction in result.transactions_created
        ]
        ctx.print("")
        ctx.print(
            table.render_table(
                ("date", "description", "amount"),
                rows,
                aligns=("left", "left", "right"),
            )
        )
    if result.issues:
        ctx.print("", f"{len(result.issues)} occurrence(s) could not be created:")
        for issue in result.issues:
            when = issue.occurrence.isoformat() if issue.occurrence else "-"
            ctx.print(f"  {when}  {issue.rule_name}: {issue.message}")
        return 2
    return 0


def cmd_pause(ctx: CliContext, args: argparse.Namespace) -> int:
    rule = ctx.services.recurring.pause(args.id)
    if ctx.json_output:
        ctx.emit_json({"rule": rule.to_dict()})
        return 0
    ctx.print(f"Paused rule #{rule.id} {rule.name!r}.")
    return 0


def cmd_resume(ctx: CliContext, args: argparse.Namespace) -> int:
    rule = ctx.services.recurring.resume(args.id)
    if ctx.json_output:
        ctx.emit_json({"rule": rule.to_dict()})
        return 0
    ctx.print(f"Resumed rule #{rule.id} {rule.name!r}; next occurrence {rule.next_date.isoformat()}.")
    return 0


def cmd_delete(ctx: CliContext, args: argparse.Namespace) -> int:
    ctx.services.recurring.delete(args.id)
    if ctx.json_output:
        ctx.emit_json({"deleted": args.id})
        return 0
    ctx.print(f"Deleted rule #{args.id}. Transactions it already created were kept.")
    return 0


def cmd_upcoming(ctx: CliContext, args: argparse.Namespace) -> int:
    upcoming = ctx.services.recurring.upcoming(days=args.days)
    if ctx.json_output:
        ctx.emit_json(
            {
                "days": args.days,
                "upcoming": [
                    {"id": rule.id, "name": rule.name, "next_date": day.isoformat()}
                    for rule, day in upcoming
                ],
            }
        )
        return 0

    if not upcoming:
        ctx.print(f"Nothing is due in the next {args.days} days.")
        return 0

    print_title(ctx, "Upcoming", subtitle=f"next {args.days} days")
    rows = [
        (
            day.isoformat(),
            rule.id,
            rule.name,
            rule.schedule_label,
            formatting.money(ctx.money(rule.template_total)),
        )
        for rule, day in upcoming
    ]
    ctx.print(
        table.render_table(
            ("date", "id", "name", "schedule", "amount"),
            rows,
            aligns=("left", "right", "left", "left", "right"),
        )
    )
    return 0

