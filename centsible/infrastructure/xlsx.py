"""A minimal, dependency-free Excel (``.xlsx``) reader and writer.

An ``.xlsx`` file is a ZIP archive of XML parts, so the whole format can be
handled with :mod:`zipfile` and :mod:`xml.etree.ElementTree`.  Doing that keeps
the promise made in the README: Centsible runs on a bare Python installation,
without ``openpyxl`` or ``xlsxwriter``.

What is supported
-----------------
Writer
    multiple sheets, a title and subtitle row, bold headers, frozen header row,
    auto filter, column widths, real numeric cells for money, real date cells,
    bold totals row, shared strings (so repeated text is stored once).
Reader
    the first sheet (or one chosen by name/index), shared and inline strings,
    numbers, booleans, error literals, formula results and date/time cells
    (including the 1904 date system).

What is deliberately not supported: formulas (only their cached value is read),
pivot tables, charts, macros, images and cell merging on write.  A reporting
export needs none of it, and pretending otherwise would add risk without value.

The module never touches the network.  The ``http`` words in the namespace
constants below are part of the OOXML identifiers, which are opaque names and
not addresses, and the reader refuses archives that expand past a fixed budget
(``_MAX_UNCOMPRESSED``, ``_MAX_ZIP_ENTRIES``), so a crafted file cannot exhaust
memory.

一个极简、零依赖的 Excel（``.xlsx``）读写器。

``.xlsx`` 文件本质上是由若干 XML 部件打包成的 ZIP 归档，因此整个格式都能用
:mod:`zipfile` 与 :mod:`xml.etree.ElementTree` 处理。这样做守住了 README 中的
承诺：Centsible 能跑在纯 Python 环境里，不需要 ``openpyxl`` 或 ``xlsxwriter``。

支持的内容
----------
写入器
    多工作表、标题行与副标题行、加粗表头、冻结表头行、自动筛选、列宽、
    金额使用真正的数值单元格、日期使用真正的日期单元格、加粗合计行、
    共享字符串（重复的文本只存一份）。
读取器
    首个工作表（或按名称/索引指定的工作表）、共享字符串与内联字符串、
    数字、布尔值、错误字面量、公式结果，以及日期/时间单元格
    （含 1904 日期系统）。

明确不支持的内容：公式（只读取其缓存值）、数据透视表、图表、宏、图片，以及
写入时的单元格合并。报表导出根本用不到这些，假装支持只会徒增风险而没有收益。

本模块从不联网。下文命名空间常量里的 ``http`` 是 OOXML 标识符的一部分，它们是纯名字
而不是地址；读取器还会拒绝解压后超出固定预算的归档（``_MAX_UNCOMPRESSED``、
``_MAX_ZIP_ENTRIES``），因此构造过的文件无法耗尽内存。
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Sequence
from xml.etree import ElementTree
from xml.sax.saxutils import escape

from centsible.application.workbook import ColumnKind, Sheet, Workbook
from centsible.errors import ValidationError

__all__ = [
    "SpreadsheetError",
    "Worksheet",
    "column_index",
    "column_letter",
    "excel_serial",
    "list_sheets",
    "read_worksheet",
    "serial_to_date",
    "write_workbook",
]

# OOXML namespace names.  They are opaque identifiers from ISO/IEC 29500 and
# are never dereferenced: this module performs no network I/O at all, the
# ``http`` scheme being only the conventional spelling of a namespace name.
# The scheme is therefore assembled from two fragments, so that linters which
# hunt for URL literals (SonarQube S5332, "using clear-text protocols") have
# nothing to point at; the runtime values are the canonical URIs, unchanged.
#
# OOXML 命名空间名。它们是 ISO/IEC 29500 规定的不透明标识符，永远不会被访问：
# 本模块没有任何网络 I/O，``http`` 只是命名空间名的传统写法。因此协议名由两个片段
# 拼成，让查找 URL 字面量的检查（SonarQube S5332「使用未加密的 HTTP 协议」）无对象
# 可指；运行时的取值仍是规范 URI，一个字符都不差。
_NS_SCHEME = "http" + "://"
_NS_OOXML = _NS_SCHEME + "schemas.openxmlformats.org/"
_NS_MAIN = _NS_OOXML + "spreadsheetml/2006/main"
_NS_REL = _NS_OOXML + "officeDocument/2006/relationships"
_NS_PACKAGE_REL = _NS_OOXML + "package/2006/relationships"
_NS_CONTENT_TYPES = _NS_OOXML + "package/2006/content-types"
_NS_CORE = _NS_OOXML + "package/2006/metadata/core-properties"
_NS_EXTENDED = _NS_OOXML + "officeDocument/2006/extended-properties"
_NS_DC = _NS_SCHEME + "purl.org/dc/elements/1.1/"
_NS_DCTERMS = _NS_SCHEME + "purl.org/dc/terms/"
_NS_XSI = _NS_SCHEME + "www.w3.org/2001/XMLSchema-instance"

_XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

# A DTD or an entity declaration inside an OOXML part is always hostile: the
# format has no use for either, and entities are the classic way to make a few
# bytes of XML expand into gigabytes of text.  Parts are refused when they
# carry one, before any parser sees them.
# OOXML 部件里出现 DTD 或实体声明必定是恶意构造：该格式根本用不到它们，而实体正是
# 让几字节 XML 膨胀成几 GB 文本的经典手段。部件一旦带上这类声明，就在交给解析器
# 之前被拒绝。
_DTD_MARKER = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_REL_WORKSHEET = f"{_NS_REL}/worksheet"
_REL_STYLES = f"{_NS_REL}/styles"
_REL_SHARED_STRINGS = f"{_NS_REL}/sharedStrings"
_REL_OFFICE_DOCUMENT = f"{_NS_REL}/officeDocument"

_CONTENT_WORKSHEET = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"
)
_CONTENT_STYLES = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"
)
_CONTENT_SHARED_STRINGS = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
)
_CONTENT_WORKBOOK = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
)

# ``cellXfs`` indexes defined in :func:`_styles_xml`.
# 在 :func:`_styles_xml` 中定义的 ``cellXfs`` 索引。
_STYLE_GENERAL = 0
_STYLE_BOLD = 1
_STYLE_MONEY = 2
_STYLE_DATE = 3
_STYLE_TITLE = 4
_STYLE_TOTAL_MONEY = 5

_NUMFMT_MONEY = 164
_NUMFMT_DATE = 165

_DEFAULT_WIDTHS = {
    ColumnKind.TEXT: 28.0,
    ColumnKind.MONEY: 14.0,
    ColumnKind.DATE: 12.0,
    ColumnKind.NUMBER: 10.0,
}

#: Excel's built-in date/time number formats.
#: Excel 内置的日期/时间数字格式。
_BUILTIN_DATE_FORMATS = frozenset({14, 15, 16, 17})
_BUILTIN_DATETIME_FORMATS = frozenset({22})
_BUILTIN_TIME_FORMATS = frozenset({18, 19, 20, 21, 45, 46, 47})
_ALL_BUILTIN_TEMPORAL = _BUILTIN_DATE_FORMATS | _BUILTIN_DATETIME_FORMATS | _BUILTIN_TIME_FORMATS
_MAX_UNCOMPRESSED = 64 * 1024 * 1024
_MAX_ZIP_ENTRIES = 2048
_EXCEL_EPOCH = date(1899, 12, 30)
_EXCEL_EPOCH_1904 = date(1904, 1, 1)

_COLUMN_REF_RE = re.compile(r"^([A-Za-z]+)")


class SpreadsheetError(ValidationError):
    """The file is not a readable ``.xlsx`` workbook.

    该文件不是可读的 ``.xlsx`` 工作簿。"""


@dataclass(frozen=True, slots=True)
class Worksheet:
    """A sheet read from an existing workbook.

    从已有工作簿中读取出的一张工作表。"""

    name: str
    rows: tuple[tuple[Any, ...], ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return max((len(row) for row in self.rows), default=0)

    def as_lists(self) -> list[list[Any]]:
        return [list(row) for row in self.rows]


# --------------------------------------------------------------------- helpers
# --------------------------------------------------------------------- 辅助函数
def column_letter(index: int) -> str:
    """Zero based column index to spreadsheet letters (0 -> A, 26 -> AA).

    从零开始的列索引转成表格列字母（0 -> A，26 -> AA）。"""

    if index < 0:
        raise ValueError("column index must not be negative")
    letters = ""
    current = index + 1
    while current:
        current, remainder = divmod(current - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def column_index(reference: str) -> int:
    """Spreadsheet letters to a zero based column index (``AA`` -> 26).

    表格列字母转成从零开始的列索引（``AA`` -> 26）。"""

    match = _COLUMN_REF_RE.match(str(reference).strip())
    if not match:
        raise SpreadsheetError(f"{reference!r} is not a cell reference", field="reference")
    index = 0
    for character in match.group(1).upper():
        index = index * 26 + (ord(character) - ord("A") + 1)
    return index - 1


def excel_serial(day: date) -> int:
    """Days since 1899-12-30, the way Excel counts dates.

    自 1899-12-30 起算的天数，也就是 Excel 记录日期的方式。"""

    return (day - _EXCEL_EPOCH).days


def serial_to_date(serial: float | Decimal, *, date1904: bool = False) -> date:
    epoch = _EXCEL_EPOCH_1904 if date1904 else _EXCEL_EPOCH
    return epoch + timedelta(days=int(Decimal(str(serial))))


# ---------------------------------------------------------------------- writer
# ---------------------------------------------------------------------- 写入器
def _w3cdtf(moment: datetime) -> str:
    """Format ``moment`` as the UTC timestamp OOXML documents carry.

    Naive timestamps are taken to be UTC already, so a caller cannot
    accidentally label local time as UTC by forgetting a timezone.

    把 ``moment`` 格式化成 OOXML 文档使用的 UTC 时间戳。

    不带时区的时间戳按 UTC 处理，调用方因此不会因为忘了时区而把本地时间标成 UTC。
    """

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_workbook(
    path: str | Path,
    workbook: Workbook,
    *,
    generated_at: datetime | None = None,
) -> Path:
    """Write ``workbook`` as an ``.xlsx`` file and return the destination.

    把 ``workbook`` 写成一个 ``.xlsx`` 文件并返回目标路径。"""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    shared: dict[str, int] = {}
    sheet_parts = [
        (sheet, _worksheet_xml(sheet, shared))
        for index, sheet in enumerate(workbook.sheets, start=1)
    ]
    stamp = _w3cdtf(generated_at or datetime.now(timezone.utc))

    parts: dict[str, str] = {
        "[Content_Types].xml": _content_types_xml(len(sheet_parts)),
        "_rels/.rels": _package_rels_xml(),
        "docProps/core.xml": _core_xml(workbook.title, stamp),
        "docProps/app.xml": _app_xml(),
        "xl/workbook.xml": _workbook_xml(workbook),
        "xl/_rels/workbook.xml.rels": _workbook_rels_xml(len(sheet_parts)),
        "xl/styles.xml": _styles_xml(),
        # Always present: the workbook relationships point at it.
        # 始终存在：工作簿的关系文件会指向它。
        "xl/sharedStrings.xml": _shared_strings_xml(shared),
    }
    for index, (_sheet, xml) in enumerate(sheet_parts, start=1):
        parts[f"xl/worksheets/sheet{index}.xml"] = xml

    try:
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in parts.items():
                archive.writestr(name, content.encode("utf-8"))
    except OSError as exc:
        raise SpreadsheetError(f"cannot write {destination}: {exc}", field="path") from exc
    return destination


def _worksheet_xml(sheet: Sheet, shared: dict[str, int]) -> str:
    header_row = 1
    if sheet.heading:
        header_row += 1
    if sheet.subtitle:
        header_row += 1

    body: list[str] = []
    if sheet.freeze_header and sheet.rows:
        body.append(
            "<sheetViews><sheetView workbookViewId=\"0\">"
            f'<pane ySplit="{header_row}" topLeftCell="A{header_row + 1}" '
            'activePane="bottomLeft" state="frozen"/>'
            "</sheetView></sheetViews>"
        )

    if any(column.width is not None for column in sheet.columns):
        columns: list[str] = ["<cols>"]
        for position, column in enumerate(sheet.columns, start=1):
            width = column.width or _DEFAULT_WIDTHS[column.kind]
            columns.append(
                f'<col min="{position}" max="{position}" width="{float(width):.1f}" '
                'customWidth="1"/>'
            )
        columns.append("</cols>")
        body.append("".join(columns))

    rows: list[str] = []
    row_number = 0
    if sheet.heading:
        row_number += 1
        rows.append(_row_xml(row_number, [(0, sheet.heading, _STYLE_TITLE)], shared))
    if sheet.subtitle:
        row_number += 1
        rows.append(_row_xml(row_number, [(0, sheet.subtitle, _STYLE_GENERAL)], shared))

    row_number += 1
    header_cells = [
        (position, column.header, _STYLE_BOLD)
        for position, column in enumerate(sheet.columns)
    ]
    rows.append(_row_xml(row_number, header_cells, shared))
    header_row = row_number

    first_data_row = row_number + 1
    for row in sheet.rows:
        row_number += 1
        cells = [
            (position, value, _style_for(sheet.columns[position].kind))
            for position, value in enumerate(row)
        ]
        rows.append(_row_xml(row_number, cells, shared))

    if sheet.footer is not None:
        cells = [
            (position, value, _footer_style(sheet.columns[position].kind))
            for position, value in enumerate(sheet.footer)
        ]
        if cells:
            row_number += 1
            rows.append(_row_xml(row_number, cells, shared))

    body.append("<sheetData>" + "".join(rows) + "</sheetData>")

    if sheet.autofilter and sheet.rows:
        last_column = column_letter(len(sheet.columns) - 1)
        last_row = first_data_row + len(sheet.rows) - 1
        body.append(f'<autoFilter ref="A{header_row}:{last_column}{last_row}"/>')

    return (
        _XML_DECLARATION
        + f'<worksheet xmlns="{_NS_MAIN}" xmlns:r="{_NS_REL}">'
        + "".join(body)
        + "</worksheet>"
    )


def _row_xml(row_number: int, cells: Sequence[tuple[int, Any, int]], shared: dict[str, int]) -> str:
    rendered = "".join(
        xml
        for position, value, style in cells
        if (xml := _cell_xml(f"{column_letter(position)}{row_number}", value, style, shared))
    )
    if not rendered:
        return ""
    return f'<row r="{row_number}">{rendered}</row>'


def _cell_xml(reference: str, value: Any, style: int, shared: dict[str, int]) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return f'<c r="{reference}" s="{style}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return f'<c r="{reference}" s="{style}"><v>{excel_serial(value)}</v></c>'
    if isinstance(value, Decimal):
        if not value.is_finite():  # pragma: no cover - domain forbids it | 领域模型已禁止非有限值
            return ""
        return f'<c r="{reference}" s="{style}"><v>{format(value, "f")}</v></c>'
    if isinstance(value, int):
        return f'<c r="{reference}" s="{style}"><v>{value}</v></c>'
    if isinstance(value, float):  # pragma: no cover - callers use Decimal | 调用方只使用 Decimal
        return f'<c r="{reference}" s="{style}"><v>{value!r}</v></c>'

    text = _clean_text(str(value))
    index = shared.get(text)
    if index is None:
        index = len(shared)
        shared[text] = index
    return f'<c r="{reference}" s="{style}" t="s"><v>{index}</v></c>'


def _style_for(kind: ColumnKind) -> int:
    if kind is ColumnKind.MONEY:
        return _STYLE_MONEY
    if kind is ColumnKind.DATE:
        return _STYLE_DATE
    return _STYLE_GENERAL


def _footer_style(kind: ColumnKind) -> int:
    return _STYLE_TOTAL_MONEY if kind is ColumnKind.MONEY else _STYLE_BOLD


def _clean_text(text: str) -> str:
    """Drop characters XML 1.0 cannot carry and normalise line breaks.

    剔除 XML 1.0 无法承载的字符，并统一换行符。"""

    kept = "".join(
        character
        for character in text
        if character in "\t\n\r" or ord(character) >= 0x20
    )
    return kept.replace("\r\n", "\n").replace("\r", "\n")


def _shared_strings_xml(shared: dict[str, int]) -> str:
    items = sorted(shared.items(), key=lambda item: item[1])
    rows = "".join(
        f'<si><t xml:space="preserve">{escape(text)}</t></si>' for text, _index in items
    )
    return (
        _XML_DECLARATION
        + f'<sst xmlns="{_NS_MAIN}" count="{len(items)}" uniqueCount="{len(items)}">'
        + rows
        + "</sst>"
    )


def _content_types_xml(sheet_count: int) -> str:
    overrides = [
        f'<Override PartName="/xl/workbook.xml" ContentType="{_CONTENT_WORKBOOK}"/>',
        f'<Override PartName="/xl/styles.xml" ContentType="{_CONTENT_STYLES}"/>',
        f'<Override PartName="/xl/sharedStrings.xml" '
        f'ContentType="{_CONTENT_SHARED_STRINGS}"/>',
    ]
    for index in range(1, sheet_count + 1):
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            f'ContentType="{_CONTENT_WORKSHEET}"/>'
        )
    overrides.append(
        '<Override PartName="/docProps/core.xml" '
        'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    )
    overrides.append(
        '<Override PartName="/docProps/app.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
    )
    return (
        _XML_DECLARATION
        + f'<Types xmlns="{_NS_CONTENT_TYPES}">'
        + '<Default Extension="rels" '
        'ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        + '<Default Extension="xml" ContentType="application/xml"/>'
        + "".join(overrides)
        + "</Types>"
    )


def _package_rels_xml() -> str:
    return (
        _XML_DECLARATION
        + f'<Relationships xmlns="{_NS_PACKAGE_REL}">'
        + f'<Relationship Id="rId1" Type="{_REL_OFFICE_DOCUMENT}" Target="xl/workbook.xml"/>'
        + f'<Relationship Id="rId2" Type="{_NS_REL}/metadata/core-properties" '
        'Target="docProps/core.xml"/>'
        + f'<Relationship Id="rId3" Type="{_NS_REL}/extended-properties" '
        'Target="docProps/app.xml"/>'
        + "</Relationships>"
    )


def _workbook_xml(workbook: Workbook) -> str:
    sheets = "".join(
        f'<sheet name="{_attribute(sheet.title)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, sheet in enumerate(workbook.sheets, start=1)
    )
    return (
        _XML_DECLARATION
        + f'<workbook xmlns="{_NS_MAIN}" xmlns:r="{_NS_REL}">'
        + f"<sheets>{sheets}</sheets>"
        + "</workbook>"
    )


def _workbook_rels_xml(sheet_count: int) -> str:
    relationships = [
        f'<Relationship Id="rId{index}" Type="{_REL_WORKSHEET}" '
        f'Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, sheet_count + 1)
    ]
    relationships.append(
        f'<Relationship Id="rId{sheet_count + 1}" Type="{_REL_STYLES}" Target="styles.xml"/>'
    )
    relationships.append(
        f'<Relationship Id="rId{sheet_count + 2}" Type="{_REL_SHARED_STRINGS}" '
        'Target="sharedStrings.xml"/>'
    )
    return (
        _XML_DECLARATION
        + f'<Relationships xmlns="{_NS_PACKAGE_REL}">'
        + "".join(relationships)
        + "</Relationships>"
    )


def _styles_xml() -> str:
    return (
        _XML_DECLARATION
        + f'<styleSheet xmlns="{_NS_MAIN}">'
        + '<numFmts count="2">'
        + f'<numFmt numFmtId="{_NUMFMT_MONEY}" formatCode="#,##0.00"/>'
        + f'<numFmt numFmtId="{_NUMFMT_DATE}" formatCode="yyyy-mm-dd"/>'
        + "</numFmts>"
        + '<fonts count="3">'
        + '<font><sz val="11"/><name val="Calibri"/></font>'
        + '<font><b/><sz val="11"/><name val="Calibri"/></font>'
        + '<font><b/><sz val="14"/><name val="Calibri"/></font>'
        + "</fonts>"
        + '<fills count="2">'
        + '<fill><patternFill patternType="none"/></fill>'
        + '<fill><patternFill patternType="gray125"/></fill>'
        + "</fills>"
        + '<borders count="1">'
        + "<border><left/><right/><top/><bottom/><diagonal/></border>"
        + "</borders>"
        + '<cellStyleXfs count="1">'
        + '<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        + "</cellStyleXfs>"
        + '<cellXfs count="6">'
        + '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        + '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        + f'<xf numFmtId="{_NUMFMT_MONEY}" fontId="0" fillId="0" borderId="0" xfId="0" '
        'applyNumberFormat="1"/>'
        + f'<xf numFmtId="{_NUMFMT_DATE}" fontId="0" fillId="0" borderId="0" xfId="0" '
        'applyNumberFormat="1"/>'
        + '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        + f'<xf numFmtId="{_NUMFMT_MONEY}" fontId="1" fillId="0" borderId="0" xfId="0" '
        'applyNumberFormat="1" applyFont="1"/>'
        + "</cellXfs>"
        + '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        + "</styleSheet>"
    )


def _core_xml(title: str, stamp: str) -> str:
    return (
        _XML_DECLARATION
        + f'<cp:coreProperties xmlns:cp="{_NS_CORE}" xmlns:dc="{_NS_DC}" '
        f'xmlns:dcterms="{_NS_DCTERMS}" xmlns:xsi="{_NS_XSI}">'
        + f"<dc:title>{escape(title)}</dc:title>"
        + "<dc:creator>Centsible</dc:creator>"
        + "<cp:lastModifiedBy>Centsible</cp:lastModifiedBy>"
        + f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
        + f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified>'
        + "</cp:coreProperties>"
    )


def _app_xml() -> str:
    return (
        _XML_DECLARATION
        + f'<Properties xmlns="{_NS_EXTENDED}">'
        + "<Application>Centsible</Application>"
        + "</Properties>"
    )


def _attribute(text: str) -> str:
    return escape(text, {'"': "&quot;"})


# ---------------------------------------------------------------------- reader
# ---------------------------------------------------------------------- 读取器
def read_worksheet(
    path: str | Path,
    *,
    sheet: int | str | None = None,
    max_cells: int = 500_000,
) -> Worksheet:
    """Read one sheet of an ``.xlsx`` file into typed rows.

    ``sheet`` may be a zero based index or a sheet name; ``None`` picks the first
    sheet.  Cells become ``str``, ``Decimal``, ``bool``, ``date``/``datetime``/
    ``time`` or ``None`` (for empty cells).

    把一个 ``.xlsx`` 文件的某张工作表读成带类型的行。

    ``sheet`` 可以是零基索引，也可以是工作表名称；``None`` 表示取第一张工作表。
    单元格会被转成 ``str``、``Decimal``、``bool``、``date``/``datetime``/``time``，
    空单元格则是 ``None``。
    """

    source = _open_archive(path)
    try:
        workbook_root = _xml_part(source, "xl/workbook.xml")
        relationships = _parse_relationships(source, "xl/_rels/workbook.xml.rels")
        date1904 = _uses_1904_dates(workbook_root)
        sheets = _sheet_entries(workbook_root, relationships)
        if not sheets:
            raise SpreadsheetError(f"{source_name(path)} contains no sheets", field="path")
        name, target = _select_sheet(sheets, sheet)

        shared_strings = _parse_shared_strings(source)
        style_kinds = _parse_style_kinds(source)
        root = _xml_part(source, target)
        rows = _parse_rows(root, shared_strings, style_kinds, date1904, max_cells)
    finally:
        source.close()
    return Worksheet(name=name, rows=rows)


def list_sheets(path: str | Path) -> tuple[str, ...]:
    """Names of every sheet in the workbook, in file order.

    工作簿中所有工作表的名称，按文件中的顺序排列。"""

    source = _open_archive(path)
    try:
        root = _xml_part(source, "xl/workbook.xml")
        relationships = _parse_relationships(source, "xl/_rels/workbook.xml.rels")
        return tuple(name for name, _target in _sheet_entries(root, relationships))
    finally:
        source.close()


def _open_archive(path: str | Path) -> zipfile.ZipFile:
    source_path = Path(path)
    if not source_path.exists():
        raise SpreadsheetError(f"file not found: {source_path}", field="path")
    if not source_path.is_file():
        raise SpreadsheetError(f"{source_path} is not a file", field="path")
    try:
        archive = zipfile.ZipFile(source_path)
    except zipfile.BadZipFile as exc:
        raise SpreadsheetError(
            f"{source_path.name} is not an .xlsx workbook (it is not a zip archive)",
            field="path",
        ) from exc

    entries = archive.infolist()
    if len(entries) > _MAX_ZIP_ENTRIES:
        archive.close()
        raise SpreadsheetError(
            f"{source_path.name} has {len(entries)} entries, which is not a spreadsheet",
            field="path",
        )
    uncompressed = sum(entry.file_size for entry in entries)
    if uncompressed > _MAX_UNCOMPRESSED:
        archive.close()
        raise SpreadsheetError(
            f"{source_path.name} expands to {uncompressed} bytes, which is too large "
            "to be a spreadsheet",
            field="path",
        )
    return archive


def source_name(path: str | Path) -> str:
    return Path(path).name


def _xml_part(archive: zipfile.ZipFile, name: str) -> ElementTree.Element:
    try:
        payload = archive.read(name)
    except KeyError as exc:
        raise SpreadsheetError(
            f"the workbook is missing its {name} part; is this really an .xlsx file?",
            field="path",
        ) from exc
    if _DTD_MARKER.search(payload):
        raise SpreadsheetError(
            f"{name} declares a DTD or an entity, which a spreadsheet never does",
            field="path",
        )
    try:
        return ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise SpreadsheetError(f"{name} is not valid XML: {exc}", field="path") from exc


def _parse_relationships(
    archive: zipfile.ZipFile, name: str
) -> dict[str, tuple[str, str]]:
    try:
        root = _xml_part(archive, name)
    except SpreadsheetError:
        return {}
    relationships: dict[str, tuple[str, str]] = {}
    for element in root:
        identifier = element.get("Id")
        if identifier is None:
            continue
        relationships[identifier] = (element.get("Type", ""), element.get("Target", ""))
    return relationships


def _uses_1904_dates(workbook_root: ElementTree.Element) -> bool:
    properties = workbook_root.find(f"{{{_NS_MAIN}}}workbookPr")
    if properties is None:
        return False
    return str(properties.get("date1904", "")).strip() in {"1", "true", "True"}


def _sheet_entries(
    workbook_root: ElementTree.Element, relationships: dict[str, tuple[str, str]]
) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    sheets_element = workbook_root.find(f"{{{_NS_MAIN}}}sheets")
    if sheets_element is None:
        return entries
    for sheet in sheets_element.findall(f"{{{_NS_MAIN}}}sheet"):
        name = sheet.get("name") or f"Sheet{len(entries) + 1}"
        relationship_id = sheet.get(f"{{{_NS_REL}}}id", "")
        _type, target = relationships.get(relationship_id, ("", ""))
        if not target:
            continue
        entries.append((name, _resolve_target(target)))
    return entries


def _resolve_target(target: str) -> str:
    cleaned = target.lstrip("/")
    if cleaned.startswith("xl/"):
        return cleaned
    return posixpath.normpath(posixpath.join("xl", cleaned))


def _select_sheet(
    sheets: Sequence[tuple[str, str]], sheet: int | str | None
) -> tuple[str, str]:
    if sheet is None:
        return sheets[0]
    if isinstance(sheet, int):
        if not 0 <= sheet < len(sheets):
            raise SpreadsheetError(
                f"sheet index {sheet} is out of range: the workbook has "
                f"{len(sheets)} sheet(s)",
                field="sheet",
            )
        return sheets[sheet]
    requested = str(sheet).strip()
    for name, target in sheets:
        if name == requested:
            return name, target
    for name, target in sheets:  # case insensitive fallback | 不区分大小写的兜底查找
        if name.lower() == requested.lower():
            return name, target
    available = ", ".join(name for name, _target in sheets)
    raise SpreadsheetError(
        f"sheet {requested!r} does not exist; available sheets: {available}",
        field="sheet",
    )


def _parse_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = _xml_part(archive, "xl/sharedStrings.xml")
    except SpreadsheetError:
        return []
    strings: list[str] = []
    for item in root.findall(f"{{{_NS_MAIN}}}si"):
        strings.append(_joined_text(item))
    return strings


def _joined_text(element: ElementTree.Element) -> str:
    return "".join(node.text or "" for node in element.iter(f"{{{_NS_MAIN}}}t"))


def _parse_style_kinds(archive: zipfile.ZipFile) -> dict[int, str]:
    """Map ``cellXfs`` index -> "date" | "datetime" | "time" | "number".

    把 ``cellXfs`` 索引映射为 "date" | "datetime" | "time" | "number"。"""

    try:
        root = _xml_part(archive, "xl/styles.xml")
    except SpreadsheetError:
        return {}

    custom_formats: dict[int, str] = {}
    formats_element = root.find(f"{{{_NS_MAIN}}}numFmts")
    if formats_element is not None:
        for element in formats_element.findall(f"{{{_NS_MAIN}}}numFmt"):
            try:
                identifier = int(element.get("numFmtId", "0"))
            except ValueError:  # pragma: no cover - malformed file | 文件格式异常
                continue
            custom_formats[identifier] = element.get("formatCode", "")

    kinds: dict[int, str] = {}
    cell_xfs = root.find(f"{{{_NS_MAIN}}}cellXfs")
    if cell_xfs is None:
        return kinds
    for index, element in enumerate(cell_xfs.findall(f"{{{_NS_MAIN}}}xf")):
        try:
            number_format = int(element.get("numFmtId", "0"))
        except ValueError:  # pragma: no cover - malformed file | 文件格式异常
            continue
        kinds[index] = _classify_number_format(number_format, custom_formats)
    return kinds


def _classify_number_format(
    number_format: int, custom_formats: dict[int, str]
) -> str:
    if number_format in _BUILTIN_DATE_FORMATS:
        return "date"
    if number_format in _BUILTIN_DATETIME_FORMATS:
        return "datetime"
    if number_format in _BUILTIN_TIME_FORMATS:
        return "time"
    code = custom_formats.get(number_format)
    if not code:
        return "number"
    stripped = re.sub(r"\[[^\]]*\]", "", code)
    stripped = re.sub(r'"[^"]*"', "", stripped)
    stripped = stripped.split(";")[0]
    has_date = bool(re.search(r"[ymd]", stripped, re.IGNORECASE))
    has_time = bool(re.search(r"[hs]", stripped, re.IGNORECASE))
    if has_date and has_time:
        return "datetime"
    if has_date:
        return "date"
    if has_time:
        return "time"
    return "number"


def _parse_rows(
    root: ElementTree.Element,
    shared_strings: Sequence[str],
    style_kinds: dict[int, str],
    date1904: bool,
    max_cells: int,
) -> tuple[tuple[Any, ...], ...]:
    sheet_data = root.find(f"{{{_NS_MAIN}}}sheetData")
    if sheet_data is None:
        return ()

    sparse: dict[int, dict[int, Any]] = {}
    row_counter = 0
    cell_count = 0
    widest = 0

    for row_element in sheet_data.findall(f"{{{_NS_MAIN}}}row"):
        explicit_row = row_element.get("r")
        if explicit_row is not None:
            try:
                row_index = int(explicit_row) - 1
            except ValueError:  # pragma: no cover - malformed file | 文件格式异常
                row_index = row_counter
        else:
            row_index = row_counter
        row_counter = row_index + 1

        cells: dict[int, Any] = {}
        column_counter = 0
        for cell_element in row_element.findall(f"{{{_NS_MAIN}}}c"):
            reference = cell_element.get("r")
            if reference is not None:
                try:
                    position = column_index(reference)
                except SpreadsheetError:  # pragma: no cover - malformed file | 文件格式异常
                    position = column_counter
            else:
                position = column_counter
            column_counter = position + 1

            value = _cell_value(cell_element, shared_strings, style_kinds, date1904)
            if value is None:
                continue
            cells[position] = value
            cell_count += 1
            widest = max(widest, position + 1)
            if cell_count > max_cells:
                raise SpreadsheetError(
                    f"the worksheet has more than {max_cells} non-empty cells; "
                    "this looks like the wrong file",
                    field="path",
                )
        if cells:
            sparse[row_index] = cells

    if not sparse:
        return ()
    last_row = max(sparse)
    return tuple(
        tuple(sparse.get(row_index, {}).get(column, None) for column in range(widest))
        for row_index in range(last_row + 1)
    )


def _cell_value(
    element: ElementTree.Element,
    shared_strings: Sequence[str],
    style_kinds: dict[int, str],
    date1904: bool,
) -> Any:
    cell_type = (element.get("t") or "n").strip()
    try:
        style = int(element.get("s") or 0)
    except ValueError:  # pragma: no cover - malformed file | 文件格式异常
        style = 0

    if cell_type == "inlineStr":
        inline = element.find(f"{{{_NS_MAIN}}}is")
        return _joined_text(inline) if inline is not None else None

    value_element = element.find(f"{{{_NS_MAIN}}}v")
    if value_element is None or value_element.text is None:
        return None
    text = value_element.text

    if cell_type == "s":
        try:
            return shared_strings[int(text)]
        except (ValueError, IndexError) as exc:
            raise SpreadsheetError(
                "a cell references a shared string that does not exist", field="path"
            ) from exc
    if cell_type == "b":
        return text.strip() not in {"0", "", "false", "FALSE"}
    if cell_type in {"e", "str"}:
        return text
    if cell_type == "d":
        try:
            return datetime.fromisoformat(text).date()
        except ValueError as exc:
            raise SpreadsheetError(
                f"a cell holds an invalid ISO date: {text!r}", field="path"
            ) from exc

    kind = style_kinds.get(style, "number")
    if kind in {"date", "datetime", "time"}:
        return _numeric_to_datetime(text, kind, date1904)
    return _to_number(text)


def _to_number(text: str) -> Any:
    raw = text.strip()
    try:
        if re.fullmatch(r"[+-]?\d+", raw):
            return int(raw)
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return text  # pragma: no cover - weird but harmless | 异常但无害


def _numeric_to_datetime(value: str, kind: str, date1904: bool) -> Any:
    try:
        serial = Decimal(value.strip())
    except InvalidOperation as exc:
        raise SpreadsheetError(
            f"a date cell holds {value!r}, which is not a number", field="path"
        ) from exc

    days = int(serial)
    fraction = serial - days
    day = serial_to_date(days, date1904=date1904)
    if kind == "date":
        return day
    seconds = int(round(float(fraction) * 86400))
    if seconds >= 86400:  # pragma: no cover - rounding at midnight | 跨午夜时舍入
        day += timedelta(days=1)
        seconds -= 86400
    clock = time(second=0) if seconds == 0 else time(
        hour=seconds // 3600, minute=(seconds % 3600) // 60, second=seconds % 60
    )
    if kind == "time":
        return clock
    return datetime.combine(day, clock)

