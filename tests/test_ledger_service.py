"""Tests for the journal write side: recording, voiding, locking.

日记账写入侧的测试：记录凭证、作废与锁账。"""

from __future__ import annotations

import unittest
from datetime import date, timedelta
from decimal import Decimal

from centsible.bootstrap import build_services
from centsible.errors import (
    ConflictError,
    LedgerLockedError,
    NotFoundError,
    UnbalancedEntryError,
    ValidationError,
)
from tests.support import LedgerTestCase


class RecordTests(LedgerTestCase):
    def test_record_returns_a_stored_transaction(self) -> None:
        transaction = self.record(
            "Office rent",
            ("5100", "1200"),
            ("1010", "-1200"),
            payee="Landlord",
            tags="fixed",
        )
        self.assertIsNotNone(transaction.id)
        self.assertEqual(transaction.total_debits, Decimal("1200"))
        self.assertEqual(self.services.ledger.get(transaction.id).description, "Office rent")

    def test_unbalanced_entry_is_refused_before_it_is_stored(self) -> None:
        with self.assertRaises(UnbalancedEntryError):
            self.record("Bad", ("5100", "10"), ("1010", "-9"))
        self.assertEqual(self.services.ledger.stats()["transactions"], 0)

    def test_future_dates_are_refused_unless_allowed(self) -> None:
        tomorrow = self.today + timedelta(days=1)
        with self.assertRaises(ValidationError) as caught:
            self.record("Future", ("5100", "10"), ("1010", "-10"), day=tomorrow)
        self.assertIn("future", str(caught.exception))

        transaction = self.record(
            "Future", ("5100", "10"), ("1010", "-10"), day=tomorrow, allow_future=True
        )
        self.assertEqual(transaction.date, tomorrow)

    def test_unknown_account_is_reported(self) -> None:
        with self.assertRaises(NotFoundError):
            self.record("Nope", ("9999", "10"), ("1010", "-10"))

    def test_archived_account_cannot_receive_postings(self) -> None:
        self.services.accounts.create(code="TEMP", name="Temp", type="asset")
        self.services.accounts.archive("TEMP")
        with self.assertRaises(ConflictError):
            self.record("Blocked", ("TEMP", "10"), ("1000", "-10"))

    def test_heading_accounts_cannot_receive_postings(self) -> None:
        self.services.accounts.create(code="HEAD", name="Head", type="asset")
        self.services.accounts.create(code="LEAF", name="Leaf", type="asset", parent_code="HEAD")
        with self.assertRaises(ValidationError) as caught:
            self.record("Heading", ("HEAD", "10"), ("1000", "-10"))
        self.assertIn("heading", str(caught.exception))

    def test_reference_must_be_unique(self) -> None:
        self.record("One", ("5100", "1"), ("1010", "-1"), reference="INV-1")
        with self.assertRaises(ConflictError) as caught:
            self.record("Two", ("5100", "2"), ("1010", "-2"), reference="INV-1")
        self.assertIn("INV-1", str(caught.exception))
        self.assertIn("transaction_id", caught.exception.context)

    def test_audit_trail_records_every_entry(self) -> None:
        self.record("Rent", ("5100", "10"), ("1010", "-10"))
        entries = self.services.ledger.audit_trail(limit=10)
        self.assertEqual(entries[0].action, "transaction.record")
        self.assertEqual(entries[0].entity, "transaction")


class LockTests(LedgerTestCase):
    def test_lock_blocks_postings_on_or_before_the_lock_date(self) -> None:
        self.services.ledger.set_lock_date(self.today)
        with self.assertRaises(LedgerLockedError):
            self.record("Today", ("5100", "1"), ("1010", "-1"))
        with self.assertRaises(LedgerLockedError):
            self.record("Yesterday", ("5100", "1"), ("1010", "-1"), day=self.today - timedelta(days=1))

        allowed = self.record(
            "Tomorrow", ("5100", "1"), ("1010", "-1"), day=self.today + timedelta(days=1), allow_future=True
        )
        self.assertEqual(allowed.date, self.today + timedelta(days=1))

    def test_lock_date_cannot_be_in_the_future(self) -> None:
        with self.assertRaises(ValidationError):
            self.services.ledger.set_lock_date(self.today + timedelta(days=1))

    def test_lock_cannot_move_backwards(self) -> None:
        self.services.ledger.set_lock_date(self.today)
        with self.assertRaises(ConflictError):
            self.services.ledger.set_lock_date(self.today - timedelta(days=30))

    def test_lock_is_recorded_in_meta(self) -> None:
        self.services.ledger.set_lock_date(self.today)
        self.assertEqual(self.services.ledger.lock_date(), self.today)
        status = self.services.ledger.stats()
        self.assertEqual(status["closed_through"], self.today.isoformat())

    def test_unlock_requires_force(self) -> None:
        self.services.ledger.set_lock_date(self.today)
        with self.assertRaises(ConflictError):
            self.services.ledger.unlock()
        self.assertEqual(self.services.ledger.unlock(force=True), self.today)
        self.assertIsNone(self.services.ledger.lock_date())

    def test_unlock_when_open_is_harmless(self) -> None:
        self.assertIsNone(self.services.ledger.unlock(force=True))


class VoidTests(LedgerTestCase):
    def test_void_marks_the_entry_and_removes_it_from_balances(self) -> None:
        original = self.record(
            "Office rent", ("5100", "1200"), ("1010", "-1200"), day=self.today - timedelta(days=5)
        )
        voided = self.services.ledger.void(original.id, reason="wrong month")

        self.assertEqual(voided.id, original.id)
        self.assertTrue(voided.is_void)
        self.assertEqual(voided.void_reason, "wrong month")
        self.assertEqual(voided.total_debits, Decimal("1200"))

        reloaded = self.services.ledger.get(original.id)
        self.assertTrue(reloaded.is_void)
        self.assertEqual(self.balance("5100"), Decimal("0"))
        self.assertEqual(self.balance("1010"), Decimal("0"))

    def test_the_entry_stays_in_the_journal_for_audit(self) -> None:
        original = self.record("Rent", ("5100", "100"), ("1010", "-100"))
        self.services.ledger.void(original.id, reason="duplicate")
        stats = self.services.ledger.stats()
        self.assertEqual(stats["transactions"], 1)
        self.assertEqual(stats["posted_transactions"], 0)

        listed = self.services.ledger.search(status="void")
        self.assertEqual([tx.id for tx in listed], [original.id])
        entries = self.services.ledger.audit_trail()
        self.assertEqual(entries[0].action, "transaction.void")
        self.assertEqual(entries[0].detail, "duplicate")

    def test_a_transaction_cannot_be_voided_twice(self) -> None:
        original = self.record("Rent", ("5100", "100"), ("1010", "-100"))
        self.services.ledger.void(original.id, reason="first")
        with self.assertRaises(ConflictError) as caught:
            self.services.ledger.void(original.id, reason="second")
        self.assertIn("already void", str(caught.exception))

    def test_a_reason_is_required(self) -> None:
        original = self.record("Rent", ("5100", "100"), ("1010", "-100"))
        with self.assertRaises(ValidationError):
            self.services.ledger.void(original.id, reason="   ")

    def test_voiding_a_missing_transaction(self) -> None:
        with self.assertRaises(NotFoundError):
            self.services.ledger.void(999, reason="nope")

    def test_void_is_blocked_in_a_closed_period(self) -> None:
        original = self.record(
            "Rent", ("5100", "100"), ("1010", "-100"), day=self.today - timedelta(days=10)
        )
        self.services.ledger.set_lock_date(self.today)
        with self.assertRaises(LedgerLockedError):
            self.services.ledger.void(original.id, reason="late correction")

    def test_void_keeps_reports_balanced(self) -> None:
        original = self.record("Rent", ("5100", "100"), ("1010", "-100"))
        self.services.ledger.void(original.id, reason="mistake")
        trial = self.services.reports.trial_balance()
        self.assertTrue(trial.balanced)
        self.assertEqual(trial.rows, ())
        self.assertEqual(self.balance("5100"), Decimal("0"))


class RestoreTests(LedgerTestCase):
    def test_restore_brings_a_voided_entry_back(self) -> None:
        original = self.record("Rent", ("5100", "100"), ("1010", "-100"))
        self.services.ledger.void(original.id, reason="typed the wrong id")
        self.assertEqual(self.balance("5100"), Decimal("0"))

        restored = self.services.ledger.restore(original.id, reason="undo the void")
        self.assertFalse(restored.is_void)
        self.assertEqual(restored.void_reason, "")
        self.assertEqual(self.balance("5100"), Decimal("100"))

        entries = self.services.ledger.audit_trail(limit=5)
        self.assertEqual(entries[0].action, "transaction.restore")
        self.assertIn("typed the wrong id", entries[0].detail)
        self.assertIn("undo the void", entries[0].detail)

    def test_restoring_a_live_entry_is_a_conflict(self) -> None:
        original = self.record("Rent", ("5100", "100"), ("1010", "-100"))
        with self.assertRaises(ConflictError) as caught:
            self.services.ledger.restore(original.id)
        self.assertIn("not void", str(caught.exception))

    def test_restore_is_blocked_in_a_closed_period(self) -> None:
        original = self.record(
            "Rent", ("5100", "100"), ("1010", "-100"), day=self.today - timedelta(days=10)
        )
        self.services.ledger.void(original.id, reason="mistake")
        self.services.ledger.set_lock_date(self.today)
        with self.assertRaises(LedgerLockedError):
            self.services.ledger.restore(original.id)

    def test_restoring_a_missing_transaction(self) -> None:
        with self.assertRaises(NotFoundError):
            self.services.ledger.restore(999)


class LedgerFactsTests(LedgerTestCase):
    def test_a_fresh_ledger_has_no_recorded_currency(self) -> None:
        self.assertIsNone(self.services.ledger.ledger_currency())

    def test_recording_the_currency_is_idempotent(self) -> None:
        self.assertEqual(self.services.ledger.record_ledger_currency("CNY"), "CNY")
        self.assertEqual(self.services.ledger.record_ledger_currency("cny"), "CNY")
        self.assertEqual(self.services.ledger.ledger_currency(), "CNY")
        self.assertEqual(self.services.ledger.stats()["ledger_currency"], "CNY")

    def test_the_currency_cannot_change_later(self) -> None:
        self.services.ledger.record_ledger_currency("CNY")
        with self.assertRaises(ConflictError) as caught:
            self.services.ledger.record_ledger_currency("USD")
        self.assertIn("cannot be changed", str(caught.exception))
        self.assertEqual(self.services.ledger.ledger_currency(), "CNY")

    def test_the_ledger_currency_wins_over_a_stale_configuration(self) -> None:
        self.services.ledger.record_ledger_currency("CNY")
        reopened = build_services(
            self.config.with_overrides(currency="USD"), clock=self.clock
        )
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.settings.currency, "CNY")
        self.assertEqual(reopened.config.currency, "USD")


class SearchTests(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.first = self.record(
            "Office rent", ("5100", "100"), ("1010", "-100"), tags="fixed",
            day=date(2025, 2, 10),
        )
        self.second = self.record(
            "POS sales", ("1010", "500"), ("4000", "-500"), tags="sales",
            day=date(2025, 3, 10), payee="Card terminal",
        )

    def test_filter_by_account_and_tag(self) -> None:
        by_account = self.services.ledger.search(account_code="4000")
        self.assertEqual([tx.id for tx in by_account], [self.second.id])

        by_tag = self.services.ledger.search(tag="#fixed")
        self.assertEqual([tx.id for tx in by_tag], [self.first.id])

    def test_filter_by_text(self) -> None:
        found = self.services.ledger.search(text="terminal")
        self.assertEqual([tx.id for tx in found], [self.second.id])

    def test_limit_and_recency(self) -> None:
        recent = self.services.ledger.recent(limit=1)
        self.assertEqual(recent[0].id, self.second.id)

    def test_stats(self) -> None:
        stats = self.services.ledger.stats()
        self.assertEqual(stats["transactions"], 2)
        self.assertEqual(stats["posted_transactions"], 2)
        self.assertEqual(stats["first_transaction"], "2025-02-10")
        self.assertEqual(stats["last_transaction"], "2025-03-10")
        self.assertIsNone(stats["closed_through"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
