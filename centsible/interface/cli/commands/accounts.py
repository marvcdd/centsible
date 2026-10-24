"""Chart of accounts commands.

科目表相关命令。"""

from __future__ import annotations

import argparse
from typing import Any

from centsible.errors import ValidationError
from centsible.interface.cli import formatting, table
from centsible.interface.cli.commands.common import print_title
from centsible.interface.cli.context import CliContext

__all__ = ["register"]


def register(subparsers: argparse._SubParsersAction) -> None:
    account = subparsers.add_parser("account", help="manage the chart of accounts")
    account_sub = account.add_subparsers(dest="account_command", required=True, metavar="ACTION")

    add = account_sub.add_parser("add", help="create an account")
    add.add_argument("code", help="stable account code, e.g. 1000 or CASH")
    add.add_argument("--name", required=True, help="human readable name")
    add.add_argument(
        "--type",
        required=True,
        metavar="TYPE",
        help="asset, liability, equity, income or expense",
    )
    add.add_argument("--parent", metavar="CODE", help="parent account (a heading, not postable)")
    add.add_argument("--description", default="", help="free text description")
    add.set_defaults(handler=cmd_add)

    listing = account_sub.add_parser("list", help="list accounts")
    listing.add_argument("--tree", action="store_true", help="show the parent/child hierarchy")
    listing.add_argument("--all", action="store_true", help="include archived accounts")
    listing.set_defaults(handler=cmd_list)

    show = account_sub.add_parser("show", help="details, balance and recent postings")
    show.add_argument("code")
    show.set_defaults(handler=cmd_show)

    update = account_sub.add_parser("update", help="rename, re-describe or re-parent an account")
    update.add_argument("code")
    update.add_argument("--name", help="new name")
    update.add_argument("--description", help="new description")
    update.add_argument("--parent", metavar="CODE", help="move under this parent")
    update.add_argument("--no-parent", action="store_true", help="detach from its parent")
    update.set_defaults(handler=cmd_update)

    archive = account_sub.add_parser(
        "archive", help="retire an account (balance must be zero, no active children)"
    )
    archive.add_argument("code")
    archive.set_defaults(handler=cmd_archive)

    restore = account_sub.add_parser("restore", help="make an archived account active again")
    restore.add_argument("code")
    restore.set_defaults(handler=cmd_restore)

    delete = account_sub.add_parser(
        "delete", help="remove an account that was never used"
    )
    delete.add_argument("code")
    delete.set_defaults(handler=cmd_delete)


# ---------------------------------------------------------------------- add
# ---------------------------------------------------------------------- 新增 (add)
def cmd_add(ctx: CliContext, args: argparse.Namespace) -> int:
    account = ctx.services.accounts.create(
        code=args.code,
        name=args.name,
        type=args.type,
        parent_code=args.parent,
        description=args.description,
    )
    if ctx.json_output:
        ctx.emit_json({"account": account.to_dict()})
        return 0
    ctx.print(
        f"Created {account.label} ({account.type.value})",
        f"  normal balance: {account.type.normal_balance}",
    )
    if account.parent_code:
        ctx.print(f"  parent: {account.parent_code}")
    return 0


# --------------------------------------------------------------------- list
# --------------------------------------------------------------------- 列表 (list)
def cmd_list(ctx: CliContext, args: argparse.Namespace) -> int:
    services = ctx.services
    if args.tree:
        entries = services.accounts.tree(include_archived=args.all)
        rows = [
            (
                account.code,
                f"{'  ' * depth}{account.name}",
                account.type.value,
                account.parent_code or "",
                "yes" if account.archived else "",
            )
            for depth, account in entries
        ]
    else:
        accounts = services.accounts.list(include_archived=args.all)
        rows = [
            (
                account.code,
                account.name,
                account.type.value,
                account.parent_code or "",
                "yes" if account.archived else "",
            )
            for account in accounts
        ]

    if ctx.json_output:
        ctx.emit_json(
            {
                "count": len(rows),
                "accounts": [
                    {
                        "code": row[0],
                        "name": row[1].strip(),
                        "type": row[2],
                        "parent": row[3] or None,
                        "archived": row[4] == "yes",
                    }
                    for row in rows
                ],
            }
        )
        return 0

    if not rows:
        ctx.print("No accounts yet. Create one with 'centsible account add'.")
        return 0

    print_title(
        ctx,
        "Chart of accounts",
        subtitle=f"{len(rows)} account(s){'' if args.all else ' - archived accounts hidden (use --all)'}",
    )
    ctx.print(
        table.render_table(
            ("code", "name", "type", "parent", "archived"),
            rows,
            aligns=("left", "left", "left", "left", "left"),
        )
    )
    return 0


# --------------------------------------------------------------------- show
# --------------------------------------------------------------------- 查看 (show)
def cmd_show(ctx: CliContext, args: argparse.Namespace) -> int:
    services = ctx.services
    account = services.accounts.require(args.code)
    balance = services.reports.account_balance(account.code)
    children = services.accounts.list()
    child_codes = sorted(
        child.code for child in children if child.parent_code == account.code
    )
    recent = services.ledger.search(account_code=account.code, limit=5)

    payload = {
        "account": account.to_dict(),
        "balance": balance.to_dict(),
        "children": child_codes,
        "recent_transactions": [transaction.to_dict() for transaction in recent],
    }
    if ctx.json_output:
        ctx.emit_json(payload)
        return 0

    print_title(ctx, account.label, subtitle=f"{account.type.value} account")
    ctx.print(
        table.render_key_values(
            (
                ("code", account.code),
                ("type", account.type.value),
                ("normal balance", account.type.normal_balance),
                ("parent", account.parent_code or "-"),
                ("children", ", ".join(child_codes) if child_codes else "-"),
                ("status", "archived" if account.archived else "active"),
                ("balance", formatting.money_with_currency(balance)),
                ("description", account.description or "-"),
            )
        )
    )

    if recent:
        ctx.print("", "  Recent activity")
        rows = [
            (
                transaction.id,
                transaction.date.isoformat(),
                transaction.description,
                formatting.money(
                    ctx.money(
                        sum(
                            (
                                posting.amount
                                for posting in transaction.postings_for(account.code)
                            ),
                            start=0,
                        )
                    )
                ),
            )
            for transaction in recent
        ]
        ctx.print(
            table.render_table(
                ("id", "date", "description", "amount"),
                rows,
                aligns=("right", "left", "left", "right"),
                indent="  ",
            )
        )
    return 0


# ------------------------------------------------------------------- update
# ------------------------------------------------------------------- 修改
def cmd_update(ctx: CliContext, args: argparse.Namespace) -> int:
    changes: dict[str, Any] = {}
    if args.name is not None:
        changes["name"] = args.name
    if args.description is not None:
        changes["description"] = args.description
    if args.no_parent:
        changes["parent_code"] = None
    elif args.parent is not None:
        changes["parent_code"] = args.parent

    if not changes:
        raise ValidationError(
            "nothing to update; pass --name, --description, --parent or --no-parent",
            field="update",
        )

    account = ctx.services.accounts.update(args.code, **changes)
    if ctx.json_output:
        ctx.emit_json({"account": account.to_dict()})
        return 0
    ctx.print(f"Updated {account.label}")
    return 0


# ------------------------------------------------------------ archive/restore
# ------------------------------------------------------------ 归档/恢复
def cmd_archive(ctx: CliContext, args: argparse.Namespace) -> int:
    account = ctx.services.accounts.archive(args.code, archived=True)
    if ctx.json_output:
        ctx.emit_json({"account": account.to_dict()})
        return 0
    ctx.print(f"Archived {account.label}; it can no longer receive postings.")
    return 0


def cmd_restore(ctx: CliContext, args: argparse.Namespace) -> int:
    account = ctx.services.accounts.archive(args.code, archived=False)
    if ctx.json_output:
        ctx.emit_json({"account": account.to_dict()})
        return 0
    ctx.print(f"Restored {account.label}.")
    return 0


# ------------------------------------------------------------------- delete
# ------------------------------------------------------------------- 删除
def cmd_delete(ctx: CliContext, args: argparse.Namespace) -> int:
    ctx.services.accounts.delete(args.code)
    if ctx.json_output:
        ctx.emit_json({"deleted": args.code.upper()})
        return 0
    ctx.print(f"Deleted account {args.code.upper()}.")
    return 0
