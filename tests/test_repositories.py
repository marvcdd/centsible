"""Tests for the SQLite repositories, exercised through a unit of work.

SQLite 仓储的测试，全部经由工作单元执行。"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from centsible.application.ports import ImportBatch, TransactionQuery
from centsible.domain.accounts import Account, AccountType
from centsible.domain.budgets import Budget
from centsible.domain.recurrence import Frequency, RecurringRule
from centsible.domain.periods import DatePeriod
from centsible.domain.transactions import Posting, Transaction, TransactionStatus
from centsible.errors import ConflictError, NotFoundError
from tests.support import LedgerTestCase, posts


class RepositoryTestCase(LedgerTestCase):
    """Helpers that keep the repository tests short.

    让仓储测试保持简练的辅助方法。"""

    def make_account(
        self, code: str, name: str = "", account_type: AccountType = AccountType.ASSET
    ) -> Account:
        with self.services.unit_of_work() as uow:
            account = uow.accounts.add(
                Account(code=code, name=name or f"Account {code}", type=account_type)
            )
            uow.commit()
        return account

    def make_transaction(
        self,
        description: str,
        *pairs: tuple[str, str],
        day: date = date(2025, 3, 10),
        **kwargs,
    ) -> Transaction:
        with self.services.unit_of_work() as uow:
            stored = uow.transactions.add(
                Transaction(
                    date=day, description=description, postings=posts(*pairs), **kwargs
                )
            )
            uow.commit()
        return stored


class AccountRepositoryTests(RepositoryTestCase):
    def test_add_get_and_count(self) -> None:
        account = self.make_account("cash", "Cash")
        self.assertIsNotNone(account.id)
        with self.services.unit_of_work() as uow:
            self.assertEqual(uow.accounts.get("cash").code, "CASH")
            self.assertEqual(uow.accounts.count(), 24)  # 23 from the chart + 1 | 科目表 23 个 + 1 个

    def test_get_returns_none_for_unknown(self) -> None:
        with self.services.unit_of_work() as uow:
            self.assertIsNone(uow.accounts.get("nope"))

    def test_list_and_archive_filter(self) -> None:
        account = self.make_account("TEMP")
        with self.services.unit_of_work() as uow:
            uow.accounts.update(account.with_changes(archived=True))
            uow.commit()
        with self.services.unit_of_work() as uow:
            # the chart's accounts are all active
            # 科目表里的科目全部未归档
            active = uow.accounts.list(include_archived=False)
            everything = uow.accounts.list(include_archived=True)
        self.assertEqual(len(everything), len(active) + 1)
        self.assertNotIn("TEMP", {item.code for item in active})

    def test_children_and_has_children(self) -> None:
        self.make_account("HEAD", "Heading")
        with self.services.unit_of_work() as uow:
            uow.accounts.add(
                Account(code="LEAF", name="Leaf", type=AccountType.ASSET, parent_code="HEAD")
            )
            uow.commit()
        with self.services.unit_of_work() as uow:
            self.assertTrue(uow.accounts.has_children("HEAD"))
            self.assertFalse(uow.accounts.has_children("LEAF"))
            self.assertEqual([child.code for child in uow.accounts.children_of("HEAD")], ["LEAF"])

    def test_update_and_delete(self) -> None:
        self.make_account("TEMP", "Temporary")
        with self.services.unit_of_work() as uow:
            existing = uow.accounts.get("TEMP")
            updated = uow.accounts.update(existing.with_changes(name="Renamed"))
            uow.commit()
        self.assertEqual(updated.name, "Renamed")

        with self.services.unit_of_work() as uow:
            uow.accounts.delete("TEMP")
            uow.commit()
        with self.services.unit_of_work() as uow:
            self.assertIsNone(uow.accounts.get("TEMP"))
            with self.assertRaises(NotFoundError):
                uow.accounts.delete("TEMP")

    def test_duplicate_code_is_a_conflict(self) -> None:
        self.make_account("TEMP")
        with self.services.unit_of_work() as uow, self.assertRaises(ConflictError):
            uow.accounts.add(Account(code="TEMP", name="Again", type=AccountType.ASSET))

    def test_posting_count(self) -> None:
        self.make_transaction("Rent", ("5100", "100"), ("1010", "-100"))
        with self.services.unit_of_work() as uow:
            self.assertEqual(uow.accounts.posting_count("1010"), 1)
            self.assertEqual(uow.accounts.posting_count("1000"), 0)


class TransactionRepositoryTests(RepositoryTestCase):
    def test_add_hydrates_postings_in_order(self) -> None:
        stored = self.make_transaction(
            "POS sales",
            ("1010", "4520"),
            ("4000", "-4000"),
            ("2200", "-520"),
            tags="sales",
            payee="Card terminal",
        )
        self.assertIsNotNone(stored.id)
        self.assertEqual(
            [posting.account_code for posting in stored.postings],
            ["1010", "4000", "2200"],
        )
        self.assertEqual(stored.tags, ("sales",))
        self.assertEqual(stored.payee, "Card terminal")
        self.assertIsNotNone(stored.created_at)

    def test_get_and_reference_lookup(self) -> None:
        stored = self.make_transaction(
            "Rent", ("5100", "100"), ("1010", "-100"), reference="INV-1"
        )
        with self.services.unit_of_work() as uow:
            self.assertIsNotNone(uow.transactions.get(stored.id))
            found = uow.transactions.find_by_reference("INV-1")
            self.assertIsNotNone(found)
            self.assertEqual(found.id, stored.id)
            self.assertIsNone(uow.transactions.find_by_reference("INV-2"))
            self.assertIsNone(uow.transactions.get(9999))

    def test_duplicate_reference_is_refused_by_the_index(self) -> None:
        self.make_transaction("One", ("5100", "1"), ("1010", "-1"), reference="DUP")
        with self.services.unit_of_work() as uow, self.assertRaises(ConflictError):
            uow.transactions.add(
                Transaction(
                    date=date(2025, 3, 11),
                    description="Two",
                    postings=(Posting("5100", Decimal("1")), Posting("1010", Decimal("-1"))),
                    reference="DUP",
                )
            )

    def test_search_by_period_account_tag_text_and_status(self) -> None:
        march = self.make_transaction(
            "Office rent", ("5100", "100"), ("1010", "-100"), tags="fixed", day=date(2025, 3, 10)
        )
        april = self.make_transaction(
            "POS sales", ("1010", "500"), ("4000", "-500"), tags="sales", day=date(2025, 4, 10)
        )

        with self.services.unit_of_work() as uow:
            march_only = uow.transactions.search(
                TransactionQuery(period=DatePeriod.for_month(2025, 3))
            )
            self.assertEqual([tx.id for tx in march_only], [march.id])

            by_account = uow.transactions.search(TransactionQuery(account_code="4000"))
            self.assertEqual([tx.id for tx in by_account], [april.id])

            by_tag = uow.transactions.search(TransactionQuery(tag="fixed"))
            self.assertEqual([tx.id for tx in by_tag], [march.id])

            by_text = uow.transactions.search(TransactionQuery(text="Office"))
            self.assertEqual([tx.id for tx in by_text], [march.id])

            limited = uow.transactions.search(TransactionQuery(limit=1))
            self.assertEqual(len(limited), 1)
            self.assertEqual(limited[0].id, april.id)  # newest first | 最新的排在前面

            skipped = uow.transactions.search(TransactionQuery(limit=1, offset=1))
            self.assertEqual(skipped[0].id, march.id)

    def test_totals_respect_the_date_range(self) -> None:
        self.make_transaction("March", ("5100", "100"), ("1010", "-100"), day=date(2025, 3, 10))
        self.make_transaction("April", ("5100", "40"), ("1010", "-40"), day=date(2025, 4, 10))
        with self.services.unit_of_work() as uow:
            march = uow.transactions.totals(start=date(2025, 3, 1), end=date(2025, 3, 31))
            everything = uow.transactions.totals()
        self.assertEqual(march["5100"].debit, 100)
        self.assertEqual(march["5100"].entries, 1)
        self.assertNotIn("1000", march)
        self.assertEqual(everything["5100"].debit, 140)

    def test_voided_transactions_are_excluded_from_totals(self) -> None:
        stored = self.make_transaction("Rent", ("5100", "100"), ("1010", "-100"))
        with self.services.unit_of_work() as uow:
            uow.transactions.mark_void(stored.id, reason="oops")
            uow.commit()
        with self.services.unit_of_work() as uow:
            self.assertEqual(uow.transactions.totals(), {})
            self.assertEqual(uow.transactions.count(include_void=False), 0)
            self.assertEqual(uow.transactions.count(include_void=True), 1)
            voided = uow.transactions.search(TransactionQuery(status=TransactionStatus.VOID))
            self.assertEqual(len(voided), 1)
            self.assertEqual(voided[0].void_reason, "oops")
        with self.services.unit_of_work() as uow, self.assertRaises(ConflictError):
            uow.transactions.mark_void(stored.id, reason="again")

    def test_type_totals_by_month(self) -> None:
        self.make_transaction("Rent", ("5100", "100"), ("1010", "-100"), day=date(2025, 3, 5))
        self.make_transaction("Sales", ("1010", "300"), ("4000", "-300"), day=date(2025, 4, 5))
        with self.services.unit_of_work() as uow:
            grouped = uow.transactions.type_totals_by_month(
                start=date(2025, 3, 1), end=date(2025, 4, 30)
            )
        self.assertEqual(grouped["2025-03"]["expense"].debit, 100)
        self.assertEqual(grouped["2025-04"]["income"].credit, 300)
        self.assertEqual(grouped["2025-04"]["asset"].debit, 300)

    def test_postings_for_an_account(self) -> None:
        self.make_transaction("Rent", ("5100", "100"), ("1010", "-100"), day=date(2025, 3, 5))
        self.make_transaction("Fees", ("5100", "12"), ("1010", "-12"), day=date(2025, 3, 8))
        with self.services.unit_of_work() as uow:
            lines = uow.transactions.postings("5100")
        self.assertEqual([line.amount for line in lines], [100, 12])
        self.assertEqual(lines[0].entry_date, date(2025, 3, 5))

    def test_date_bounds(self) -> None:
        self.make_transaction("A", ("5100", "1"), ("1010", "-1"), day=date(2025, 1, 5))
        self.make_transaction("B", ("5100", "1"), ("1010", "-1"), day=date(2025, 6, 5))
        with self.services.unit_of_work() as uow:
            first, last = uow.transactions.date_bounds()
        self.assertEqual((first, last), (date(2025, 1, 5), date(2025, 6, 5)))


class BudgetRepositoryTests(RepositoryTestCase):
    def test_upsert_inserts_then_updates(self) -> None:
        with self.services.unit_of_work() as uow:
            created = uow.budgets.upsert(
                Budget(account_code="5100", month="2025-03", amount=Decimal("100"))
            )
            uow.commit()
        self.assertIsNotNone(created.id)

        with self.services.unit_of_work() as uow:
            updated = uow.budgets.upsert(
                Budget(account_code="5100", month="2025-03", amount=Decimal("150"), note="raised")
            )
            uow.commit()
        self.assertEqual(updated.id, created.id)
        self.assertEqual(updated.amount, 150)
        self.assertEqual(updated.note, "raised")

    def test_list_get_delete(self) -> None:
        with self.services.unit_of_work() as uow:
            for month in ("2025-02", "2025-03", "2025-04"):
                uow.budgets.upsert(Budget(account_code="5100", month=month, amount=Decimal("10")))
            uow.commit()

        with self.services.unit_of_work() as uow:
            self.assertEqual(len(uow.budgets.list()), 3)
            self.assertEqual(
                [budget.month for budget in uow.budgets.list(start_month="2025-03", end_month="2025-04")],
                ["2025-03", "2025-04"],
            )
            self.assertIsNotNone(uow.budgets.get("5100", "2025-03"))
            self.assertTrue(uow.budgets.delete("5100", "2025-03"))
            self.assertFalse(uow.budgets.delete("5100", "2025-03"))
            self.assertEqual(uow.budgets.count(), 2)


class RecurringRepositoryTests(RepositoryTestCase):
    @staticmethod
    def rule(name: str = "Monthly rent") -> RecurringRule:
        return RecurringRule(
            name=name,
            description="Office rent",
            frequency=Frequency.MONTHLY,
            postings=(Posting("5100", Decimal("1200")), Posting("1010", Decimal("-1200"))),
            start_date=date(2025, 4, 1),
            next_date=date(2025, 4, 1),
        )

    def test_add_get_find_by_name(self) -> None:
        with self.services.unit_of_work() as uow:
            stored = uow.recurring.add(self.rule())
            uow.commit()
        self.assertIsNotNone(stored.id)
        with self.services.unit_of_work() as uow:
            self.assertEqual(uow.recurring.get(stored.id).name, "Monthly rent")
            self.assertEqual(uow.recurring.find_by_name("Monthly rent").id, stored.id)
            self.assertIsNone(uow.recurring.find_by_name("nope"))

    def test_list_is_ordered_by_next_date(self) -> None:
        with self.services.unit_of_work() as uow:

            later = RecurringRule(
                name="Later",
                description="x",
                frequency=Frequency.MONTHLY,
                postings=(Posting("5100", Decimal("1")), Posting("1010", Decimal("-1"))),
                start_date=date(2025, 6, 1),
                next_date=date(2025, 6, 1),
            )
            uow.recurring.add(self.rule("Sooner"))
            uow.recurring.add(later)
            uow.commit()
        with self.services.unit_of_work() as uow:
            self.assertEqual([rule.name for rule in uow.recurring.list()], ["Sooner", "Later"])

    def test_update_replaces_postings(self) -> None:
        with self.services.unit_of_work() as uow:
            stored = uow.recurring.add(self.rule())
            uow.commit()

        with self.services.unit_of_work() as uow:
            advanced = stored.advance(1).with_changes(description="Raised rent")
            updated = uow.recurring.update(advanced)
            uow.commit()
        self.assertEqual(updated.next_date, date(2025, 5, 1))
        self.assertEqual(updated.description, "Raised rent")
        self.assertEqual(len(updated.postings), 2)

    def test_delete(self) -> None:
        with self.services.unit_of_work() as uow:
            stored = uow.recurring.add(self.rule())
            uow.commit()
        with self.services.unit_of_work() as uow:
            self.assertTrue(uow.recurring.delete(stored.id))
            self.assertFalse(uow.recurring.delete(stored.id))
            self.assertIsNone(uow.recurring.get(stored.id))


class MetaAndAuditTests(RepositoryTestCase):
    def test_meta_crud(self) -> None:
        with self.services.unit_of_work() as uow:
            self.assertIsNone(uow.meta.get("lock_date"))
            uow.meta.set("lock_date", "2025-01-31")
            uow.meta.set("lock_date", "2025-02-28")
            uow.commit()
        with self.services.unit_of_work() as uow:
            self.assertEqual(uow.meta.get("lock_date"), "2025-02-28")
            self.assertEqual(uow.meta.all(), {"lock_date": "2025-02-28"})
            uow.meta.delete("lock_date")
            uow.commit()
        with self.services.unit_of_work() as uow:
            self.assertIsNone(uow.meta.get("lock_date"))

    def test_audit_trail_is_newest_first(self) -> None:
        with self.services.unit_of_work() as uow:
            uow.audit.record(action="a.one", entity="thing", entity_id=1)
            uow.audit.record(action="a.two", entity="thing", entity_id=2, detail="second")
            uow.commit()
        with self.services.unit_of_work() as uow:
            entries = uow.audit.recent(limit=1)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].action, "a.two")
        self.assertEqual(entries[0].entity_id, "2")
        self.assertIn("second", entries[0].detail)
        self.assertEqual(entries[0].to_dict()["entity"], "thing")

    def test_audit_count_is_exact(self) -> None:
        with self.services.unit_of_work() as uow:
            before = uow.audit.count()
            for index in range(3):
                uow.audit.record(action="a.one", entity="thing", entity_id=index)
            uow.commit()
        with self.services.unit_of_work() as uow:
            self.assertEqual(uow.audit.count(), before + 3)
        # The count is a real COUNT(*), not "however many rows came back", so it
        # does not silently stop at an arbitrary page size.
        # 计数来自真正的 COUNT(*)，而不是“取回了多少行”，因此不会在某个分页上限
        # 处悄悄停止。
        self.assertEqual(self.services.ledger.stats()["audit_entries"], before + 3)


class ImportRepositoryTests(RepositoryTestCase):
    @staticmethod
    def batch() -> ImportBatch:
        return ImportBatch(
            source="bank",
            filename="statement.csv",
            checksum="checksum-1",
            imported_at=datetime(2025, 3, 1, 12, 0, tzinfo=timezone.utc),
        )

    def test_batches_fingerprints_and_counts(self) -> None:
        with self.services.unit_of_work() as uow:
            stored = uow.imports.add_batch(self.batch())
            uow.commit()
        self.assertIsNotNone(stored.id)

        with self.services.unit_of_work() as uow:
            self.assertFalse(uow.imports.has_fingerprint("fp-1"))
            uow.imports.add_fingerprint("fp-1", batch_id=stored.id, transaction_id=None)
            uow.imports.finish_batch(
                stored.id, rows_read=5, rows_imported=4, rows_skipped=1
            )
            uow.commit()

        with self.services.unit_of_work() as uow:
            self.assertTrue(uow.imports.has_fingerprint("fp-1"))
            recent = uow.imports.recent_batches(limit=1)
        self.assertEqual(recent[0].rows_read, 5)
        self.assertEqual(recent[0].rows_imported, 4)
        self.assertEqual(recent[0].rows_skipped, 1)
        self.assertEqual(recent[0].filename, "statement.csv")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

