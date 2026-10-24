"""Tests for the chart of accounts.

科目表的测试。"""

from __future__ import annotations

import unittest
from decimal import Decimal

from centsible.domain.accounts import Account, AccountType, normalize_code, sort_accounts
from centsible.errors import ValidationError


class AccountTypeTests(unittest.TestCase):
    def test_parse_accepts_aliases_and_case(self) -> None:
        self.assertIs(AccountType.parse("Asset"), AccountType.ASSET)
        self.assertIs(AccountType.parse("liabilities"), AccountType.LIABILITY)
        self.assertIs(AccountType.parse("revenue"), AccountType.INCOME)
        self.assertIs(AccountType.parse(AccountType.EXPENSE), AccountType.EXPENSE)

    def test_parse_rejects_unknown(self) -> None:
        with self.assertRaises(ValidationError) as caught:
            AccountType.parse("profit")
        self.assertIn("not an account type", str(caught.exception))

    def test_normal_balance_sides(self) -> None:
        self.assertEqual(AccountType.ASSET.normal_balance, "debit")
        self.assertEqual(AccountType.EXPENSE.normal_balance, "debit")
        self.assertEqual(AccountType.LIABILITY.normal_balance, "credit")
        self.assertEqual(AccountType.EQUITY.normal_balance, "credit")
        self.assertEqual(AccountType.INCOME.normal_balance, "credit")

    def test_normal_sign_matches_normal_balance(self) -> None:
        for kind in AccountType:
            with self.subTest(kind=kind):
                expected = 1 if kind.normal_balance == "debit" else -1
                self.assertEqual(kind.normal_sign, expected)

    def test_statement_grouping(self) -> None:
        self.assertTrue(AccountType.ASSET.is_balance_sheet)
        self.assertFalse(AccountType.INCOME.is_balance_sheet)
        self.assertTrue(AccountType.EXPENSE.is_income_statement)

    def test_natural_balance_flips_credit_accounts(self) -> None:
        self.assertEqual(AccountType.ASSET.natural_balance(Decimal("100")), Decimal("100"))
        self.assertEqual(AccountType.LIABILITY.natural_balance(Decimal("-100")), Decimal("100"))
        self.assertEqual(AccountType.INCOME.natural_balance(Decimal("-100")), Decimal("100"))
        self.assertEqual(AccountType.EXPENSE.natural_balance(Decimal("100")), Decimal("100"))


class AccountTests(unittest.TestCase):
    def test_code_is_canonicalised(self) -> None:
        account = Account(code=" cash ", name="Cash", type=AccountType.ASSET)
        self.assertEqual(account.code, "CASH")

    def test_invalid_codes(self) -> None:
        for code in ("", "  ", "-1000", "1 000", "cash!", "A" * 40):
            with self.subTest(code=code), self.assertRaises(ValidationError):
                Account(code=code, name="X", type=AccountType.ASSET)

    def test_name_is_required_and_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            Account(code="1000", name="   ", type=AccountType.ASSET)
        with self.assertRaises(ValidationError):
            Account(code="1000", name="x" * 200, type=AccountType.ASSET)

    def test_self_parent_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            Account(
                code="1000", name="Cash", type=AccountType.ASSET, parent_code="1000"
            )

    def test_type_accepts_strings(self) -> None:
        account = Account(code="1000", name="Cash", type="asset")  # type: ignore[arg-type]
        self.assertIs(account.type, AccountType.ASSET)

    def test_label_and_serialisation(self) -> None:
        account = Account(code="1000", name="Cash", type=AccountType.ASSET)
        self.assertEqual(account.label, "1000 Cash")
        self.assertEqual(
            account.to_dict(),
            {
                "id": None,
                "code": "1000",
                "name": "Cash",
                "type": "asset",
                "parent_code": None,
                "description": "",
                "archived": False,
            },
        )

    def test_with_changes_revalidates(self) -> None:
        account = Account(code="1000", name="Cash", type=AccountType.ASSET)
        renamed = account.with_changes(name="Petty cash")
        self.assertEqual(renamed.name, "Petty cash")
        self.assertEqual(account.name, "Cash")
        with self.assertRaises(ValidationError):
            account.with_changes(parent_code="1000")

    def test_natural_balance_helpers(self) -> None:
        income = Account(code="4000", name="Sales", type=AccountType.INCOME)
        self.assertEqual(income.natural_balance(Decimal("-50")), Decimal("50"))
        self.assertEqual(income.normal_sign, -1)
        self.assertFalse(income.is_balance_sheet)


class HelperTests(unittest.TestCase):
    def test_normalize_code(self) -> None:
        self.assertEqual(normalize_code(" a-1_b.2 "), "A-1_B.2")
        with self.assertRaises(ValidationError):
            normalize_code("")

    def test_sort_accounts_is_stable_by_code(self) -> None:
        accounts = [
            Account(code="3000", name="Equity", type=AccountType.EQUITY),
            Account(code="1000", name="Cash", type=AccountType.ASSET),
        ]
        self.assertEqual([account.code for account in sort_accounts(accounts)], ["1000", "3000"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
