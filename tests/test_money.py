"""Tests for exact money handling.

精确金额处理的测试。"""

from __future__ import annotations

import unittest
from decimal import Decimal

from centsible.domain.money import (
    Money,
    coerce_decimal,
    currency_exponent,
    format_amount,
    from_minor,
    normalize_currency,
    parse_amount,
    parse_decimal,
    quantize,
    sum_decimals,
    to_minor,
)
from centsible.errors import CurrencyMismatchError, ValidationError


class CurrencyTests(unittest.TestCase):
    def test_normalize_currency_upper_cases(self) -> None:
        self.assertEqual(normalize_currency(" cny "), "CNY")

    def test_normalize_currency_rejects_garbage(self) -> None:
        with self.assertRaises(ValidationError):
            normalize_currency("RMB¥")
        with self.assertRaises(ValidationError):
            normalize_currency("EU")
        with self.assertRaises(ValidationError):
            normalize_currency(42)  # type: ignore[arg-type]

    def test_currency_exponent_knows_zero_decimal_currencies(self) -> None:
        self.assertEqual(currency_exponent("JPY"), 0)
        self.assertEqual(currency_exponent("CNY"), 2)
        self.assertEqual(currency_exponent("BTC"), 8)

    def test_unknown_but_valid_code_defaults_to_two(self) -> None:
        self.assertEqual(currency_exponent("XYZ"), 2)


class ParsingTests(unittest.TestCase):
    def test_plain_amount(self) -> None:
        self.assertEqual(parse_decimal("12.34"), Decimal("12.34"))

    def test_thousands_separators_and_spaces(self) -> None:
        self.assertEqual(parse_decimal(" 1,234,567.89 "), Decimal("1234567.89"))

    def test_negative_and_explicit_plus(self) -> None:
        self.assertEqual(parse_decimal("-5"), Decimal("-5"))
        self.assertEqual(parse_decimal("+5"), Decimal("5"))

    def test_parentheses_mean_negative(self) -> None:
        self.assertEqual(parse_decimal("(7.25)"), Decimal("-7.25"))
        self.assertEqual(parse_decimal("(7.25)"), parse_decimal("-7.25"))

    def test_rejects_words(self) -> None:
        for text in ("", "abc", "1.2.3", "1e5", "12,3x"):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_decimal(text)

    def test_parse_amount_rejects_extra_decimals(self) -> None:
        self.assertEqual(parse_amount("1.20", exponent=2), Decimal("1.20"))
        with self.assertRaises(ValidationError):
            parse_amount("1.234", exponent=2)

    def test_parse_amount_honours_zero_decimal_currency(self) -> None:
        self.assertEqual(parse_amount("100", exponent=0), Decimal("100"))
        with self.assertRaises(ValidationError):
            parse_amount("100.5", exponent=0)

    def test_float_is_refused_because_it_cannot_be_exact(self) -> None:
        with self.assertRaises(ValidationError):
            coerce_decimal(0.1)  # type: ignore[arg-type]


class QuantizeTests(unittest.TestCase):
    def test_half_up_rounding(self) -> None:
        self.assertEqual(quantize(Decimal("0.005"), 2), Decimal("0.01"))
        self.assertEqual(quantize(Decimal("0.004"), 2), Decimal("0.00"))
        self.assertEqual(quantize(Decimal("2.675"), 2), Decimal("2.68"))

    def test_negative_zero_is_normalised(self) -> None:
        zero = quantize(Decimal("-0.001"), 2)
        self.assertEqual(zero, Decimal("0.00"))
        self.assertEqual(str(zero), "0.00")

    def test_minor_unit_conversion_round_trips(self) -> None:
        for text, exponent in (("12.34", 2), ("-7.05", 2), ("100", 0), ("0.00000001", 8)):
            with self.subTest(text=text):
                amount = Decimal(text)
                self.assertEqual(from_minor(to_minor(amount, exponent), exponent), amount)

    def test_format_amount_groups_and_keeps_sign(self) -> None:
        self.assertEqual(format_amount(Decimal("1234567.5"), exponent=2), "1,234,567.50")
        self.assertEqual(format_amount(Decimal("-12"), exponent=2), "-12.00")
        self.assertEqual(format_amount(Decimal("100"), exponent=0), "100")
        self.assertEqual(
            format_amount(Decimal("1234.5"), exponent=2, thousands=False), "1234.50"
        )

    def test_sum_decimals_is_exact(self) -> None:
        self.assertEqual(
            sum_decimals([Decimal("0.1"), Decimal("0.2")]), Decimal("0.3")
        )


class MoneyTests(unittest.TestCase):
    def test_construction_quantises(self) -> None:
        self.assertEqual(Money(Decimal("1.005")).amount, Decimal("1.01"))

    def test_arithmetic(self) -> None:
        a = Money(Decimal("10.00"))
        b = Money(Decimal("2.50"))
        self.assertEqual((a + b).amount, Decimal("12.50"))
        self.assertEqual((a - b).amount, Decimal("7.50"))
        self.assertEqual((-a).amount, Decimal("-10.00"))
        self.assertEqual(abs(Money(Decimal("-3.00"))).amount, Decimal("3.00"))
        self.assertEqual((a * 3).amount, Decimal("30.00"))
        self.assertEqual((a / 4).amount, Decimal("2.50"))

    def test_mixing_currencies_is_refused(self) -> None:
        with self.assertRaises(CurrencyMismatchError):
            Money(Decimal("1.00"), "CNY") + Money(Decimal("1.00"), "USD")
        with self.assertRaises(CurrencyMismatchError):
            Money(Decimal("1.00"), "CNY") - Money(Decimal("1.00"), "USD")

    def test_comparison(self) -> None:
        self.assertLess(Money(Decimal("1.00")), Money(Decimal("2.00")))
        self.assertEqual(Money(Decimal("1.00")), Money(Decimal("1")))
        with self.assertRaises(TypeError):
            _comparison = Money(Decimal("1.00")) < 5  # type: ignore[operator]

    def test_sum_of_empty_is_zero(self) -> None:
        self.assertTrue(Money.sum([]).is_zero)
        self.assertEqual(Money.sum([]).currency, "CNY")

    def test_sum_requires_one_currency(self) -> None:
        total = Money.sum([Money(Decimal("1.00")), Money(Decimal("2.00"))])
        self.assertEqual(total.amount, Decimal("3.00"))
        with self.assertRaises(CurrencyMismatchError):
            Money.sum([Money(Decimal("1.00"), "CNY"), Money(Decimal("1.00"), "USD")])

    def test_factories(self) -> None:
        self.assertEqual(Money.zero().amount, Decimal("0.00"))
        self.assertEqual(Money.from_minor(1234).amount, Decimal("12.34"))
        self.assertEqual(Money.parse("1,234.56").amount, Decimal("1234.56"))
        self.assertEqual(Money.of(3).amount, Decimal("3.00"))
        self.assertEqual(Money(Decimal("12.34")).minor_units, 1234)

    def test_sign_helpers(self) -> None:
        self.assertTrue(Money(Decimal("-1")).is_negative)
        self.assertTrue(Money(Decimal("1")).is_positive)
        self.assertTrue(Money(Decimal("0")).is_zero)
        self.assertEqual(Money(Decimal("-1")).sign, -1)

    def test_format(self) -> None:
        self.assertEqual(Money(Decimal("1234.5")).format(), "1,234.50")
        self.assertEqual(
            Money(Decimal("1234.5")).format(with_currency=True), "1,234.50 CNY"
        )
        self.assertEqual(Money(Decimal("100"), "JPY").format(), "100")

    def test_serialisation_has_no_grouping(self) -> None:
        money = Money(Decimal("1234.5"))
        self.assertEqual(money.to_dict(), {"amount": "1234.50", "currency": "CNY"})
        self.assertEqual(Money.from_dict(money.to_dict()), money)

    def test_division_by_zero(self) -> None:
        with self.assertRaises(ZeroDivisionError):
            Money(Decimal("1")) / 0


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
