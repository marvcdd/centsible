"""Tests for the statement readers (shared pipeline, CSV and XLSX specifics).

银行对账单读取器的测试（共享流水线，以及 CSV 与 XLSX 各自的细节）。"""

from __future__ import annotations

import tempfile
import unittest
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from centsible.application.ports import StatementMapping
from centsible.application.workbook import Column, ColumnKind, Sheet, Workbook
from centsible.errors import NotFoundError, ValidationError
from centsible.infrastructure.csvio import write_csv
from centsible.infrastructure.statements import (
    CsvStatementReader,
    XlsxStatementReader,
    statement_reader_for,
)
from centsible.infrastructure.xlsx import write_workbook

HEADER = ("date", "amount", "description", "reference")


class ReaderTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.directory = Path(self._tempdir.name)

    def csv_path(self, rows, header=HEADER) -> Path:
        path = self.directory / "statement.csv"
        write_csv(path, header, rows)
        return path

    def xlsx_path(self, rows, header=HEADER, sheet_name: str = "流水") -> Path:
        kinds = {
            "date": ColumnKind.DATE,
            "amount": ColumnKind.MONEY,
            "description": ColumnKind.TEXT,
            "reference": ColumnKind.TEXT,
            "debit": ColumnKind.MONEY,
            "credit": ColumnKind.MONEY,
        }
        columns = tuple(
            Column(name, kinds.get(name, ColumnKind.TEXT), width=14 if name == "amount" else None)
            for name in header
        )
        typed_rows = tuple(
            tuple(_typed(name, value) for name, value in zip(header, row)) for row in rows
        )
        workbook = Workbook(
            title="statement",
            sheets=(Sheet(title=sheet_name, columns=columns, rows=typed_rows),),
        )
        path = self.directory / "statement.xlsx"
        write_workbook(path, workbook)
        return path

    @staticmethod
    def mapping(**changes: Any) -> StatementMapping:
        payload: dict[str, Any] = {
            "date_column": "date",
            "amount_column": "amount",
            "description_column": "description",
            "id_column": "reference",
        }
        payload.update(changes)
        return StatementMapping(**payload)

    def rows(self, path: Path, mapping: StatementMapping | None = None):
        effective = mapping if mapping is not None else self.mapping()
        return statement_reader_for(path).read(path, effective)


def _typed(name: str, value):
    if name in {"amount", "debit", "credit"} and isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return value
    if name == "date" and isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value
    return value


ROWS = (
    ("2025-03-01", "-1299.00", "Cloud hosting", "INV-1001"),
    ("2025-03-02", "-45.50", "Coffee beans", "INV-1002"),
    ("2025-03-03", "2500.00", "Client payment", "PMT-88"),
)


class CsvReaderTests(ReaderTestCase):
    def test_parse_by_header_names(self) -> None:
        parsed, issues = self.rows(self.csv_path(ROWS))
        self.assertEqual(issues, [])
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[0].amount, Decimal("-1299.00"))
        self.assertEqual(parsed[0].external_id, "INV-1001")
        self.assertEqual(parsed[2].description, "Client payment")

    def test_missing_file(self) -> None:
        with self.assertRaises(NotFoundError):
            self.rows(self.directory / "nope.csv")

    def test_bad_encoding(self) -> None:
        path = self.directory / "latin.csv"
        path.write_text("date,amount,description\n2025-03-01,-1,café\n", encoding="latin-1")
        with self.assertRaises(ValidationError) as caught:
            self.rows(path)
        self.assertIn("cannot be decoded", str(caught.exception))

    def test_empty_file(self) -> None:
        path = self.directory / "empty.csv"
        path.write_text("", encoding="utf-8")
        with self.assertRaises(ValidationError):
            self.rows(path)

    def test_repeated_lines_are_numbered_not_merged(self) -> None:
        rows = (
            ("2025-03-02", "-4.50", "Coffee", ""),
            ("2025-03-02", "-4.50", "Coffee", ""),
            ("2025-03-02", "-4.50", "Coffee", ""),
        )
        parsed, issues = self.rows(self.csv_path(rows))
        self.assertEqual(issues, [])
        self.assertEqual([row.occurrence for row in parsed], [1, 2, 3])
        self.assertEqual(parsed[0].external_id, "2025-03-02|-4.50|Coffee")
        self.assertEqual(
            [row.external_id for row in parsed], ["2025-03-02|-4.50|Coffee"] * 3
        )


class XlsxReaderTests(ReaderTestCase):
    def test_parse_typed_cells(self) -> None:
        parsed, issues = self.rows(self.xlsx_path(ROWS))
        self.assertEqual(issues, [])
        self.assertEqual(len(parsed), 3)
        self.assertEqual(parsed[0].date, date(2025, 3, 1))
        self.assertEqual(parsed[0].amount, Decimal("-1299.00"))
        self.assertEqual(parsed[0].external_id, "INV-1001")

    def test_sheet_selection_by_name_and_index(self) -> None:
        path = self.xlsx_path(ROWS)
        parsed, _ = self.rows(path, self.mapping(sheet="流水"))
        self.assertEqual(len(parsed), 3)
        parsed, _ = self.rows(path, self.mapping(sheet="0"))
        self.assertEqual(len(parsed), 3)

    def test_missing_sheet_is_a_clear_error(self) -> None:
        path = self.xlsx_path(ROWS)
        with self.assertRaises(ValidationError) as caught:
            self.rows(path, self.mapping(sheet="不存在的表"))
        self.assertIn("available sheets", str(caught.exception))

    def test_text_dates_fail_that_row_only(self) -> None:
        path = self.xlsx_path(
            (
                ("not-a-date", "-1.00", "Broken", "A"),
                ("2025-03-02", "-2.00", "Fine", "B"),
            )
        )
        parsed, issues = self.rows(path)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(issues[0].line_number, 2)
        self.assertIn("date", issues[0].message)

    def test_split_debit_and_credit_columns(self) -> None:
        # The debit column is money OUT, the credit column money IN (positive in both).
        # 借方列表示资金流出，贷方列表示资金流入（两列都填正数）。
        path = self.xlsx_path(
            (
                ("2025-03-01", "100.00", "", "Out"),
                ("2025-03-02", "", "300.00", "In"),
            ),
            header=("date", "debit", "credit", "description"),
        )
        mapping = self.mapping(
            amount_column=None,
            debit_column="debit",
            credit_column="credit",
            id_column=None,
        )
        parsed, issues = self.rows(path, mapping)
        self.assertEqual(issues, [])
        self.assertEqual([row.amount for row in parsed], [Decimal("-100.00"), Decimal("300.00")])

    def test_amount_sign_flip(self) -> None:
        parsed, _ = self.rows(
            self.xlsx_path((("2025-03-01", "100.00", "Sign flip", "A"),)),
            self.mapping(amount_sign=-1),
        )
        self.assertEqual(parsed[0].amount, Decimal("-100.00"))

    def test_blank_mapping_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            StatementMapping(
                date_column="date",
                amount_column="amount",
                description_column="description",
                sheet="   ",
            )


class DispatchTests(ReaderTestCase):
    def test_suffix_dispatch(self) -> None:
        self.assertIsInstance(statement_reader_for(Path("a.csv")), CsvStatementReader)
        self.assertIsInstance(statement_reader_for(Path("a.TSV")), CsvStatementReader)
        self.assertIsInstance(statement_reader_for(Path("a.xlsx")), XlsxStatementReader)
        self.assertIsInstance(statement_reader_for(Path("a.xlsm")), XlsxStatementReader)

    def test_unknown_suffix_is_rejected(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            statement_reader_for(Path("a.pdf"))
        self.assertIn("unsupported statement file type", str(caught.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
