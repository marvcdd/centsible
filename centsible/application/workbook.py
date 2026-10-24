"""Spreadsheet-shaped data transfer objects.

These are intentionally storage agnostic: a :class:`Workbook` is what a report
pack *means* (sheets, typed columns, rows), not how it is encoded.  The xlsx
writer in the infrastructure layer turns it into an OOXML file; a future CSV or
ODS writer could consume the same object unchanged.

Cell values are one of ``str``, ``int``, ``bool``, ``Decimal``, ``date`` or
``None``.  ``Decimal`` and ``date`` exist as first-class kinds so that money and
dates reach the spreadsheet as *numbers*, not as text that needs cleaning up.

表格形态的数据传输对象。

这些对象刻意与存储方式无关：:class:`Workbook` 描述的是报表包的*含义*（工作表、
带类型的列、数据行），而不是它的编码方式。基础设施层的 xlsx 写出器把它转换成
OOXML 文件；将来实现 CSV 或 ODS 写出器时，也能原封不动地复用同一个对象。

单元格取值为 ``str``、``int``、``bool``、``Decimal``、``date`` 或 ``None`` 之一。
``Decimal`` 与 ``date`` 作为一等类型存在，是为了让金额和日期以*数值*的形式抵达
电子表格，而不是需要清洗的文本。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any

from centsible.errors import ValidationError

__all__ = [
    "CellValue",
    "Column",
    "ColumnKind",
    "MAX_COLUMNS",
    "MAX_ROWS",
    "MAX_SHEET_TITLE",
    "Sheet",
    "Workbook",
]

MAX_SHEET_TITLE = 31
MAX_COLUMNS = 64
MAX_ROWS = 100_000

_INVALID_TITLE_CHARS = set('[]:*?/\\')


class ColumnKind(str, Enum):
    """How a column's values should be typed and formatted in the output.

    一列的值在输出中应如何确定类型并格式化。"""

    TEXT = "text"
    MONEY = "money"
    DATE = "date"
    NUMBER = "number"

    @classmethod
    def parse(cls, value: "ColumnKind | str") -> "ColumnKind":
        if isinstance(value, cls):
            return value
        if not isinstance(value, str):
            raise ValidationError("column kind must be text, money, date or number")
        try:
            return cls(value.strip().lower())
        except ValueError:
            raise ValidationError(
                f"{value!r} is not a column kind; choose text, money, date or number"
            ) from None


CellValue = str | int | bool | Decimal | date | None


@dataclass(frozen=True, slots=True)
class Column:
    """One typed column of a sheet.

    工作表中带类型的一列。"""

    header: str
    kind: ColumnKind = ColumnKind.TEXT
    width: float | None = None

    def __post_init__(self) -> None:
        header = str(self.header).strip()
        if not header:
            raise ValidationError("column header is required", field="header")
        object.__setattr__(self, "header", header)
        object.__setattr__(self, "kind", ColumnKind.parse(self.kind))
        if self.width is not None and not 1.0 <= float(self.width) <= 255.0:
            raise ValidationError("column width must be between 1 and 255", field="width")


@dataclass(frozen=True, slots=True)
class Sheet:
    """A titled table with an optional heading, subtitle and totals row.

    一张带标题的表格，可选配大标题、副标题和合计行。"""

    title: str
    columns: tuple[Column, ...]
    rows: tuple[tuple[CellValue, ...], ...] = ()
    heading: str | None = None
    subtitle: str | None = None
    footer: tuple[CellValue, ...] | None = None
    freeze_header: bool = True
    autofilter: bool = True

    def __post_init__(self) -> None:
        title = str(self.title).strip()
        if not title:
            raise ValidationError("sheet title is required", field="title")
        if len(title) > MAX_SHEET_TITLE:
            raise ValidationError(
                f"sheet title {title!r} must be at most {MAX_SHEET_TITLE} characters",
                field="title",
            )
        if any(character in _INVALID_TITLE_CHARS for character in title):
            raise ValidationError(
                f"sheet title {title!r} must not contain any of []:*?/\\",
                field="title",
            )
        if title.startswith("'") or title.endswith("'"):
            raise ValidationError(
                "sheet titles must not start or end with an apostrophe", field="title"
            )
        object.__setattr__(self, "title", title)

        columns = tuple(
            column if isinstance(column, Column) else Column(header=str(column))
            for column in self.columns
        )
        if not columns:
            raise ValidationError("a sheet needs at least one column", field="columns")
        if len(columns) > MAX_COLUMNS:
            raise ValidationError(
                f"a sheet may have at most {MAX_COLUMNS} columns", field="columns"
            )
        object.__setattr__(self, "columns", columns)

        rows = tuple(tuple(row) for row in self.rows)
        if len(rows) > MAX_ROWS:
            raise ValidationError(
                f"a sheet may have at most {MAX_ROWS} rows", field="rows"
            )
        for index, row in enumerate(rows, start=1):
            if len(row) > len(columns):
                raise ValidationError(
                    f"row {index} has {len(row)} values but the sheet has "
                    f"{len(columns)} columns",
                    field="rows",
                )
        object.__setattr__(self, "rows", rows)

        if self.footer is not None:
            footer = tuple(self.footer)
            if len(footer) > len(columns):
                raise ValidationError(
                    "the footer row cannot be wider than the sheet", field="footer"
                )
            object.__setattr__(self, "footer", footer)

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return len(self.columns)


@dataclass(frozen=True, slots=True)
class Workbook:
    """An ordered collection of sheets.

    一组有序的工作表。"""

    title: str
    sheets: tuple[Sheet, ...]
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        title = str(self.title).strip()
        if not title:
            raise ValidationError("workbook title is required", field="title")
        object.__setattr__(self, "title", title)

        sheets = tuple(self.sheets)
        if not sheets:
            raise ValidationError("a workbook needs at least one sheet", field="sheets")
        titles = [sheet.title for sheet in sheets]
        duplicates = {title for title in titles if titles.count(title) > 1}
        if duplicates:
            raise ValidationError(
                f"duplicate sheet title(s): {', '.join(sorted(duplicates))}",
                field="sheets",
            )
        object.__setattr__(self, "sheets", sheets)

    @property
    def sheet_count(self) -> int:
        return len(self.sheets)

    @property
    def total_rows(self) -> int:
        return sum(sheet.row_count for sheet in self.sheets)

    def sheet_titles(self) -> tuple[str, ...]:
        return tuple(sheet.title for sheet in self.sheets)

    def to_dict(self) -> dict[str, Any]:
        """Summary used by ``--json`` output (never the full data).

        供 ``--json`` 输出使用的摘要（绝不含完整数据）。"""

        return {
            "title": self.title,
            "sheets": [
                {
                    "title": sheet.title,
                    "columns": [column.header for column in sheet.columns],
                    "rows": sheet.row_count,
                }
                for sheet in self.sheets
            ],
            "total_rows": self.total_rows,
        }
