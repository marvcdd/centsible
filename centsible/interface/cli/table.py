"""Text tables.

Alphabetic terminals and CJK terminals disagree about what "one character"
means, so column widths are computed from the *display* width of the text
(``unicodedata.east_asian_width``).  Without that, a table containing Chinese
account names drifts apart - with it, alignment holds for mixed scripts.

文本表格。

西文终端与中日韩终端对“一个字符”的理解并不一致，因此列宽按文本的*显示*
宽度计算（``unicodedata.east_asian_width``）。不按显示宽度算，含中文科目名的
表格就会错位；按它来算，混排文本也能保持对齐。
"""

from __future__ import annotations

import unicodedata
from typing import Sequence

__all__ = [
    "Align",
    "display_width",
    "heading",
    "pad",
    "render_key_values",
    "render_table",
    "truncate",
]

Align = str  # "left" | "right" | "center" | 对齐方式：left、right、center

_WIDE = frozenset({"W", "F"})


def display_width(text: str) -> int:
    """Number of terminal cells ``text`` occupies.

    ``text`` 占用的终端单元格数。"""

    width = 0
    for char in text:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in _WIDE else 1
    return width


def truncate(text: str, width: int, *, marker: str = "...") -> str:
    """Shorten ``text`` to at most ``width`` display cells.

    把 ``text`` 截短到最多 ``width`` 个显示单元格。"""

    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    budget = width - display_width(marker)
    if budget <= 0:
        return marker[:width]
    kept: list[str] = []
    used = 0
    for char in text:
        char_width = 2 if unicodedata.east_asian_width(char) in _WIDE else 1
        if used + char_width > budget:
            break
        kept.append(char)
        used += char_width
    return "".join(kept) + marker


def pad(text: str, width: int, align: Align = "left") -> str:
    """Pad ``text`` with spaces to ``width`` display cells.

    用空格把 ``text`` 补齐到 ``width`` 个显示单元格。"""

    missing = max(0, width - display_width(text))
    if align == "right":
        return " " * missing + text
    if align == "center":
        left = missing // 2
        return " " * left + text + " " * (missing - left)
    return text + " " * missing


def render_table(
    headers: Sequence[str],
    rows: Sequence[Sequence[object]],
    *,
    aligns: Sequence[Align] | None = None,
    footer: Sequence[object] | None = None,
    indent: str = "  ",
    separators: bool = True,
) -> str:
    """Render a fixed-width table with an optional footer row.

    渲染一张固定列宽的表格，可选附加页脚行。"""

    column_count = len(headers)
    if column_count == 0:
        return ""

    cells: list[tuple[str, ...]] = [
        tuple(_cell_text(value) for value in row) for row in rows
    ]
    alignments: Sequence[Align] = (
        ["left"] * column_count if aligns is None else aligns
    )
    if len(alignments) != column_count:
        raise ValueError("aligns must have one entry per column")

    widths = [display_width(header) for header in headers]
    for row in cells:
        for index in range(min(column_count, len(row))):
            widths[index] = max(widths[index], display_width(row[index]))
    if footer is not None:
        for index in range(min(column_count, len(footer))):
            widths[index] = max(widths[index], display_width(_cell_text(footer[index])))

    def render_row(values: Sequence[str]) -> str:
        return indent + "  ".join(
            pad(values[index] if index < len(values) else "", widths[index], alignments[index])
            for index in range(column_count)
        ).rstrip()

    rule = indent + "  ".join("-" * width for width in widths)
    lines = [render_row([str(header) for header in headers])]
    if separators:
        lines.append(rule)
    lines.extend(render_row(row) for row in cells)
    if footer is not None:
        if separators:
            lines.append(rule)
        lines.append(render_row([_cell_text(value) for value in footer]))
    return "\n".join(lines)


def render_key_values(
    pairs: Sequence[tuple[str, object]], *, indent: str = "  ", separator: str = "  "
) -> str:
    """Render ``label  value`` lines with the labels aligned.

    渲染 ``label  value`` 形式的行，并让标签对齐。"""

    if not pairs:
        return ""
    labels = [label for label, _ in pairs]
    width = max(display_width(label) for label in labels)
    return "\n".join(
        f"{indent}{pad(label, width)} {separator}{_cell_text(value)}" for label, value in pairs
    )


def heading(text: str, *, char: str = "=", indent: str = "") -> str:
    """A title underlined with ``char``.

    用 ``char`` 加下划线的标题。"""

    return f"{indent}{text}\n{indent}{char * display_width(text)}"


def _cell_text(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("\n", " ").replace("\r", " ").replace("\t", " ")

