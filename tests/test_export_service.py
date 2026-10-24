"""Tests for the export service.

导出服务的测试。"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from centsible.domain.periods import DatePeriod
from tests.support import LedgerTestCase


class ExportServiceTests(LedgerTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.rent = self.record(
            "Office rent",
            ("5100", "100"),
            ("1010", "-100"),
            day=date(2025, 3, 1),
            payee="Landlord",
        )
        self.fees = self.record(
            "Bank fees", ("5600", "12"), ("1010", "-12"), day=date(2025, 3, 2)
        )

    def test_journal_has_one_row_per_posting(self) -> None:
        export = self.services.exports.journal()
        self.assertEqual(export.transaction_count, 2)
        self.assertEqual(export.row_count, 4)
        self.assertEqual(export.headers[0], "id")
        self.assertEqual(export.headers[7], "account")

        first = export.rows[0]
        self.assertEqual(first[1], "2025-03-01")
        self.assertEqual(first[2], "Office rent")
        self.assertEqual(first[3], "Landlord")
        self.assertEqual(first[7], "5100")
        self.assertEqual(first[8], "100.00")
        self.assertEqual(export.rows[1][8], "-100.00")

    def test_period_filter(self) -> None:
        april = self.record(
            "April rent",
            ("5100", "100"),
            ("1010", "-100"),
            day=date(2025, 4, 1),
            allow_future=True,
        )
        export = self.services.exports.journal(DatePeriod.for_month(2025, 3))
        self.assertEqual(export.transaction_count, 2)
        self.assertNotIn(april.id, [int(row[0]) for row in export.rows])

    def test_voided_entries_can_be_excluded(self) -> None:
        self.services.ledger.void(self.fees.id, reason="wrong account")
        with_void = self.services.exports.journal()
        without_void = self.services.exports.journal(include_void=False)
        self.assertEqual(with_void.transaction_count, 2)
        self.assertEqual(with_void.row_count, 4)
        # transaction_count describes what the export contains, so a filtered
        # export reports only the transactions it still carries.
        # transaction_count 描述的是导出包含的内容，因此过滤后的导出只报告
        # 它仍然保留的凭证。
        self.assertEqual(without_void.transaction_count, 1)
        self.assertEqual(without_void.row_count, 2)

    def test_backup_payload(self) -> None:
        payload = self.services.exports.backup()
        data = payload.data
        self.assertEqual(data["format"], "centsible-backup")
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["ledger"]["currency"], "CNY")
        self.assertEqual(payload.counts["accounts"], 23)
        self.assertEqual(payload.counts["transactions"], 2)
        self.assertEqual(payload.counts["postings"], 4)
        self.assertIn("budgets", data)
        self.assertIn("recurring_rules", data)
        self.assertEqual(data["meta"], {})
        self.assertEqual(payload.to_dict()["counts"]["transactions"], 2)

    def test_backup_includes_budgets_and_rules(self) -> None:
        self.services.budgets.set(account_code="5100", month="2025-03", amount="120")
        self.services.recurring.create(
            name="Rent",
            description="Office rent",
            frequency="monthly",
            postings=self.rent.postings,
            start_date=date(2025, 4, 1),
        )
        payload = self.services.exports.backup()
        self.assertEqual(payload.counts["budgets"], 1)
        self.assertEqual(payload.counts["recurring_rules"], 1)
        self.assertEqual(len(payload.data["budgets"]), 1)

    def test_journal_workbook_is_typed(self) -> None:
        workbook = self.services.exports.journal_workbook()
        self.assertEqual(workbook.title, "Centsible journal")
        sheet = workbook.sheets[0]
        self.assertEqual(sheet.title, "Journal")
        self.assertEqual(sheet.row_count, 4)

        first = sheet.rows[0]
        self.assertEqual(first[0], self.rent.id)
        self.assertEqual(first[1], date(2025, 3, 1))
        self.assertEqual(first[8], Decimal("100.00"))
        self.assertEqual(sheet.rows[1][8], Decimal("-100.00"))

    def test_journal_workbook_honours_void_filtering(self) -> None:
        self.services.ledger.void(self.fees.id, reason="wrong account")
        everything = self.services.exports.journal_workbook()
        posted_only = self.services.exports.journal_workbook(include_void=False)
        self.assertEqual(everything.sheets[0].row_count, 4)
        self.assertEqual(posted_only.sheets[0].row_count, 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
