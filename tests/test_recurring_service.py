"""Tests for recurring rules and their materialisation.

周期规则及其生成凭证的测试。"""

from __future__ import annotations

import unittest
from datetime import date

from centsible.errors import ConflictError, NotFoundError, ValidationError
from tests.support import LedgerTestCase, posts


class RecurringServiceTests(LedgerTestCase):
    def create_rent(self, **kwargs):
        payload = {
            "name": "Monthly rent",
            "description": "Office rent",
            "frequency": "monthly",
            "postings": posts(("5100", "1200"), ("1010", "-1200")),
            "start_date": date(2025, 1, 1),
        }
        payload.update(kwargs)
        return self.services.recurring.create(**payload)

    def test_create_list_and_get(self) -> None:
        rule = self.create_rent()
        self.assertIsNotNone(rule.id)
        self.assertEqual(rule.next_date, date(2025, 1, 1))
        self.assertEqual(len(self.services.recurring.list()), 1)
        self.assertEqual(self.services.recurring.get(rule.id).name, "Monthly rent")

    def test_names_must_be_unique(self) -> None:
        self.create_rent()
        with self.assertRaises(ConflictError):
            self.create_rent()

    def test_template_must_balance(self) -> None:
        with self.assertRaises(ValidationError):
            self.create_rent(postings=posts(("5100", "1200"), ("1010", "-1199")))

    def test_template_needs_two_legs(self) -> None:
        with self.assertRaises(ValidationError):
            self.create_rent(postings=posts(("5100", "1200")))

    def test_end_date_must_follow_start(self) -> None:
        with self.assertRaises(ValidationError):
            self.create_rent(end_date=date(2024, 12, 31))

    def test_unknown_frequency(self) -> None:
        with self.assertRaises(ValidationError):
            self.create_rent(frequency="fortnightly")

    def test_run_materialises_every_due_occurrence(self) -> None:
        rule = self.create_rent()
        result = self.services.recurring.run()
        self.assertEqual(result.created_count, 3)
        self.assertEqual(result.rules_processed, 1)
        self.assertEqual([tx.date for tx in result.transactions_created],
                         [date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1)])
        self.assertEqual(self.services.recurring.get(rule.id).next_date, date(2025, 4, 1))
        self.assertEqual(self.services.ledger.stats()["transactions"], 3)

    def test_running_twice_creates_nothing_new(self) -> None:
        self.create_rent()
        self.services.recurring.run()
        second = self.services.recurring.run()
        self.assertEqual(second.created_count, 0)
        self.assertEqual(second.rules_processed, 0)
        self.assertEqual(self.services.ledger.stats()["transactions"], 3)

    def test_dry_run_writes_nothing(self) -> None:
        rule = self.create_rent()
        result = self.services.recurring.run(dry_run=True)
        self.assertTrue(result.dry_run)
        self.assertEqual(result.created_count, 3)
        self.assertEqual(self.services.ledger.stats()["transactions"], 0)
        self.assertEqual(self.services.recurring.get(rule.id).next_date, date(2025, 1, 1))

    def test_paused_rules_are_skipped(self) -> None:
        rule = self.create_rent()
        self.services.recurring.pause(rule.id)
        result = self.services.recurring.run()
        self.assertEqual(result.created_count, 0)
        self.assertEqual(self.services.ledger.stats()["transactions"], 0)

    def test_resume_pause_and_delete(self) -> None:
        rule = self.create_rent()
        paused = self.services.recurring.pause(rule.id)
        self.assertFalse(paused.active)
        resumed = self.services.recurring.resume(rule.id)
        self.assertTrue(resumed.active)
        self.services.recurring.delete(rule.id)
        with self.assertRaises(NotFoundError):
            self.services.recurring.get(rule.id)

    def test_rule_stops_at_its_end_date(self) -> None:
        rule = self.create_rent(end_date=date(2025, 2, 1))
        result = self.services.recurring.run()
        self.assertEqual(result.created_count, 2)
        stored = self.services.recurring.get(rule.id)
        self.assertFalse(stored.active)
        self.assertEqual(stored.next_date, date(2025, 3, 1))

    def test_a_broken_rule_reports_issues_and_moves_on(self) -> None:
        rule = self.create_rent()
        self.services.accounts.archive("5100")
        result = self.services.recurring.run()
        self.assertEqual(result.created_count, 0)
        self.assertEqual(len(result.issues), 3)
        self.assertIn("archived", result.issues[0].message)
        self.assertEqual(self.services.recurring.get(rule.id).next_date, date(2025, 4, 1))

    def test_horizon_limits_materialisation(self) -> None:
        rule = self.create_rent()
        result = self.services.recurring.run(until=date(2025, 1, 31))
        self.assertEqual(result.created_count, 1)
        self.assertEqual(self.services.recurring.get(rule.id).next_date, date(2025, 2, 1))

    def test_interval_is_honoured(self) -> None:
        rule = self.create_rent(
            name="Quarterly rent", frequency="quarterly", interval=1
        )
        result = self.services.recurring.run(until=date(2025, 6, 30))
        self.assertEqual([tx.date for tx in result.transactions_created],
                         [date(2025, 1, 1), date(2025, 4, 1)])
        self.assertEqual(self.services.recurring.get(rule.id).next_date, date(2025, 7, 1))

    def test_upcoming(self) -> None:
        self.create_rent()
        self.services.recurring.run()
        upcoming = self.services.recurring.upcoming(days=45)
        self.assertEqual(len(upcoming), 1)
        self.assertEqual(upcoming[0][1], date(2025, 4, 1))

    def test_json_shape(self) -> None:
        rule = self.create_rent()
        payload = self.services.recurring.run(dry_run=True).to_dict()
        self.assertEqual(payload["kind"], "recurrence-run")
        self.assertTrue(payload["dry_run"])
        self.assertEqual(rule.schedule_label, "every month")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
