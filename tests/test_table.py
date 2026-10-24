"""Tests for the width-aware text tables.

按显示宽度对齐的文本表格的测试。"""

from __future__ import annotations

import unittest

from centsible.interface.cli.table import (
    display_width,
    heading,
    pad,
    render_key_values,
    render_table,
    truncate,
)


class WidthTests(unittest.TestCase):
    def test_ascii_width(self) -> None:
        self.assertEqual(display_width("abc"), 3)
        self.assertEqual(display_width(""), 0)

    def test_cjk_width_doubles(self) -> None:
        self.assertEqual(display_width("现金"), 4)
        self.assertEqual(display_width("a中"), 3)

    def test_combining_marks_add_nothing(self) -> None:
        self.assertEqual(display_width("e\u0301"), 1)

    def test_pad_respects_display_width(self) -> None:
        self.assertEqual(display_width(pad("现金", 6)), 6)
        self.assertEqual(pad("现金", 4, "right"), "现金")
        self.assertEqual(pad("x", 3, "center"), " x ")

    def test_truncate_keeps_width(self) -> None:
        self.assertEqual(truncate("abcdef", 6), "abcdef")
        self.assertEqual(truncate("abcdef", 5), "ab...")
        self.assertEqual(display_width(truncate("现金现金", 5)), 5)
        self.assertEqual(truncate("abc", 0), "")


class TableTests(unittest.TestCase):
    def test_every_line_has_the_same_width(self) -> None:
        text = render_table(
            ("code", "account", "amount"),
            [("1000", "现金", "1,200.00"), ("4000", "Sales", "300.00")],
            aligns=("left", "left", "right"),
            footer=("", "total", "1,500.00"),
        )
        widths = {display_width(line) for line in text.splitlines()}
        self.assertEqual(len(widths), 1, text)

    def test_alignment(self) -> None:
        text = render_table(("a", "b"), [("x", "1")], aligns=("left", "right"))
        lines = text.splitlines()
        self.assertTrue(lines[-1].endswith("1"))
        self.assertTrue(lines[0].startswith("  a"))

    def test_separators_can_be_disabled(self) -> None:
        with_separators = render_table(("a",), [("1",)])
        without = render_table(("a",), [("1",)], separators=False)
        self.assertEqual(len(with_separators.splitlines()), 3)
        self.assertEqual(len(without.splitlines()), 2)

    def test_empty_table(self) -> None:
        self.assertEqual(render_table((), []), "")

    def test_none_and_newlines_are_sanitised(self) -> None:
        text = render_table(("a", "b"), [(None, "two\nlines")])
        self.assertIn("two lines", text)
        self.assertEqual(len(text.splitlines()), 3)

    def test_key_values_alignment(self) -> None:
        text = render_key_values([("currency", "CNY"), ("fiscal year starts", 1)])
        lines = text.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("  currency"))
        self.assertIn("CNY", lines[0])

    def test_heading_underlines_by_display_width(self) -> None:
        text = heading("现金")
        top, underline = text.splitlines()
        self.assertEqual(display_width(underline), display_width(top))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
