"""Tests for turning command line strings into domain values.

把命令行字符串转换为领域值的测试。"""

from __future__ import annotations

import argparse
import unittest
from datetime import date
from decimal import Decimal

from centsible.domain.periods import DatePeriod
from centsible.errors import ValidationError
from centsible.interface.cli.parsing import (
    parse_date_arg,
    parse_posting_specs,
    parse_tags_arg,
    resolve_period,
)


def args(**kwargs) -> argparse.Namespace:
    payload = {"month": None, "from_date": None, "to_date": None}
    payload.update(kwargs)
    return argparse.Namespace(**payload)


class PostingSpecTests(unittest.TestCase):
    def test_two_explicit_postings(self) -> None:
        postings = parse_posting_specs(
            postings=["5100=1200", "1010=-1200"], exponent=2
        )
        self.assertEqual([posting.account_code for posting in postings], ["5100", "1010"])
        self.assertEqual([posting.amount for posting in postings],
                         [Decimal("1200.00"), Decimal("-1200.00")])

    def test_commas_and_parentheses_are_understood(self) -> None:
        postings = parse_posting_specs(postings=["1010=1,200", "4000=(1,200)"], exponent=2)
        self.assertEqual(postings[1].amount, Decimal("-1200.00"))

    def test_debit_and_credit_flags_force_the_direction(self) -> None:
        postings = parse_posting_specs(
            debits=["5100=-100"], credits=["1010=100"], exponent=2
        )
        self.assertEqual(postings[0].amount, Decimal("100.00"))
        self.assertEqual(postings[1].amount, Decimal("-100.00"))

    def test_one_missing_amount_is_filled_in(self) -> None:
        postings = parse_posting_specs(
            postings=["1010=4520", "4000=-4000", "2200="], exponent=2
        )
        self.assertEqual(postings[2].amount, Decimal("-520.00"))
        self.assertEqual(sum(posting.amount for posting in postings), Decimal("0"))

    def test_more_than_one_missing_amount_is_ambiguous(self) -> None:
        with self.assertRaises(ValidationError):
            parse_posting_specs(postings=["1010=", "4000="], exponent=2)

    def test_unbalanced_input_is_refused(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            parse_posting_specs(postings=["5100=100", "1010=-90"], exponent=2)
        self.assertIn("do not balance", str(caught.exception))

    def test_implied_zero_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            parse_posting_specs(
                postings=["5100=100", "1010=-100", "4000="], exponent=2
            )

    def test_memo_after_a_colon(self) -> None:
        postings = parse_posting_specs(postings=["5100=100:lunch", "1010=-100"], exponent=2)
        self.assertEqual(postings[0].memo, "lunch")

    def test_spec_without_equals_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            parse_posting_specs(postings=["5100"], exponent=2)

    def test_bad_amount(self) -> None:
        with self.assertRaises(ValidationError):
            parse_posting_specs(postings=["5100=abc", "1010=-100"], exponent=2)

    def test_too_many_decimals(self) -> None:
        with self.assertRaises(ValidationError):
            parse_posting_specs(postings=["5100=1.001", "1010=-1.001"], exponent=2)

    def test_too_few_postings(self) -> None:
        with self.assertRaises(ValidationError):
            parse_posting_specs(postings=["5100=100"], exponent=2)

    def test_no_postings(self) -> None:
        with self.assertRaises(ValidationError):
            parse_posting_specs(exponent=2)

    def test_account_codes_are_canonicalised(self) -> None:
        postings = parse_posting_specs(postings=["cash=1", "equity=-1"], exponent=2)
        self.assertEqual([posting.account_code for posting in postings], ["CASH", "EQUITY"])


class TagTests(unittest.TestCase):
    def test_tags_are_split_and_sorted(self) -> None:
        self.assertEqual(parse_tags_arg(["Rent, #fixed"]), ("fixed", "rent"))

    def test_no_tags(self) -> None:
        self.assertEqual(parse_tags_arg(None), ())


class PeriodArgumentTests(unittest.TestCase):
    def test_nothing_means_default(self) -> None:
        self.assertIsNone(resolve_period(args()))
        default = DatePeriod.for_month(2025, 3)
        self.assertEqual(resolve_period(args(), default=default), default)

    def test_month(self) -> None:
        self.assertEqual(resolve_period(args(month="2025-03")), DatePeriod.for_month(2025, 3))

    def test_from_and_to(self) -> None:
        period = resolve_period(args(from_date="2025-03-01", to_date="2025-03-31"))
        self.assertEqual(period, DatePeriod.for_month(2025, 3))

    def test_month_and_range_together_are_refused(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            resolve_period(args(month="2025-03", from_date="2025-03-01"))
        self.assertIn("not both", str(caught.exception))

    def test_custom_date_format(self) -> None:
        period = resolve_period(
            args(from_date="01/03/2025", to_date="31/03/2025"), date_format="%d/%m/%Y"
        )
        self.assertEqual(period, DatePeriod.for_month(2025, 3))


class ScalarArgumentTests(unittest.TestCase):
    def test_parse_date(self) -> None:
        self.assertEqual(parse_date_arg("2025-03-17"), date(2025, 3, 17))

    def test_missing_date(self) -> None:
        with self.assertRaises(ValidationError):
            parse_date_arg(None)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
