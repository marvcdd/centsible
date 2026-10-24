"""System commands: initialisation, demo data, configuration, database, journal state.

系统类命令：初始化、演示数据、配置、数据库与日记账状态。"""

from __future__ import annotations

import argparse
from pathlib import Path

from centsible.config import CONFIG_FILENAME
from centsible.demo import MAX_DEMO_MONTHS, seed_demo
from centsible.errors import ConflictError
from centsible.interface.cli import formatting, table
from centsible.interface.cli.commands.common import print_title
from centsible.interface.cli.context import CliContext
from centsible.interface.cli.parsing import parse_date_arg

__all__ = ["register"]


def register(subparsers: argparse._SubParsersAction) -> None:
    # ---------------------------------------------------------------- init/demo
    # ---------------------------------------------------------------- 初始化/演示数据
    init = subparsers.add_parser(
        "init",
        help="create the ledger database and install a chart of accounts",
        description=(
            "Create the SQLite ledger and install a chart of accounts. This is "
            "idempotent: re-running it only adds accounts that are missing, and the "
            "currency of an existing ledger is never changed."
        ),
    )
    init.add_argument(
        "--currency",
        metavar="CODE",
        help="ISO currency code for a NEW ledger, e.g. CNY or USD",
    )
    init.add_argument(
        "--chart",
        choices=("default", "empty"),
        default="default",
        help="chart of accounts to install (default: %(default)s)",
    )
    init.add_argument(
        "--write-config",
        action="store_true",
        help=f"also write {CONFIG_FILENAME} with the effective settings",
    )
    init.set_defaults(handler=cmd_init)

    demo = subparsers.add_parser(
        "demo",
        help="fill an empty ledger with realistic demo data",
        description="Generate months of trading activity, budgets and standing entries.",
    )
    demo.add_argument(
        "--months",
        type=int,
        default=6,
        help=f"how many months to generate, 1..{MAX_DEMO_MONTHS} (default: %(default)s)",
    )
    demo.add_argument(
        "--fresh",
        action="store_true",
        help="delete the existing database file before seeding",
    )
    demo.set_defaults(handler=cmd_demo)

    # ------------------------------------------------------------------- config
    # ------------------------------------------------------------------- 配置
    config = subparsers.add_parser("config", help="inspect the effective configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True, metavar="ACTION")
    show = config_sub.add_parser("show", help="print the merged configuration")
    show.set_defaults(handler=cmd_config_show)

    # ----------------------------------------------------------------------- db
    # ----------------------------------------------------------------------- 数据库 (database)
    database = subparsers.add_parser("db", help="database maintenance")
    database_sub = database.add_subparsers(dest="db_command", required=True, metavar="ACTION")

    info = database_sub.add_parser("info", help="show size, schema version and row counts")
    info.set_defaults(handler=cmd_db_info)

    check = database_sub.add_parser(
        "check", help="run SQLite integrity checks plus a trial balance reconciliation"
    )
    check.set_defaults(handler=cmd_db_check)

    backup = database_sub.add_parser("backup", help="write a consistent copy of the database")
    backup.add_argument("--out", required=True, metavar="FILE", help="destination file")
    backup.set_defaults(handler=cmd_db_backup)

    restore = database_sub.add_parser(
        "restore",
        help="replace the ledger with a backup made by 'db backup'",
        description=(
            "The backup is verified (integrity, schema version, Centsible tables) "
            "before it replaces the current ledger file. A ledger that already "
            "holds transactions is never overwritten without --force."
        ),
    )
    restore.add_argument("file", metavar="BACKUP")
    restore.add_argument(
        "--force",
        action="store_true",
        help="overwrite a ledger that already holds transactions",
    )
    restore.set_defaults(handler=cmd_db_restore)

    # ------------------------------------------------------------------ journal
    # ------------------------------------------------------------------ 日记账
    journal = subparsers.add_parser(
        "journal", help="closed periods and the audit trail"
    )
    journal_sub = journal.add_subparsers(dest="journal_command", required=True, metavar="ACTION")

    status = journal_sub.add_parser("status", help="show whether the books are closed")
    status.set_defaults(handler=cmd_journal_status)

    lock = journal_sub.add_parser(
        "lock", help="close every period up to a date (blocks new postings)"
    )
    lock.add_argument("--date", metavar="DATE", help="last open day, default today")
    lock.set_defaults(handler=cmd_journal_lock)

    unlock = journal_sub.add_parser("unlock", help="reopen the books (requires --force)")
    unlock.add_argument("--force", action="store_true", help="confirm the destructive change")
    unlock.set_defaults(handler=cmd_journal_unlock)

    audit = journal_sub.add_parser("audit", help="show the most recent audit entries")
    audit.add_argument("--limit", type=int, default=20, help="how many entries (default: %(default)s)")
    audit.set_defaults(handler=cmd_journal_audit)


# ---------------------------------------------------------------------- init
# ---------------------------------------------------------------------- 初始化 (initialise)
def cmd_init(ctx: CliContext, args: argparse.Namespace) -> int:
    requested_currency = args.currency
    if requested_currency:
        ctx.reconfigure(currency=requested_currency)

    services = ctx.services
    path = ctx.config.resolved_database_path()
    already_initialised = services.accounts.count() > 0

    recorded_currency = services.ledger.ledger_currency()
    if requested_currency:
        # ``--currency`` means "create the ledger in this currency".  It can never
        # change the currency of an existing ledger: the stored amounts are minor
        # units of the original currency.
        # ``--currency`` 表示“以该币种创建账簿”。它永远无法更改既有账簿的币种：
        # 已存储的金额是原始币种的最小货币单位。
        if recorded_currency is not None and recorded_currency != requested_currency.strip().upper():
            raise ConflictError(
                f"this ledger was created in {recorded_currency}; its currency "
                f"cannot be changed to {requested_currency.strip().upper()}. "
                "Start a new ledger file if you need a different currency."
            )
        services.ledger.record_ledger_currency(requested_currency)
    elif recorded_currency is None:
        services.ledger.record_ledger_currency(ctx.settings.currency)

    created = services.accounts.ensure_chart(args.chart)

    config_file: str | None = None
    if args.write_config:
        target = Path(CONFIG_FILENAME)
        if target.exists():
            config_file = f"{target} (already exists, left untouched)"
        else:
            target.write_text(
                ctx.config.with_overrides(source=None).to_toml(), encoding="utf-8"
            )
            config_file = str(target)

    total = services.accounts.count()
    if ctx.json_output:
        ctx.emit_json(
            {
                "database": str(path),
                "currency": services.settings.currency,
                "chart": args.chart,
                "accounts_created": len(created),
                "accounts_total": total,
                "already_initialised": already_initialised,
                "config_file": config_file,
            }
        )
        return 0

    if already_initialised and not created:
        ctx.print(f"Ledger already initialised at {path}", "")
    else:
        ctx.print(f"Ledger ready at {path}", "")
    ctx.print(
        table.render_key_values(
            (
                ("currency", services.settings.currency),
                ("chart", args.chart),
                ("accounts created", len(created)),
                ("accounts in ledger", total),
                ("fiscal year starts", f"month {ctx.config.fiscal_year_start_month}"),
            )
        )
    )
    if config_file:
        ctx.print("", f"Configuration written to {config_file}")
    if already_initialised:
        ctx.print(
            "",
            f"Nothing to do: the ledger already contains every account of the "
            f"{args.chart} chart, and its currency ({services.settings.currency}) is fixed.",
            "Use 'centsible report balance-sheet' to look at it, or "
            "'centsible demo --fresh' to start over.",
        )
        return 0

    ctx.print(
        "",
        "Next steps:",
        "  centsible demo                      fill the ledger with demo data",
        "  centsible account add CASH --name Cash --type asset",
        "  centsible tx add --date 2025-03-01 --desc 'Opening capital' \\",
        "      --debit BANK=20000 --credit EQUITY=20000",
    )
    return 0


# ---------------------------------------------------------------------- demo
# ---------------------------------------------------------------------- 演示数据 (demo data)
def cmd_demo(ctx: CliContext, args: argparse.Namespace) -> int:
    path = ctx.config.resolved_database_path()
    if args.fresh:
        if ctx.services_ready:
            raise ConflictError("the database is already open; --fresh must be the first action")
        for suffix in ("", "-wal", "-shm"):
            Path(f"{path}{suffix}").unlink(missing_ok=True)

    services = ctx.services
    created = seed_demo(services, months=args.months)
    stats = services.ledger.stats()

    payload = {
        "database": str(path),
        "months": args.months,
        **created,
        "accounts_total": services.accounts.count(),
        "posted_transactions": stats["posted_transactions"],
    }
    if ctx.json_output:
        ctx.emit_json(payload)
        return 0

    ctx.print(f"Demo ledger ready at {path}", "")
    ctx.print(
        table.render_key_values(
            (
                ("months generated", args.months),
                ("accounts", services.accounts.count()),
                ("transactions", created["transactions"]),
                ("budgets", created["budgets"]),
                ("recurring rules", created["recurring_rules"]),
            )
        )
    )
    ctx.print(
        "",
        "Try:",
        "  centsible report summary",
        "  centsible report balance-sheet",
        "  centsible report income --month " + formatting.month_text(services.clock.today()),
        "  centsible tx list --limit 10",
    )
    return 0


# -------------------------------------------------------------------- config
# -------------------------------------------------------------------- 配置
def cmd_config_show(ctx: CliContext, _args: argparse.Namespace) -> int:
    config = ctx.config
    if ctx.json_output:
        ctx.emit_json(config.as_mapping())
        return 0

    print_title(
        ctx,
        "Centsible configuration",
        subtitle=f"source: {config.source if config.source else 'defaults and environment'}",
    )
    ctx.print(
        table.render_key_values(
            (
                ("database.path", config.database_path),
                ("database resolved", config.resolved_database_path()),
                ("ledger.currency", config.currency),
                ("ledger.fiscal_year_start_month", config.fiscal_year_start_month),
                ("logging.level", config.log_level),
                ("logging.file", config.resolved_log_file() or "-"),
                ("format.date", config.date_format),
            )
        )
    )
    return 0


# ------------------------------------------------------------------------ db
# ------------------------------------------------------------------------ 数据库 (database)
def cmd_db_info(ctx: CliContext, _args: argparse.Namespace) -> int:
    services = ctx.services
    info = services.database.describe()
    stats = services.ledger.stats()

    payload = {"database": info, "ledger": stats}
    if ctx.json_output:
        ctx.emit_json(payload)
        return 0

    print_title(ctx, "Ledger database", subtitle=info["path"])
    ctx.print(
        table.render_key_values(
            (
                ("size", f"{info['size_bytes']:,} bytes"),
                ("schema version", info["schema_version"]),
                ("accounts", stats["accounts"]),
                ("transactions", stats["transactions"]),
                ("posted transactions", stats["posted_transactions"]),
                ("first transaction", stats["first_transaction"] or "-"),
                ("last transaction", stats["last_transaction"] or "-"),
                ("books closed through", stats["closed_through"] or "open"),
                ("currency (recorded)", stats["ledger_currency"] or "not recorded"),
                ("currency (configured)", ctx.config.currency),
            )
        )
    )
    return 0


def cmd_db_check(ctx: CliContext, _args: argparse.Namespace) -> int:
    services = ctx.services
    problems = services.database.integrity_check()
    trial = services.reports.trial_balance()
    if not trial.balanced:
        problems.append(
            "the journal does not balance: debits "
            f"{formatting.money_with_currency(trial.total_debit)} vs credits "
            f"{formatting.money_with_currency(trial.total_credit)}"
        )

    # A balanced journal can still hide an unbalanced entry: two corrupted rows
    # in different transactions cancel out at the total level.  Check every entry
    # on its own, because that is the invariant the whole model rests on.
    # 总分平衡的账簿里仍可能藏着不平衡的分录：两处被改坏的行分别落在不同凭证上，
    # 在合计数上正好互相抵消。因此逐张凭证单独校验——整个模型就建立在这个不变量上。
    with services.unit_of_work() as uow:
        unbalanced = uow.transactions.unbalanced_transaction_ids()
    if unbalanced:
        shown = ", ".join(f"#{item}" for item in unbalanced[:5])
        more = "" if len(unbalanced) <= 5 else f" and {len(unbalanced) - 5} more"
        problems.append(
            f"{len(unbalanced)} transaction(s) whose postings do not sum to zero: "
            f"{shown}{more}"
        )

    payload = {
        "database": str(services.database.path),
        "problems": problems,
        "balanced": trial.balanced,
        "accounts_checked": len(trial.rows),
        "unbalanced_transactions": unbalanced,
    }
    if ctx.json_output:
        ctx.emit_json(payload)
        return 0 if not problems else 5

    if problems:
        print_title(ctx, "Database check failed")
        for problem in problems:
            ctx.print(f"  - {problem}")
        return 5

    print_title(ctx, "Database check")
    ctx.print(
        f"  OK - integrity, foreign keys and the trial balance agree "
        f"({len(trial.rows)} account(s), {formatting.money_with_currency(trial.total_debit)} posted)"
    )
    ctx.print("  Every transaction balances on its own.")
    return 0


def cmd_db_backup(ctx: CliContext, args: argparse.Namespace) -> int:
    services = ctx.services
    destination = services.database.backup_to(args.out)
    if ctx.json_output:
        ctx.emit_json({"backup": str(destination)})
        return 0
    ctx.print(f"Backup written to {destination}")
    return 0


def cmd_db_restore(ctx: CliContext, args: argparse.Namespace) -> int:
    services = ctx.services
    # Validate the backup before the conflict question: a missing or corrupt
    # file deserves "not found" or "data problem", not "re-run with --force".
    # 先校验备份，再问冲突：文件缺失或损坏时该报“找不到”或“数据问题”，
    # 而不是让人去加 --force。
    services.database.validate_backup(args.file)
    stats = services.ledger.stats() if services.database.exists() else None
    transactions = stats["transactions"] if stats else 0
    if transactions and not args.force:
        raise ConflictError(
            f"the current ledger holds {transactions} transaction(s); "
            "refusing to overwrite it. Re-run with --force if the backup "
            "should really replace it."
        )

    destination = services.database.restore_from(args.file)

    # Record the restore in the restored ledger itself, so the audit trail
    # explains why the books suddenly changed.
    # 把这次恢复记录到恢复后的账簿里，让审计轨迹能够解释账簿为什么突然变了。
    with services.unit_of_work() as uow:
        uow.audit.record(
            action="database.restore",
            entity="database",
            detail=f"restored from {Path(args.file).name}",
        )
        uow.commit()

    after = services.ledger.stats()
    if ctx.json_output:
        ctx.emit_json(
            {
                "restored_from": str(args.file),
                "path": str(destination),
                "transactions": after["transactions"],
                "closed_through": after["closed_through"],
            }
        )
        return 0

    print_title(ctx, "Database restored")
    ctx.print(
        f"  restored from {args.file}",
        f"  {after['transactions']} transaction(s), "
        f"closed through {after['closed_through'] or 'open'}",
    )
    return 0


# -------------------------------------------------------------------- journal
# -------------------------------------------------------------------- 日记账
def cmd_journal_status(ctx: CliContext, _args: argparse.Namespace) -> int:
    services = ctx.services
    stats = services.ledger.stats()
    closed = stats["closed_through"]

    if ctx.json_output:
        ctx.emit_json({"closed_through": closed, "transactions": stats["transactions"]})
        return 0

    print_title(ctx, "Journal status")
    ctx.print(
        table.render_key_values(
            (
                ("closed through", closed or "open"),
                ("transactions", stats["transactions"]),
                ("posted", stats["posted_transactions"]),
                ("first transaction", stats["first_transaction"] or "-"),
                ("last transaction", stats["last_transaction"] or "-"),
                ("audit entries", stats["audit_entries"]),
            )
        )
    )
    return 0


def cmd_journal_lock(ctx: CliContext, args: argparse.Namespace) -> int:
    services = ctx.services
    day = (
        parse_date_arg(args.date, date_format=ctx.config.date_format)
        if args.date
        else services.clock.today()
    )
    locked = services.ledger.set_lock_date(day)
    if ctx.json_output:
        ctx.emit_json({"closed_through": locked.isoformat()})
        return 0
    ctx.print(
        f"The journal is now closed through {locked.isoformat()}.",
        "Transactions dated on or before that day will be rejected.",
    )
    return 0


def cmd_journal_unlock(ctx: CliContext, args: argparse.Namespace) -> int:
    services = ctx.services
    if not args.force:
        raise ConflictError(
            "reopening the books can change figures that were already reported; "
            "re-run with --force if that is really what you want"
        )
    previous = services.ledger.unlock(force=True)
    if ctx.json_output:
        ctx.emit_json({"reopened_from": previous.isoformat() if previous else None})
        return 0
    if previous is None:
        ctx.print("The journal was already open; nothing changed.")
    else:
        ctx.print(f"The journal is open again (it was closed through {previous.isoformat()}).")
    return 0


def cmd_journal_audit(ctx: CliContext, args: argparse.Namespace) -> int:
    services = ctx.services
    entries = services.ledger.audit_trail(limit=args.limit)

    if ctx.json_output:
        ctx.emit_json({"entries": [entry.to_dict() for entry in entries]})
        return 0

    if not entries:
        ctx.print("No audit entries yet.")
        return 0

    print_title(ctx, "Audit trail", subtitle=f"last {len(entries)} entries")
    rows = [
        (
            entry.at.isoformat(timespec="seconds"),
            entry.action,
            entry.entity,
            entry.entity_id or "",
            entry.detail,
        )
        for entry in entries
    ]
    ctx.print(
        table.render_table(
            ("when", "action", "entity", "id", "detail"),
            rows,
            aligns=("left", "left", "left", "right", "left"),
        )
    )
    return 0

