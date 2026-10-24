"""Tests for the chart of accounts service.

科目表服务的测试。"""

from __future__ import annotations

import unittest
from datetime import date

from centsible.domain.accounts import AccountType
from centsible.errors import ConflictError, NotFoundError, ValidationError
from tests.support import LedgerTestCase


class AccountServiceTests(LedgerTestCase):
    def test_create_and_read_back(self) -> None:
        account = self.services.accounts.create(
            code="cash", name="Cash", type="asset", description="Petty cash"
        )
        self.assertEqual(account.code, "CASH")
        self.assertIsNotNone(account.id)
        self.assertEqual(self.services.accounts.get("cash").name, "Cash")
        self.assertEqual(self.services.accounts.require("CASH").description, "Petty cash")

    def test_duplicate_code_is_a_conflict(self) -> None:
        self.services.accounts.create(code="CASH", name="Cash", type="asset")
        with self.assertRaises(ConflictError) as caught:
            self.services.accounts.create(code="cash", name="Again", type="asset")
        self.assertIn("already exists", str(caught.exception))

    def test_parent_must_exist(self) -> None:
        with self.assertRaises(NotFoundError):
            self.services.accounts.create(
                code="CHILD", name="Child", type="asset", parent_code="MISSING"
            )

    def test_child_must_share_the_parent_type(self) -> None:
        self.services.accounts.create(code="HEAD", name="Head", type="asset")
        with self.assertRaises(ValidationError) as caught:
            self.services.accounts.create(
                code="BAD", name="Bad", type="expense", parent_code="HEAD"
            )
        self.assertIn("cannot be a child", str(caught.exception))

    def test_cycles_are_refused(self) -> None:
        self.services.accounts.create(code="P1", name="One", type="asset")
        self.services.accounts.create(code="P2", name="Two", type="asset", parent_code="P1")
        with self.assertRaises(ValidationError) as caught:
            self.services.accounts.update("P1", parent_code="P2")
        self.assertIn("cycle", str(caught.exception))

    def test_update_changes_presentation_fields(self) -> None:
        self.services.accounts.create(code="TEMP", name="Temp", type="asset")
        updated = self.services.accounts.update(
            "temp", name="Renamed", description="now described"
        )
        self.assertEqual(updated.name, "Renamed")
        self.assertEqual(updated.description, "now described")

    def test_update_without_changes_is_a_no_op(self) -> None:
        created = self.services.accounts.create(code="TEMP", name="Temp", type="asset")
        self.assertEqual(self.services.accounts.update("TEMP"), created)

    def test_account_cannot_be_its_own_parent(self) -> None:
        self.services.accounts.create(code="TEMP", name="Temp", type="asset")
        with self.assertRaises(ValidationError):
            self.services.accounts.update("TEMP", parent_code="TEMP")

    def test_archive_requires_a_zero_balance(self) -> None:
        self.record("Rent", ("5100", "100"), ("1010", "-100"), day=date(2025, 3, 1))
        with self.assertRaises(ConflictError) as caught:
            self.services.accounts.archive("1010")
        self.assertIn("balance", str(caught.exception))

    def test_archive_and_restore_an_unused_account(self) -> None:
        self.services.accounts.create(code="TEMP", name="Temp", type="asset")
        archived = self.services.accounts.archive("TEMP")
        self.assertTrue(archived.archived)
        with self.assertRaises(ConflictError):
            self.record("Blocked", ("TEMP", "5"), ("1000", "-5"))

        restored = self.services.accounts.archive("TEMP", archived=False)
        self.assertFalse(restored.archived)
        self.record("Allowed", ("TEMP", "5"), ("1000", "-5"))

    def test_archive_parent_with_active_child_is_refused(self) -> None:
        self.services.accounts.create(code="HEAD", name="Head", type="asset")
        self.services.accounts.create(code="LEAF", name="Leaf", type="asset", parent_code="HEAD")
        with self.assertRaises(ConflictError) as caught:
            self.services.accounts.archive("HEAD")
        self.assertIn("active child", str(caught.exception))

    def test_delete_only_works_for_unused_accounts(self) -> None:
        self.services.accounts.create(code="TEMP", name="Temp", type="asset")
        self.services.accounts.delete("TEMP")
        self.assertIsNone(self.services.accounts.get("TEMP"))

        self.record("Rent", ("5100", "100"), ("1010", "-100"))
        with self.assertRaises(ConflictError) as caught:
            self.services.accounts.delete("1010")
        self.assertIn("referenced", str(caught.exception))

    def test_delete_with_children_is_refused(self) -> None:
        self.services.accounts.create(code="HEAD", name="Head", type="asset")
        self.services.accounts.create(code="LEAF", name="Leaf", type="asset", parent_code="HEAD")
        with self.assertRaises(ConflictError):
            self.services.accounts.delete("HEAD")

    def test_tree_is_parent_before_child(self) -> None:
        self.services.accounts.create(code="HEAD", name="Head", type="asset")
        self.services.accounts.create(code="LEAF", name="Leaf", type="asset", parent_code="HEAD")
        tree = self.services.accounts.tree()
        codes = [account.code for _, account in tree]
        self.assertLess(codes.index("HEAD"), codes.index("LEAF"))
        depths = {account.code: depth for depth, account in tree}
        self.assertEqual(depths["HEAD"], 0)
        self.assertEqual(depths["LEAF"], 1)

    def test_ensure_chart_is_idempotent(self) -> None:
        self.assertEqual(self.services.accounts.ensure_chart("default"), [])
        count_before = self.services.accounts.count()
        self.assertEqual(self.services.accounts.count(), count_before)

    def test_list_hides_archived_on_request(self) -> None:
        self.services.accounts.create(code="TEMP", name="Temp", type="asset")
        self.services.accounts.archive("TEMP")
        active = {account.code for account in self.services.accounts.list(include_archived=False)}
        everything = {account.code for account in self.services.accounts.list()}
        self.assertNotIn("TEMP", active)
        self.assertIn("TEMP", everything)

    def test_require_reports_the_code(self) -> None:
        with self.assertRaises(NotFoundError) as caught:
            self.services.accounts.require("NOPE")
        self.assertIn("NOPE", str(caught.exception))

    def test_types_are_accepted_as_strings(self) -> None:
        account = self.services.accounts.create(code="REV", name="Revenue", type="Revenue")
        self.assertIs(account.type, AccountType.INCOME)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
