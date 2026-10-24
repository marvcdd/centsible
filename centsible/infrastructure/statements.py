"""Statement readers for delimited text and Excel workbooks.

Both formats feed the same parsing pipeline: a reader only has to produce raw
rows, and the shared logic below turns them into
:class:`~centsible.application.ports.StatementRow` objects (or per-line
problems).  That keeps the CSV and XLSX paths from drifting apart - the column
mapping, amount sign, date format and de-duplication rules are identical for
both.

Spreadsheets bring one extra idea: values can already be typed.  A date cell
arrives as :class:`datetime.date` and a money cell as ``Decimal``, so those are
taken at face value instead of being re-parsed from text.

分隔文本与 Excel 工作簿的银行对账单读取器。

两种格式共用同一条解析流水线：读取器只要产出原始行，下面的公共逻辑就负责把这些
行转换成 :class:`~centsible.application.ports.StatementRow` 对象（或逐行记录的
问题）。这样 CSV 与 XLSX 两条路径就不会各自跑偏——列映射、金额正负号、日期格式
与去重规则对两者完全一致。

电子表格还多出一个概念：值本身可能已经带类型。日期单元格以
:class:`datetime.date` 形式到达，金额单元格以 ``Decimal`` 形式到达，因此这类值
直接按原样采信，而不再从文本重新解析。
"""

from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

from centsible.application.ports import (
    StatementIssue,
    StatementMapping,
    StatementRow,
)
from centsible.domain.money import parse_decimal
from centsible.domain.periods import parse_date
from centsible.errors import NotFoundError, ValidationError
from centsible.infrastructure import xlsx
from centsible.infrastructure.csvio import read_csv_rows

__all__ = [
    "CSV_SUFFIXES",
    "XLSX_SUFFIXES",
    "CsvStatementReader",
    "StatementReaderRegistry",
    "XlsxStatementReader",
    "statement_reader_for",
]

CSV_SUFFIXES = frozenset({".csv", ".tsv", ".txt"})
XLSX_SUFFIXES = frozenset({".xlsx", ".xlsm"})

_COLUMN_FIELDS = {
    "date": "date_column",
    "amount": "amount_column",
    "debit": "debit_column",
    "credit": "credit_column",
    "description": "description_column",
    "payee": "payee_column",
    "id": "id_column",
}


class _TableStatementReader:
    """Shared mapping and parsing logic for any tabular statement.

    任意表格型对账单共用的映射与解析逻辑。"""

    def read(
        self, path: Path, mapping: StatementMapping
    ) -> tuple[list[StatementRow], list[StatementIssue]]:
        source = Path(path)
        if not source.exists():
            raise NotFoundError(f"statement file not found: {source}")
        if not source.is_file():
            raise ValidationError(f"{source} is not a file", field="path")

        table = self._load_rows(source, mapping)
        if not table:
            raise ValidationError(f"{source.name} contains no rows", field="path")

        header = [_text(cell).strip() for cell in table[0]] if mapping.has_header else None
        body = table[1:] if mapping.has_header else table
        if mapping.skip_rows:
            body = body[mapping.skip_rows :]

        indexes = {
            field: _resolve_index(getattr(mapping, attribute), header, field=field)
            for field, attribute in _COLUMN_FIELDS.items()
        }

        offset = 2 if mapping.has_header else 1
        rows: list[StatementRow] = []
        issues: list[StatementIssue] = []
        #: How often each external id has been seen so far in this file.
        #: 本文件中每个外部 ID 到目前为止出现过的次数。
        occurrences: dict[str, int] = {}

        for position, raw in enumerate(body):
            line_number = offset + mapping.skip_rows + position
            if not raw or not any(_text(cell) for cell in raw):
                continue
            try:
                day = _date_value(_cell(raw, indexes["date"], "date"), mapping)
                amount = _amount_value(raw, indexes, mapping)
                description = _text(_cell(raw, indexes["description"], "description")).strip()
                payee = _text(_cell(raw, indexes["payee"], "payee")).strip()
                external_id = _text(_cell(raw, indexes["id"], "id")).strip()
            except ValidationError as exc:
                issues.append(StatementIssue(line_number=line_number, message=str(exc)))
                continue

            if not description:
                description = f"Statement line {line_number}"
            if not external_id:
                external_id = f"{day.isoformat()}|{amount:f}|{description}"

            # Two identical statement lines are two real transactions, not one
            # duplicated twice: number the repeats so the importer can decide
            # (``--keep-duplicate-lines``) instead of silently dropping one.  The
            # first occurrence keeps the plain id, so fingerprints written before
            # this rule existed stay valid.
            # 两行完全相同的对账单是两笔真实业务，而不是同一笔重复两次：给重复出现的
            # 行编号，让导入器自行决定（``--keep-duplicate-lines``），而不是悄悄丢掉
            # 一行。第一次出现仍使用原始 ID，因此这条规则出现之前写入的指纹依然有效。
            seen = occurrences.get(external_id, 0) + 1
            occurrences[external_id] = seen

            rows.append(
                StatementRow(
                    line_number=line_number,
                    date=day,
                    amount=amount,
                    description=description,
                    payee=payee,
                    external_id=external_id,
                    occurrence=seen,
                )
            )

        return rows, issues

    def _load_rows(
        self, path: Path, mapping: StatementMapping
    ) -> Sequence[Sequence[Any]]:
        raise NotImplementedError  # pragma: no cover


class CsvStatementReader(_TableStatementReader):
    """Read a delimited statement (comma, semicolon, tab, ...).

    读取一份带分隔符的对账单（逗号、分号、制表符等）。"""

    def _load_rows(
        self, path: Path, mapping: StatementMapping
    ) -> Sequence[Sequence[Any]]:
        try:
            return read_csv_rows(
                path, delimiter=mapping.delimiter, encoding=mapping.encoding
            )
        except UnicodeDecodeError as exc:
            raise ValidationError(
                f"{path.name} cannot be decoded as {mapping.encoding}; "
                "try --encoding latin-1 or --encoding gb18030",
                field="encoding",
            ) from exc
        except OSError as exc:
            raise ValidationError(f"cannot read {path}: {exc}", field="path") from exc
        except csv.Error as exc:
            raise ValidationError(
                f"{path.name} is not a valid delimited file: {exc}", field="path"
            ) from exc


class XlsxStatementReader(_TableStatementReader):
    """Read one worksheet of an ``.xlsx``/``.xlsm`` workbook.

    读取 ``.xlsx``/``.xlsm`` 工作簿中的一张工作表。"""

    def _load_rows(
        self, path: Path, mapping: StatementMapping
    ) -> Sequence[Sequence[Any]]:
        return xlsx.read_worksheet(path, sheet=_selector(mapping.sheet)).rows


class StatementReaderRegistry:
    """Implements the ``StatementReader`` port by dispatching on the file suffix.

    按文件后缀分派，实现 ``StatementReader`` 端口接口。"""

    @staticmethod
    def read(
        path: Path, mapping: StatementMapping
    ) -> tuple[list[StatementRow], list[StatementIssue]]:
        return statement_reader_for(path).read(path, mapping)


def statement_reader_for(path: str | Path) -> _TableStatementReader:
    """Pick a reader for ``path`` (``.csv``/``.tsv``/``.txt`` or ``.xlsx``/``.xlsm``).

    为 ``path`` 挑一个读取器（``.csv``/``.tsv``/``.txt`` 或 ``.xlsx``/``.xlsm``）。"""

    suffix = Path(path).suffix.lower()
    if suffix in CSV_SUFFIXES:
        return CsvStatementReader()
    if suffix in XLSX_SUFFIXES:
        return XlsxStatementReader()
    supported = ", ".join(sorted(CSV_SUFFIXES | XLSX_SUFFIXES))
    raise ValidationError(
        f"unsupported statement file type {suffix or '(none)'}; expected one of {supported}",
        field="path",
    )


# ------------------------------------------------------------------- helpers
# ------------------------------------------------------------------- 辅助函数
def _selector(sheet: str | None) -> int | str | None:
    if sheet is None:
        return None
    text = str(sheet).strip()
    if not text:
        return None
    return int(text) if text.isdigit() else text


def _text(cell: Any) -> str:
    if cell is None:
        return ""
    if isinstance(cell, str):
        return cell
    return str(cell)


def _resolve_index(
    spec: str | None, header: Sequence[str] | None, *, field: str
) -> int | None:
    if spec is None:
        return None
    text = str(spec).strip()
    if not text:
        return None

    if header is not None:
        lowered = text.lower()
        for index, name in enumerate(header):
            if name.strip().lower() == lowered:
                return index

    if text.isdigit():
        return int(text)

    if header is None:
        raise ValidationError(
            f"{field} column {text!r} must be a zero based index because the file "
            "has no header",
            field="mapping",
        )
    raise ValidationError(
        f"{field} column {text!r} is not present in the header "
        f"({', '.join(header) or 'empty'})",
        field="mapping",
    )


def _cell(raw: Sequence[Any], index: int | None, field: str) -> Any:
    if index is None:
        return None
    if index >= len(raw):
        raise ValidationError(
            f"expected at least {index + 1} columns to read the {field} column, "
            f"found {len(raw)}",
            field="mapping",
        )
    return raw[index]


def _date_value(cell: Any, mapping: StatementMapping) -> date:
    if isinstance(cell, datetime):
        return cell.date()
    if isinstance(cell, date):
        return cell
    text = _text(cell).strip()
    if not text:
        raise ValidationError("a date is required", field="date")
    return parse_date(text, date_format=mapping.date_format)


def _amount_cell(cell: Any, field: str, *, empty_is_zero: bool) -> Decimal:
    if cell is None:
        return Decimal(0)
    if isinstance(cell, Decimal):
        return cell
    if isinstance(cell, bool):
        raise ValidationError("a boolean is not an amount", field=field)
    if isinstance(cell, int):
        return Decimal(cell)
    text = _text(cell).strip()
    if not text:
        if empty_is_zero:
            return Decimal(0)
        raise ValidationError("amount is required", field=field)
    return parse_decimal(text)


def _amount_value(
    raw: Sequence[Any], indexes: dict[str, int | None], mapping: StatementMapping
) -> Decimal:
    if indexes["amount"] is not None:
        value = _amount_cell(
            _cell(raw, indexes["amount"], "amount"), "amount", empty_is_zero=False
        )
    else:
        debit_amount = _amount_cell(
            _cell(raw, indexes["debit"], "debit"), "debit", empty_is_zero=True
        )
        credit_amount = _amount_cell(
            _cell(raw, indexes["credit"], "credit"), "credit", empty_is_zero=True
        )
        value = credit_amount - debit_amount
    return value * mapping.amount_sign
