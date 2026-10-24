"""Tests for calendar periods and fiscal years.

日历区间与会计年度的测试。"""

from __future__ import annotations

import unittest
from datetime import date

from centsible.domain.periods import (
    DatePeriod,
    fiscal_year_period,
    fiscal_year_start,
    iter_months,
    month_bounds,
    month_key,
    parse_date,
    parse_month_key,
    parse_period,
    shift_months,
)
from centsible.errors import ValidationError


class DatePeriodTests(unittest.TestCase):
    def test_month_bounds_handle_leap_years(self) -> None:
        period = DatePeriod.for_month(2024, 2)
        self.assertEqual(period.start, date(2024, 2, 1))
        self.assertEqual(period.end, date(2024, 2, 29))
        self.assertEqual(DatePeriod.for_month(2025, 2).end, date(2025, 2, 28))

    def test_quarter(self) -> None:
        period = DatePeriod.for_quarter(2025, 2)
        self.assertEqual(period.start, date(2025, 4, 1))
        self.assertEqual(period.end, date(2025, 6, 30))

    def test_year(self) -> None:
        period = DatePeriod.for_year(2025)
        self.assertEqual(period.start, date(2025, 1, 1))
        self.assertEqual(period.end, date(2025, 12, 31))

    def test_invalid_month_and_quarter(self) -> None:
        with self.assertRaises(ValidationError):
            DatePeriod.for_month(2025, 13)
        with self.assertRaises(ValidationError):
            DatePeriod.for_quarter(2025, 5)

    def test_start_after_end_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            DatePeriod(date(2025, 2, 1), date(2025, 1, 1))

    def test_labels(self) -> None:
        self.assertEqual(DatePeriod.for_month(2025, 3).label, "2025-03")
        self.assertEqual(DatePeriod.for_year(2025).label, "2025")
        self.assertEqual(DatePeriod.all_time().label, "all time")
        self.assertEqual(DatePeriod(date(2025, 3, 1), date(2025, 3, 5)).label, "2025-03-01..2025-03-05")
        self.assertEqual(DatePeriod(date(2025, 3, 5), date(2025, 3, 5)).label, "2025-03-05")

    def test_predicates(self) -> None:
        period = DatePeriod.for_month(2025, 3)
        self.assertTrue(period.contains(date(2025, 3, 15)))
        self.assertFalse(period.contains(date(2025, 4, 1)))
        self.assertTrue(period.overlaps(DatePeriod(date(2025, 3, 31), date(2025, 4, 5))))
        self.assertFalse(period.overlaps(DatePeriod(date(2025, 4, 1), date(2025, 4, 5))))
        self.assertFalse(period.is_all_time)
        self.assertEqual(period.days, 31)

    def test_to_dict_uses_none_for_open_ends(self) -> None:
        self.assertEqual(
            DatePeriod.all_time().to_dict(),
            {"start": None, "end": None, "label": "all time"},
        )


class MonthArithmeticTests(unittest.TestCase):
    def test_month_key(self) -> None:
        self.assertEqual(month_key(date(2025, 3, 17)), "2025-03")

    def test_parse_month_key(self) -> None:
        self.assertEqual(parse_month_key("2025-03"), (2025, 3))
        for bad in ("2025-13", "2025-00", "2025/03", "25-03", "", "abc"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                parse_month_key(bad)

    def test_month_bounds(self) -> None:
        period = month_bounds("2025-02")
        self.assertEqual(period.start, date(2025, 2, 1))
        self.assertEqual(period.end, date(2025, 2, 28))

    def test_shift_months_clamps_to_month_end(self) -> None:
        self.assertEqual(shift_months(date(2025, 1, 31), 1), date(2025, 2, 28))
        self.assertEqual(shift_months(date(2025, 1, 31), 2), date(2025, 3, 31))
        self.assertEqual(shift_months(date(2025, 3, 31), -1), date(2025, 2, 28))

    def test_shift_months_crosses_years(self) -> None:
        self.assertEqual(shift_months(date(2025, 11, 15), 3), date(2026, 2, 15))
        self.assertEqual(shift_months(date(2025, 1, 15), -13), date(2023, 12, 15))

    def test_iter_months_is_inclusive(self) -> None:
        self.assertEqual(
            list(iter_months(date(2025, 11, 5), date(2026, 2, 1))),
            ["2025-11", "2025-12", "2026-01", "2026-02"],
        )
        self.assertEqual(list(iter_months(date(2025, 3, 1), date(2025, 3, 31))), ["2025-03"])
        self.assertEqual(list(iter_months(date(2025, 4, 1), date(2025, 3, 1))), [])


class FiscalYearTests(unittest.TestCase):
    def test_calendar_year(self) -> None:
        self.assertEqual(fiscal_year_start(date(2025, 6, 1)), date(2025, 1, 1))

    def test_april_start(self) -> None:
        self.assertEqual(fiscal_year_start(date(2025, 2, 10), 4), date(2024, 4, 1))
        self.assertEqual(fiscal_year_start(date(2025, 4, 1), 4), date(2025, 4, 1))
        period = fiscal_year_period(date(2025, 2, 10), 4)
        self.assertEqual(period.start, date(2024, 4, 1))
        self.assertEqual(period.end, date(2025, 3, 31))

    def test_invalid_start_month(self) -> None:
        with self.assertRaises(ValidationError):
            fiscal_year_start(date(2025, 1, 1), 13)


class ParseDateTests(unittest.TestCase):
    def test_iso(self) -> None:
        self.assertEqual(parse_date("2025-03-17"), date(2025, 3, 17))

    def test_custom_format(self) -> None:
        self.assertEqual(parse_date("17/03/2025", date_format="%d/%m/%Y"), date(2025, 3, 17))

    def test_month_expands_to_first_day(self) -> None:
        self.assertEqual(parse_date("2025-03"), date(2025, 3, 1))

    def test_invalid(self) -> None:
        for text in ("", "not-a-date", "2025-02-30"):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_date(text)


class ParsePeriodTests(unittest.TestCase):
    def test_nothing_means_no_period(self) -> None:
        self.assertIsNone(parse_period(None, None))
        self.assertIsNone(parse_period("", "  "))

    def test_month_strings_expand(self) -> None:
        period = parse_period("2025-03", "2025-04")
        assert period is not None
        self.assertEqual(period.start, date(2025, 3, 1))
        self.assertEqual(period.end, date(2025, 4, 30))

    def test_open_ended(self) -> None:
        start_only = parse_period("2025-03", None)
        assert start_only is not None
        self.assertEqual(start_only.start, date(2025, 3, 1))
        self.assertEqual(start_only.end, date.max)

        end_only = parse_period(None, "2025-03")
        assert end_only is not None
        self.assertEqual(end_only.start, date.min)
        self.assertEqual(end_only.end, date(2025, 3, 31))

    def test_quarter_expands(self) -> None:
        period = parse_period("2025-Q2", "2025-Q2")
        assert period is not None
        self.assertEqual(period.start, date(2025, 4, 1))
        self.assertEqual(period.end, date(2025, 6, 30))

    def test_reversed_range_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            parse_period("2025-04", "2025-03")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
