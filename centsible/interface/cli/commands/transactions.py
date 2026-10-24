"""Journal entry commands: record, list, inspect and void.

日记账凭证相关命令：录入、列表、查看与作废。"""

from __future__ import annotations

import argparse

from centsible.domain.transactions import Transaction
from centsible.interface.cli import formatting, table
from centsible.interface.cli.commands.common import (
    add_period_arguments,
    print_title,
    render_transaction,
    resolve_period_arg,
)
from centsible.interface.cli.context import CliContext
from centsible.interface.cli.parsing import (
    parse_date_arg,
    parse_posting_specs,
    parse_tags_arg,
)

__all__ = ["register"]

_ADD_EPILOG = """\
amounts are signed: positive is a debit, negative is a credit.

examples:
  # pay rent from the bank account (two legs, both explicit)
  centsible tx add --date 2025-03-02 --desc "Office rent" \\
      --posting 5100=1200 --posting 1010=-1200

  # same entry using the direction flags
  centsible tx add --date 2025-03-02 --desc "Office rent" \\
      --debit 5100=1200 --credit 1010=1200

  # let the last leg balance the entry
  centsible tx add --date 2025-03-08 --desc "POS sales" \\
      --debit 1010=4520 --credit 4000=4000 --credit 2200=520

  # a 3-legged entry with memos
  centsible tx add --date 2025-03-08 --desc "POS sales" \\
      --debit 1010=4520:card terminal --credit 4000=4000:sales --credit 2200=520:tax
"""


def register(subparsers: argparse._SubParsersAction) -> None:
    tx = subparsers.add_parser("tx", help="record, inspect and void journal entries")
    tx_sub = tx.add_subparsers(dest="tx_command", required=True, metavar="ACTION")

    add = tx_sub.add_parser(
        "add",
        help="record a balanced transaction",
        description="Record one balanced journal entry.",
        epilog=_ADD_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add.add_argument("--date", required=True, metavar="DATE", help="entry date (YYYY-MM-DD)")
    add.add_argument("--desc", required=True, help="what happened")
    add.add_argument(
        "--posting",
        action="append",
        metavar="ACCOUNT=AMOUNT[:MEMO]",
        help="a signed leg (positive = debit, negative = credit); repeatable",
    )
    add.add_argument(
        "--debit", action="append", metavar="ACCOUNT=AMOUNT[:MEMO]", help="a debit leg"
    )
    add.add_argument(
        "--credit", action="append", metavar="ACCOUNT=AMOUNT[:MEMO]", help="a credit leg"
    )
    add.add_argument("--payee", help="counterparty")
    add.add_argument("--ref", metavar="REFERENCE", help="unique external reference")
    add.add_argument("--tag", action="append", metavar="TAG", help="tag; repeatable")
    add.add_argument(
        "--allow-future", action="store_true", help="accept a date after today"
    )
    add.add_argument(
        "--dry-run", action="store_true", help="validate and print without writing"
    )
    add.set_defaults(handler=cmd_add)

    listing = tx_sub.add_parser("list", help="list transactions")
    add_period_arguments(listing)
    listing.add_argument("--account", metavar="CODE", help="only entries touching this account")
    listing.add_argument("--tag", help="only entries carrying this tag")
    listing.add_argument("--text", help="substring search in description, payee or reference")
    listing.add_argument(
        "--status",
        choices=("posted", "void"),
        help="only entries with this status (default: both, voided ones included)",
    )
    listing.add_argument("--limit", type=int, default=30, help="max rows (default: %(default)s)")
    listing.add_argument("--all", action="store_true", help="no row limit")
    listing.set_defaults(handler=cmd_list)

    show = tx_sub.add_parser("show", help="show one transaction with its postings")
    show.add_argument("id", type=int)
    show.set_defaults(handler=cmd_show)

    void = tx_sub.add_parser(
        "void",
        help="cancel a transaction (kept for audit, excluded from balances)",
        description=(
            "The entry is not deleted: it keeps status VOID and the reason, so the "
            "correction stays auditable, but it no longer affects any balance. "
            "Voiding is refused inside a closed period; 'tx restore' undoes it."
        ),
    )
    void.add_argument("id", type=int)
    void.add_argument("--reason", required=True, help="why the entry is being voided")
    void.set_defaults(handler=cmd_void)

    restore = tx_sub.add_parser(
        "restore",
        help="reinstate a voided transaction",
        description=(
            "Undo a void while the entry's period is still open. Both the void and "
            "the restore remain in the audit trail."
        ),
    )
    restore.add_argument("id", type=int)
    restore.add_argument("--reason", help="optional note recorded in the audit trail")
    restore.set_defaults(handler=cmd_restore)


# ----------------------------------------------------------------------- add
# ----------------------------------------------------------------------- 新增 (add)
def cmd_add(ctx: CliContext, args: argparse.Namespace) -> int:
    postings = parse_posting_specs(
        postings=args.posting,
        debits=args.debit,
        credits=args.credit,
        exponent=ctx.settings.exponent,
    )
    day = parse_date_arg(args.date, date_format=ctx.config.date_format)
    tags = parse_tags_arg(args.tag)
    services = ctx.services

    if args.dry_run:
        transaction = Transaction(
            date=day,
            description=args.desc,
            postings=tuple(postings),
            payee=args.payee or "",
            reference=args.ref,
            tags=tags,
        )
        for code in transaction.accounts:
            services.accounts.require(code)
        if ctx.json_output:
            ctx.emit_json({"dry_run": True, "transaction": transaction.to_dict()})
            return 0
        ctx.print("Dry run - nothing was written.", "")
        render_transaction(ctx, transaction)
        return 0

    transaction = services.ledger.record(
        date=day,
        description=args.desc,
        postings=postings,
        payee=args.payee or "",
        reference=args.ref,
        tags=tags,
        allow_future=args.allow_future,
    )

    if ctx.json_output:
        ctx.emit_json({"transaction": transaction.to_dict()})
        return 0

    render_transaction(ctx, transaction)
    return 0


# ---------------------------------------------------------------------- list
# ---------------------------------------------------------------------- 列表 (list)
def cmd_list(ctx: CliContext, args: argparse.Namespace) -> int:
    period = resolve_period_arg(ctx, args)
    limit = None if args.all else args.limit
    transactions = ctx.services.ledger.search(
        period=period,
        account_code=args.account,
        tag=args.tag,
        text=args.text,
        status=args.status,
        limit=limit,
    )

    if ctx.json_output:
        ctx.emit_json(
            {
                "count": len(transactions),
                "period": period.to_dict() if period else None,
                "status": args.status,
                "transactions": [transaction.to_dict() for transaction in transactions],
            }
        )
        return 0

    if not transactions:
        ctx.print("No transactions match those filters.")
        return 0

    print_title(
        ctx,
        "Transactions",
        subtitle=(
            f"{len(transactions)} row(s), newest first"
            f"{'' if args.all else ' - use --all for everything'}"
        ),
    )
    rows = [
        (
            transaction.id,
            transaction.date.isoformat(),
            transaction.description,
            transaction.payee,
            transaction.tag_text,
            formatting.money(ctx.money(transaction.total_debits)),
            formatting.status_text(transaction.status),
        )
        for transaction in transactions
    ]
    ctx.print(
        table.render_table(
            ("id", "date", "description", "payee", "tags", "amount", "status"),
            rows,
            aligns=("right", "left", "left", "left", "left", "right", "left"),
        )
    )
    return 0


# ---------------------------------------------------------------------- show
# ---------------------------------------------------------------------- 查看 (show)
def cmd_show(ctx: CliContext, args: argparse.Namespace) -> int:
    transaction = ctx.services.ledger.get(args.id)
    if ctx.json_output:
        ctx.emit_json({"transaction": transaction.to_dict()})
        return 0
    render_transaction(ctx, transaction, title=f"Transaction #{transaction.id}")
    return 0


# ---------------------------------------------------------------------- void
# ---------------------------------------------------------------------- 作废 (void)
def cmd_void(ctx: CliContext, args: argparse.Namespace) -> int:
    transaction = ctx.services.ledger.void(args.id, reason=args.reason)
    if ctx.json_output:
        ctx.emit_json({"transaction": transaction.to_dict()})
        return 0
    ctx.print(
        f"Transaction #{transaction.id} is now VOID and no longer affects any balance.",
        "It stays in the journal for audit purposes.",
        "",
    )
    render_transaction(ctx, transaction, title=f"Voided #{transaction.id}")
    return 0


# ------------------------------------------------------------------- restore
# ------------------------------------------------------------------- 恢复
def cmd_restore(ctx: CliContext, args: argparse.Namespace) -> int:
    transaction = ctx.services.ledger.restore(args.id, reason=args.reason or "")
    if ctx.json_output:
        ctx.emit_json({"transaction": transaction.to_dict()})
        return 0
    ctx.print(
        f"Transaction #{transaction.id} is posted again and counts in every balance.",
        "",
    )
    render_transaction(ctx, transaction, title=f"Restored #{transaction.id}")
    return 0
