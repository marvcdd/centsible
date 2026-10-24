"""Import and export commands.

导入与导出命令。"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from centsible.application.import_service import StatementImportRequest
from centsible.application.ports import MatchRule, StatementMapping
from centsible.errors import ValidationError
from centsible.infrastructure.csvio import write_csv
from centsible.infrastructure.jsonio import write_json
from centsible.infrastructure.statements import XLSX_SUFFIXES
from centsible.infrastructure.xlsx import write_workbook
from centsible.interface.cli import table
from centsible.interface.cli.commands.common import add_period_arguments, print_title, resolve_period_arg
from centsible.interface.cli.context import CliContext
from centsible.interface.cli.parsing import parse_date_arg

__all__ = ["register"]


def register(subparsers: argparse._SubParsersAction) -> None:
    import_group = subparsers.add_parser(
        "import", help="import bank or card statements (CSV or Excel)"
    )
    import_sub = import_group.add_subparsers(dest="import_command", required=True, metavar="ACTION")

    bank = import_sub.add_parser(
        "bank",
        help="import a statement file (.csv, .tsv, .xlsx, .xlsm)",
        description=(
            "Every row becomes one balanced transaction between the statement account "
            "and the counter account. Rows already imported (same account, date, "
            "amount and reference) are skipped, so re-importing is safe. A line that "
            "repeats an earlier line of the same file is skipped too, and reported, "
            "unless --keep-duplicate-lines is given. Excel workbooks are read without "
            "any third party library."
        ),
        epilog=(
            "examples:\n"
            "  centsible import bank statement.csv --account 1010 --counter-account 5900\n"
            "  centsible import bank statement.xlsx --account 1010 --counter-account 5900 \\\n"
            "      --sheet 流水\n"
            "  centsible import bank may.csv --account 1010 --counter-account 5500 \\\n"
            "      --date-col Date --amount-col Amount --desc-col Narrative\n"
            "  centsible import bank export.csv --account 1010 --counter-account 5900 \\\n"
            "      --debit-col Withdrawal --credit-col Deposit --date-format %d/%m/%Y\n"
            "  centsible import bank card.csv --account 1010 --counter-account 5900 \\\n"
            "      --match-rule coffee=5800 --match-rule rent=5100\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    bank.add_argument("file", metavar="FILE")
    bank.add_argument("--account", required=True, help="the account the statement belongs to")
    bank.add_argument("--counter-account", dest="counter_account", required=True, help="the opposing account")
    bank.add_argument("--source", default="bank", help="label recorded on the import batch")
    bank.add_argument("--dry-run", action="store_true", help="validate without writing")
    bank.add_argument("--allow-future", action="store_true", help="accept future dates")
    bank.add_argument(
        "--match-rule",
        action="append",
        dest="match_rules",
        metavar="PATTERN=ACCOUNT",
        help=(
            "map rows whose description or payee contains PATTERN "
            "(case-insensitive) to ACCOUNT instead of --counter-account; "
            "repeatable, the first matching rule wins"
        ),
    )
    bank.add_argument(
        "--keep-duplicate-lines",
        dest="keep_duplicate_lines",
        action="store_true",
        help=(
            "import lines that repeat the same reference inside the file "
            "(default: skip them as duplicated export lines)"
        ),
    )
    layout = bank.add_argument_group("statement layout")
    layout.add_argument("--sheet", metavar="NAME", help="worksheet name or index (Excel only; default: first)")
    layout.add_argument("--no-header", dest="has_header", action="store_false", help="the file has no header row")
    layout.add_argument("--date-col", default="0", metavar="COL", help="column name or index (default: 0)")
    layout.add_argument("--amount-col", default="1", metavar="COL", help="signed amount column (default: 1)")
    layout.add_argument("--debit-col", metavar="COL", help="money-out column (instead of --amount-col)")
    layout.add_argument("--credit-col", metavar="COL", help="money-in column (instead of --amount-col)")
    layout.add_argument("--desc-col", default="2", metavar="COL", help="description column (default: 2)")
    layout.add_argument("--payee-col", metavar="COL", help="counterparty column")
    layout.add_argument("--id-col", metavar="COL", help="unique reference column")
    layout.add_argument("--date-format", metavar="FMT", help="strftime format, e.g. %%d/%%m/%%Y")
    layout.add_argument("--delimiter", default=",", help="field separator (default: ',')")
    layout.add_argument("--encoding", default="utf-8-sig", help="file encoding (default: utf-8-sig)")
    layout.add_argument("--amount-sign", type=int, choices=(1, -1), default=1, help="flip the amount sign if needed")
    bank.set_defaults(handler=cmd_bank)

    batches = import_sub.add_parser("batches", help="recent import runs")
    batches.add_argument("--limit", type=int, default=10, help="how many (default: %(default)s)")
    batches.set_defaults(handler=cmd_batches)

    export_group = subparsers.add_parser("export", help="export journal data")
    export_sub = export_group.add_subparsers(dest="export_command", required=True, metavar="ACTION")

    journal = export_sub.add_parser(
        "journal",
        help="write the journal (CSV by default; .xlsx gives a typed workbook)",
        description=(
            "One row per posting. If the output file ends in .xlsx or .xlsm an "
            "Excel workbook is written instead of CSV, with typed date and money "
            "columns."
        ),
    )
    journal.add_argument("--out", required=True, metavar="FILE")
    add_period_arguments(journal, month_help="export a single month")
    journal.add_argument("--posted-only", action="store_true", help="skip voided entries")
    journal.set_defaults(handler=cmd_export_journal)

    workbook = export_sub.add_parser(
        "workbook",
        help="write the full reporting pack as an .xlsx workbook",
        description=(
            "One Excel file with nine sheets: summary, monthly activity, trial "
            "balance, income statement, balance sheet, budgets, journal, recurring "
            "rules and the chart of accounts. Generated from the same figures the "
            "text reports show."
        ),
    )
    workbook.add_argument("--out", required=True, metavar="FILE")
    add_period_arguments(workbook, month_help="the reporting period (default: current month)")
    workbook.add_argument("--as-of", dest="as_of", metavar="DATE", help="balance sheet date (default: period end)")
    workbook.add_argument("--months", type=int, default=12, help="months of activity history (default: %(default)s)")
    workbook.set_defaults(handler=cmd_export_workbook)

    backup = export_sub.add_parser("backup", help="write a complete JSON backup")
    backup.add_argument("--out", required=True, metavar="FILE")
    backup.set_defaults(handler=cmd_export_backup)


def _parse_match_rules(specs: Sequence[str] | None) -> tuple[MatchRule, ...]:
    """Turn ``--match-rule PATTERN=ACCOUNT`` arguments into match rules.

    把 ``--match-rule PATTERN=ACCOUNT`` 参数解析成匹配规则。"""

    rules: list[MatchRule] = []
    for spec in specs or []:
        pattern, separator, account = str(spec).partition("=")
        if not separator or not pattern.strip() or not account.strip():
            raise ValidationError(
                "each --match-rule must look like PATTERN=ACCOUNT, "
                f"e.g. rent=5100; got {spec!r}",
                field="match-rule",
            )
        rules.append(MatchRule(pattern=pattern, account_code=account))
    return tuple(rules)


def cmd_bank(ctx: CliContext, args: argparse.Namespace) -> int:
    split_columns = bool(args.debit_col) or bool(args.credit_col)
    mapping = StatementMapping(
        date_column=args.date_col,
        amount_column=None if split_columns else args.amount_col,
        debit_column=args.debit_col,
        credit_column=args.credit_col,
        description_column=args.desc_col,
        payee_column=args.payee_col,
        id_column=args.id_col,
        date_format=args.date_format,
        delimiter=args.delimiter,
        encoding=args.encoding,
        has_header=args.has_header,
        amount_sign=args.amount_sign,
        sheet=args.sheet,
    )
    request = StatementImportRequest(
        path=Path(args.file),
        account_code=args.account,
        counter_account=args.counter_account,
        mapping=mapping,
        source=args.source,
        dry_run=args.dry_run,
        allow_future=args.allow_future,
        keep_duplicate_lines=args.keep_duplicate_lines,
        rules=_parse_match_rules(args.match_rules),
    )
    result = ctx.services.imports.import_statement(request)

    if ctx.json_output:
        ctx.emit_json(result.to_dict())
        return 0 if result.ok else 2

    prefix = "Dry run - " if result.dry_run else ""
    ctx.print(
        f"{prefix}read {result.rows_read} row(s) from {result.filename}",
        "",
    )
    ctx.print(
        table.render_key_values(
            (
                ("imported", result.imported),
                ("duplicates skipped", result.duplicates),
                ("repeated lines skipped", result.repeated),
                ("matched by rules", result.matched),
                ("failed", result.failed),
                ("batch", result.batch_id if result.batch_id is not None else "-"),
            )
        )
    )
    if result.rule_usage:
        ctx.print("", "  Rule matches:")
        for key, count in result.rule_usage.items():
            ctx.print(f"    {key} : {count} row(s)")
    if result.repeated and not args.keep_duplicate_lines:
        ctx.print(
            "",
            f"  {result.repeated} line(s) repeated an earlier line of the same file;",
            "  they were skipped. If they are separate transactions (two identical",
            "  purchases on one day), re-run with --keep-duplicate-lines.",
        )
    if result.issues:
        ctx.print("", "  Problems:")
        rows = [(issue.line_number, issue.message) for issue in result.issues]
        ctx.print(
            table.render_table(
                ("line", "message"),
                rows,
                aligns=("right", "left"),
                indent="    ",
            )
        )
    return 0 if result.ok else 2


def cmd_batches(ctx: CliContext, args: argparse.Namespace) -> int:
    batches = ctx.services.imports.batches(limit=args.limit)

    if ctx.json_output:
        ctx.emit_json(
            {
                "count": len(batches),
                "batches": [
                    {
                        "id": batch.id,
                        "source": batch.source,
                        "filename": batch.filename,
                        "imported_at": batch.imported_at.isoformat(timespec="seconds"),
                        "rows_read": batch.rows_read,
                        "rows_imported": batch.rows_imported,
                        "rows_skipped": batch.rows_skipped,
                    }
                    for batch in batches
                ],
            }
        )
        return 0

    if not batches:
        ctx.print("No statement has been imported yet.")
        return 0

    print_title(ctx, "Import batches", subtitle=f"last {len(batches)}")
    rows = [
        (
            batch.id,
            batch.imported_at.isoformat(timespec="seconds"),
            batch.source,
            batch.filename,
            batch.rows_read,
            batch.rows_imported,
            batch.rows_skipped,
        )
        for batch in batches
    ]
    ctx.print(
        table.render_table(
            ("id", "when", "source", "file", "read", "imported", "skipped"),
            rows,
            aligns=("right", "left", "left", "left", "right", "right", "right"),
        )
    )
    return 0


def cmd_export_journal(ctx: CliContext, args: argparse.Namespace) -> int:
    period = resolve_period_arg(ctx, args)
    destination = Path(args.out)
    if destination.suffix.lower() in XLSX_SUFFIXES:
        workbook = ctx.services.exports.journal_workbook(
            period, include_void=not args.posted_only
        )
        write_workbook(destination, workbook, generated_at=ctx.services.clock.now())
        if ctx.json_output:
            ctx.emit_json({"file": str(destination), **workbook.to_dict()})
            return 0
        ctx.print(
            f"Wrote {workbook.total_rows} posting row(s) as an Excel workbook "
            f"to {destination}"
        )
        return 0

    export = ctx.services.exports.journal(period, include_void=not args.posted_only)
    written = write_csv(destination, export.headers, export.rows)

    if ctx.json_output:
        ctx.emit_json({"file": str(destination), "rows": written, **export.to_dict()})
        return 0
    ctx.print(
        f"Wrote {written} posting row(s) from {export.transaction_count} transaction(s) "
        f"to {destination}"
    )
    return 0


def cmd_export_workbook(ctx: CliContext, args: argparse.Namespace) -> int:
    period = resolve_period_arg(ctx, args, default=ctx.services.reports.current_month())
    as_of = (
        parse_date_arg(args.as_of, date_format=ctx.config.date_format)
        if args.as_of
        else None
    )
    workbook = ctx.services.report_pack.build(
        period, as_of=as_of, months=args.months
    )
    destination = write_workbook(
        args.out, workbook, generated_at=ctx.services.clock.now()
    )

    if ctx.json_output:
        ctx.emit_json({"file": str(destination), **workbook.to_dict()})
        return 0
    ctx.print(
        f"Wrote {workbook.sheet_count} sheet(s), {workbook.total_rows} data row(s) "
        f"to {destination}"
    )
    for sheet in workbook.sheets:
        ctx.print(f"  - {sheet.title}: {sheet.row_count} row(s)")
    return 0


def cmd_export_backup(ctx: CliContext, args: argparse.Namespace) -> int:
    payload = ctx.services.exports.backup()
    destination = write_json(args.out, payload.data)

    if ctx.json_output:
        ctx.emit_json({"file": str(destination), **payload.to_dict()})
        return 0
    ctx.print(f"Backup written to {destination}")
    ctx.print(
        table.render_key_values(
            [(key, value) for key, value in payload.counts.items()], indent="  "
        )
    )
    return 0
