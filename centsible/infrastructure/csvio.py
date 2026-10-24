"""Plain delimited-file helpers.

The statement *readers* (CSV and XLSX) live in
:mod:`centsible.infrastructure.statements`; this module keeps the raw file
utilities - writing a CSV export and reading a delimited file row by row - that
the export command and the readers share.

简单的分隔符文件辅助工具。

对账单*读取器*（CSV 与 XLSX）位于 :mod:`centsible.infrastructure.statements`；
本模块保留的是原始文件工具——写出 CSV 导出、逐行读取分隔符文件——供导出命令
与各读取器共用。
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any, Iterable, Sequence

from centsible.errors import NotFoundError, ValidationError

__all__ = ["read_csv_rows", "write_csv"]


def write_csv(
    path: str | Path,
    headers: Sequence[str],
    rows: Iterable[Sequence[Any]],
    *,
    encoding: str = "utf-8-sig",
) -> int:
    """Write a CSV file and return the number of data rows written.

    The default encoding carries a BOM so that Excel opens non-ASCII text
    correctly; the files remain valid UTF-8 for every other tool.

    写出一个 CSV 文件，并返回写入的数据行数。

    默认编码带 BOM，以便 Excel 正确打开非 ASCII 文本；对其他所有工具而言，
    这些文件仍然是合法的 UTF-8。
    """

    destination = Path(path)
    written = 0
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding=encoding, newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(list(headers))
            for row in rows:
                writer.writerow(["" if value is None else value for value in row])
                written += 1
    except OSError as exc:
        raise ValidationError(
            f"cannot write {destination}: {exc}", field="path"
        ) from exc
    return written


def read_csv_rows(
    path: str | Path, *, delimiter: str = ",", encoding: str = "utf-8-sig"
) -> list[list[str]]:
    """Read a delimited file into raw rows (used by tooling and tests).

    把分隔符文件读成原始行（供工具与测试使用）。"""

    source = Path(path)
    if not source.exists():
        raise NotFoundError(f"file not found: {source}")
    text = source.read_text(encoding=encoding)
    return list(csv.reader(io.StringIO(text), delimiter=delimiter))
