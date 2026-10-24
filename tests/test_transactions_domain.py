"""Tests for postings, transactions and the double entry invariant.

分录、凭证以及复式记账恒等式的测试。"""

from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from centsible.domain.transactions import (
    Posting,
    Transaction,
    TransactionStatus,
    check_balanced,
    normalize_tags,
    sum_credits,
    sum_debits,
)
from centsible.errors import UnbalancedEntryError, ValidationError


def tx(*pairs: tuple[str, str], description: str = "Test entry") -> Transaction:
    return Transaction(
        date=date(2025, 3, 1),
        description=description,
        postings=tuple(Posting(code, Decimal(amount)) for code, amount in pairs),
    )


class PostingTests(unittest.TestCase):
    def test_sign_semantics(self) -> None:
        debit = Posting("1010", Decimal("5"))
        credit = Posting("1010", Decimal("-5"))
        self.assertTrue(debit.is_debit)
        self.assertFalse(debit.is_credit)
        self.assertTrue(credit.is_credit)
        self.assertEqual(debit.debit_amount, Decimal("5"))
        self.assertEqual(debit.credit_amount, Decimal("0"))
        self.assertEqual(credit.credit_amount, Decimal("5"))

    def test_zero_posting_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            Posting("1010", Decimal("0"))

    def test_account_code_is_normalised(self) -> None:
        self.assertEqual(Posting(" cash ", Decimal("1")).account_code, "CASH")

    def test_negated(self) -> None:
        posting = Posting("1010", Decimal("12.34"), "memo")
        negated = posting.negated()
        self.assertEqual(negated.amount, Decimal("-12.34"))
        self.assertEqual(negated.memo, "memo")
        self.assertEqual(negated.account_code, "1010")

    def test_to_dict(self) -> None:
        data = Posting("1010", Decimal("-5"), "note").to_dict()
        self.assertEqual(data["account"], "1010")
        self.assertEqual(data["debit"], "0")
        self.assertEqual(data["credit"], "5")
        self.assertEqual(data["memo"], "note")


class TransactionTests(unittest.TestCase):
    def test_balanced_entry(self) -> None:
        entry = tx(("5100", "100"), ("1010", "-100"))
        self.assertEqual(entry.total_debits, Decimal("100"))
        self.assertEqual(entry.total_credits, Decimal("100"))
        self.assertEqual(entry.accounts, ("1010", "5100"))
        self.assertIs(entry.status, TransactionStatus.POSTED)

    def test_needs_two_postings(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            tx(("1010", "100"))
        self.assertIn("at least two postings", str(caught.exception))

    def test_unbalanced_entry_is_refused(self) -> None:
        with self.assertRaises(UnbalancedEntryError) as caught:
            tx(("5100", "100"), ("1010", "-90"))
        message = str(caught.exception)
        self.assertIn("do not balance", message)
        self.assertIn("10", message)

    def test_description_is_required(self) -> None:
        with self.assertRaises(ValidationError):
            Transaction(
                date=date(2025, 1, 1),
                description="   ",
                postings=(Posting("A", Decimal("1")), Posting("B", Decimal("-1"))),
            )

    def test_date_must_not_be_datetime(self) -> None:
        with self.assertRaises(ValidationError):
            Transaction(
                date=datetime(2025, 1, 1, 12),  # type: ignore[arg-type]
                description="x",
                postings=(Posting("A", Decimal("1")), Posting("B", Decimal("-1"))),
            )

    def test_tags_are_normalised_and_deduplicated(self) -> None:
        raw_tags: Any = "Rent, #rent fixed"
        entry = Transaction(
            date=date(2025, 1, 1),
            description="x",
            postings=(Posting("A", Decimal("1")), Posting("B", Decimal("-1"))),
            tags=raw_tags,
        )
        self.assertEqual(entry.tags, ("fixed", "rent"))
        self.assertEqual(entry.tag_text, "fixed, rent")

    def test_invalid_tag_is_refused(self) -> None:
        with self.assertRaises(ValidationError):
            normalize_tags(["bad tag!"])

    def test_reference_is_trimmed(self) -> None:
        entry = Transaction(
            date=date(2025, 1, 1),
            description="x",
            postings=(Posting("A", Decimal("1")), Posting("B", Decimal("-1"))),
            reference="  INV-1 ",
        )
        self.assertEqual(entry.reference, "INV-1")

    def test_blank_reference_becomes_none(self) -> None:
        entry = Transaction(
            date=date(2025, 1, 1),
            description="x",
            postings=(Posting("A", Decimal("1")), Posting("B", Decimal("-1"))),
            reference="   ",
        )
        self.assertIsNone(entry.reference)

    def test_voiding_requires_a_reason(self) -> None:
        entry = Transaction(
            id=7,
            date=date(2025, 3, 1),
            description="Rent",
            postings=(Posting("5100", Decimal("100")), Posting("1010", Decimal("-100"))),
        )
        with self.assertRaises(ValidationError):
            entry.voided(reason="   ")

    def test_voiding_keeps_the_data_and_marks_the_status(self) -> None:
        entry = Transaction(
            id=7,
            date=date(2025, 3, 1),
            description="Rent",
            postings=(Posting("5100", Decimal("100")), Posting("1010", Decimal("-100"))),
            tags=("fixed",),
        )
        voided = entry.voided(reason="wrong month")
        self.assertTrue(voided.is_void)
        self.assertEqual(voided.void_reason, "wrong month")
        self.assertEqual(voided.total_debits, Decimal("100"))
        self.assertEqual(voided.description, "Rent")
        self.assertEqual(voided.tags, ("fixed",))
        self.assertFalse(entry.is_void)  # the original object is untouched | 原对象保持不变

    def test_postings_for_account(self) -> None:
        entry = tx(("1010", "100"), ("4000", "-60"), ("2200", "-40"))
        self.assertEqual(len(entry.postings_for("1010")), 1)
        self.assertEqual(entry.postings_for("9999"), ())

    def test_void_status(self) -> None:
        raw_status: Any = "void"
        entry = Transaction(
            date=date(2025, 1, 1),
            description="x",
            postings=(Posting("A", Decimal("1")), Posting("B", Decimal("-1"))),
            status=raw_status,
        )
        self.assertTrue(entry.is_void)

    def test_to_dict(self) -> None:
        entry = Transaction(
            id=3,
            date=date(2025, 1, 2),
            description="x",
            postings=(Posting("A", Decimal("1")), Posting("B", Decimal("-1"))),
        )
        data = entry.to_dict()
        self.assertEqual(data["id"], 3)
        self.assertEqual(data["date"], "2025-01-02")
        self.assertEqual(data["total"], "1")
        self.assertEqual(len(data["postings"]), 2)


class BalanceHelperTests(unittest.TestCase):
    def test_residual(self) -> None:
        balanced = (Posting("A", Decimal("5")), Posting("B", Decimal("-5")))
        self.assertEqual(check_balanced(balanced), Decimal("0"))
        unbalanced = (Posting("A", Decimal("5")), Posting("B", Decimal("-4")))
        self.assertEqual(check_balanced(unbalanced), Decimal("1"))

    def test_totals_ignore_the_other_side(self) -> None:
        postings = (
            Posting("A", Decimal("5")),
            Posting("B", Decimal("-3")),
            Posting("C", Decimal("-2")),
        )
        self.assertEqual(sum_debits(postings), Decimal("5"))
        self.assertEqual(sum_credits(postings), Decimal("5"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
