"""Tests for the demo data generator.

演示数据生成器的测试。"""

from __future__ import annotations

import unittest

from centsible.demo import MAX_DEMO_MONTHS, seed_demo
from centsible.errors import ConflictError, ValidationError
from tests.support import LedgerTestCase


class DemoTests(LedgerTestCase):
    def test_seed_creates_a_balanced_ledger(self) -> None:
        created = seed_demo(self.services, months=3)

        self.assertEqual(created["accounts"], 0)  # the default chart already existed | 默认科目表已存在
        self.assertGreater(created["transactions"], 10)
        self.assertEqual(created["budgets"], 5)
        self.assertEqual(created["recurring_rules"], 2)

        trial = self.services.reports.trial_balance()
        self.assertTrue(trial.balanced)
        sheet = self.services.reports.balance_sheet()
        self.assertTrue(sheet.balanced)

    def test_the_demo_business_turns_a_profit(self) -> None:
        seed_demo(self.services, months=3)
        report = self.services.reports.activity(months=3)
        self.assertGreater(report.total_net.amount, 0)

    def test_seeding_twice_is_refused(self) -> None:
        seed_demo(self.services, months=2)
        with self.assertRaises(ConflictError):
            seed_demo(self.services, months=2)

    def test_month_count_is_validated(self) -> None:
        for months in (0, -1, MAX_DEMO_MONTHS + 1):
            with self.subTest(months=months), self.assertRaises(ValidationError):
                seed_demo(self.services, months=months)

    def test_every_transaction_is_posted(self) -> None:
        seed_demo(self.services, months=2)
        transactions = self.services.ledger.search(limit=None)
        self.assertTrue(all(not transaction.is_void for transaction in transactions))
        self.assertTrue(all(len(transaction.postings) >= 2 for transaction in transactions))

    def test_seed_records_the_ledger_currency(self) -> None:
        seed_demo(self.services, months=1)
        self.assertEqual(self.services.ledger.ledger_currency(), "CNY")

    def test_recurring_rules_are_scheduled_for_the_next_month(self) -> None:
        seed_demo(self.services, months=2)
        rules = self.services.recurring.list()
        self.assertTrue(all(rule.active for rule in rules))
        self.assertTrue(all(rule.next_date > self.today for rule in rules))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
