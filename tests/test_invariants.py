"""Randomised invariant tests for the double-entry engine.

Hand written fixtures only cover the situations their author thought of.  This
module generates a few hundred balanced transactions with a fixed seed - spread
over two fiscal years, every account type, some of them voided - and then
re-derives the accounting invariants from the stored rows alone.  If any of them
ever disagree, either the reports or the storage layer lost a cent somewhere.

手写用例只能覆盖作者想得到的情况。本模块用固定随机种子生成几百笔平衡凭证——跨越
两个会计年度、涵盖每种科目类型，其中一部分被作废——然后仅从已存储的数据行出发重新
推导各项会计不变量。它们一旦对不上，就说明报表层或存储层在某处丢掉了一分钱。

The seed is fixed, so a failure is reproducible; the assertions are properties, so
they do not need updating when the demo chart or the fixtures change.  The ledger
is built once for the whole class, because it is read-only for every test after
setup.

随机种子是固定的，因此失败可以复现；断言写的是性质而不是具体数字，所以更换演示
科目表或用例数据时都不需要跟着改。整套用例共用一个账簿，因为除初始化之外它们只读。

"""

from __future__ import annotations

import random
import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from centsible.bootstrap import build_services
from centsible.config import Config
from centsible.domain.accounts import AccountType
from centsible.domain.periods import DatePeriod
from centsible.domain.transactions import Posting
from centsible.infrastructure.clock import FixedClock
from tests.support import DEFAULT_TODAY

# Accounts that exist in the default chart, spread over every account type.
# 默认科目表中确实存在的科目，覆盖每一种科目类型。
POOL = ("1010", "1200", "1500", "2100", "2200", "3000", "4000", "5100", "5500", "5900")

# First day the generator may use.
# 生成器可以使用的最早日期。
FIRST_DAY = date(2024, 1, 1)

# How many days of history the generator spreads entries over.
# 生成器把凭证铺开多少天的历史。
SPAN_DAYS = 435


class RandomisedInvariantTests(unittest.TestCase):
    """A ledger full of random but valid entries still obeys the rules.

    装满随机但合法的凭证的账簿，依然遵守全部规则。
    """

    entries = 120
    seed = 20250315
    void_ratio = 0.12

    @classmethod
    def setUpClass(cls) -> None:
        cls._tempdir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tempdir.cleanup)
        cls.services = build_services(
            Config(
                database_path=Path(cls._tempdir.name) / "ledger.db",
                currency="CNY",
            ),
            clock=FixedClock(DEFAULT_TODAY),
        )
        cls.addClassCleanup(cls.services.close)
        cls.services.accounts.ensure_chart("default")

        cls.random = random.Random(cls.seed)
        cls.voided_ids: list[int] = []
        for index in range(cls.entries):
            transaction = cls._record_random(index)
            if transaction.id is None:  # pragma: no cover - defensive
                continue
            if cls.random.random() < cls.void_ratio:
                cls.services.ledger.void(transaction.id, reason="randomised test void")
                cls.voided_ids.append(transaction.id)

    # --------------------------------------------------------------- generator
    # --------------------------------------------------------------- 生成器
    @classmethod
    def _random_day(cls) -> date:
        return FIRST_DAY + timedelta(days=cls.random.randrange(0, SPAN_DAYS))

    @classmethod
    def _amount(cls) -> Decimal:
        return Decimal(cls.random.randrange(50, 250_000)) / 100

    @classmethod
    def _postings(cls) -> tuple[Posting, ...]:
        codes = cls.random.sample(POOL, cls.random.randrange(2, 5))
        amounts: list[Decimal] = []
        for _ in codes[:-1]:
            value = cls._amount()
            amounts.append(-value if cls.random.random() < 0.5 else value)
        # The last leg always balances the entry, and must not be zero.
        # 最后一笔永远用来配平这张凭证，且不能是零。
        missing = -sum(amounts, Decimal(0))
        while missing == 0:
            extra = cls._amount()
            amounts[0] += extra
            missing = -sum(amounts, Decimal(0))
        amounts.append(missing)
        return tuple(Posting(code, amount) for code, amount in zip(codes, amounts))

    @classmethod
    def _record_random(cls, index: int):
        return cls.services.ledger.record(
            date=cls._random_day(),
            description=f"Random entry {index}",
            postings=cls._postings(),
        )

    # ---------------------------------------------------------------- helpers
    # ---------------------------------------------------------------- 辅助方法
    def _posted_postings(self) -> list[tuple[str, Decimal]]:
        """Every (account, amount) pair that still counts, from the rows alone.

        仍然生效的每一对（科目, 金额），完全从数据行重新推导。
        """
        collected: list[tuple[str, Decimal]] = []
        for transaction in self.services.ledger.search(limit=None):
            if transaction.is_void:
                continue
            for posting in transaction.postings:
                collected.append((posting.account_code, posting.amount))
        return collected

    # ------------------------------------------------------------- assertions
    # ------------------------------------------------------------- 断言
    def test_the_generator_produced_a_meaningful_ledger(self) -> None:
        stats = self.services.ledger.stats()
        self.assertEqual(stats["transactions"], self.entries)
        self.assertGreater(len(self.voided_ids), 0)
        self.assertLess(len(self.voided_ids), self.entries)
        self.assertEqual(
            stats["posted_transactions"], self.entries - len(self.voided_ids)
        )

    def test_every_stored_transaction_is_balanced(self) -> None:
        for transaction in self.services.ledger.search(limit=None):
            with self.subTest(transaction=transaction.id):
                self.assertEqual(
                    sum(
                        (posting.amount for posting in transaction.postings),
                        Decimal(0),
                    ),
                    Decimal(0),
                )

    def test_trial_balance_balances(self) -> None:
        trial = self.services.reports.trial_balance()
        self.assertTrue(trial.balanced)
        self.assertEqual(trial.total_debit, trial.total_credit)

        postings = self._posted_postings()
        debit_total = sum((amount for _, amount in postings if amount > 0), Decimal(0))
        credit_total = sum((amount for _, amount in postings if amount < 0), Decimal(0))
        self.assertEqual(trial.total_debit.amount, debit_total)
        self.assertEqual(trial.total_credit.amount, -credit_total)
        self.assertEqual(debit_total + credit_total, Decimal(0))

    def test_balance_sheet_balances(self) -> None:
        sheet = self.services.reports.balance_sheet()
        self.assertTrue(sheet.balanced)
        self.assertEqual(
            sheet.assets.total.amount,
            sheet.liabilities.total.amount + sheet.equity.total.amount,
        )
        # Equity holds the real equity accounts plus the two synthetic earnings
        # lines, so the section total must cover all of them.
        # 权益部分由真实权益科目加上两条合成的利润行组成，因此该部分合计必须涵盖
        # 它们全部。
        real_equity = sum(
            (item.amount.amount for item in sheet.equity.items if item.code), Decimal(0)
        )
        self.assertEqual(
            sheet.equity.total.amount,
            real_equity + sheet.retained_earnings.amount + sheet.current_earnings.amount,
        )

    def test_account_balances_match_an_independent_recomputation(self) -> None:
        expected: dict[str, Decimal] = {}
        for code, amount in self._posted_postings():
            expected[code] = expected.get(code, Decimal(0)) + amount

        for code in POOL:
            with self.subTest(account=code):
                account = self.services.accounts.require(code)
                natural = account.natural_balance(expected.get(code, Decimal(0)))
                self.assertEqual(
                    self.services.reports.account_balance(code).amount, natural
                )

    def test_voided_entries_are_kept_but_excluded(self) -> None:
        for transaction_id in self.voided_ids:
            with self.subTest(transaction=transaction_id):
                stored = self.services.ledger.get(transaction_id)
                self.assertTrue(stored.is_void)
                self.assertTrue(stored.void_reason)
        # Voiding never deletes: the rows are still there for the audit trail.
        # 作废从不删除：为审计轨迹保留原始数据行。
        self.assertEqual(len(self.services.ledger.search(limit=None)), self.entries)
        posted = self.services.ledger.search(status="posted", limit=None)
        self.assertEqual(len(posted), self.entries - len(self.voided_ids))

    def test_income_statement_agrees_with_the_monthly_activity_report(self) -> None:
        period = DatePeriod.for_year(2024)
        statement = self.services.reports.income_statement(period)
        activity = self.services.reports.activity(months=12, until=date(2024, 12, 31))

        self.assertEqual(statement.revenue.total.amount, activity.total_income.amount)
        self.assertEqual(statement.expenses.total.amount, activity.total_expenses.amount)
        self.assertEqual(statement.net_income.amount, activity.total_net.amount)
        self.assertEqual(
            statement.revenue.total.amount - statement.expenses.total.amount,
            statement.net_income.amount,
        )

    def test_locking_a_period_does_not_change_reported_figures(self) -> None:
        before = self.services.reports.balance_sheet()
        income_before = self.services.reports.income_statement(DatePeriod.for_year(2024))

        self.services.ledger.set_lock_date(date(2024, 12, 31))
        self.addCleanup(self.services.ledger.unlock, force=True)

        after = self.services.reports.balance_sheet()
        income_after = self.services.reports.income_statement(DatePeriod.for_year(2024))
        self.assertEqual(before.assets.total, after.assets.total)
        self.assertEqual(before.total_liabilities_and_equity, after.total_liabilities_and_equity)
        self.assertEqual(income_before.net_income, income_after.net_income)

    def test_the_journal_export_covers_every_posted_posting(self) -> None:
        export = self.services.exports.journal(include_void=False)
        self.assertEqual(len(export.rows), len(self._posted_postings()))
        self.assertEqual(
            export.transaction_count, self.entries - len(self.voided_ids)
        )

    def test_the_database_is_healthy(self) -> None:
        self.assertEqual(self.services.database.integrity_check(), [])

    def test_every_account_type_appears_in_the_ledger(self) -> None:
        used = {code for code, _amount in self._posted_postings()}
        types = {self.services.accounts.require(code).type for code in used}
        self.assertEqual(types, set(AccountType))


if __name__ == "__main__":  # pragma: no cover - manual runs
    unittest.main()
