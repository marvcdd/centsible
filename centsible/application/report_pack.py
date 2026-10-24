"""The multi-sheet Excel report pack.

``export workbook`` produces one ``.xlsx`` file with the whole picture of the
ledger: a summary sheet, monthly activity, the three financial statements, the
journal, budgets, recurring rules and the chart of accounts.  One file, ready to
print, email or archive - which is exactly what spreadsheets are good at.

The pack is assembled from the *same* report DTOs the CLI prints, so the numbers
in Excel and on screen can never disagree: both come from the pure builders in
:mod:`centsible.application.reporting`.

多工作表的 Excel 报表包。

``export workbook`` 生成一个 ``.xlsx`` 文件，呈现账簿的全貌：一张汇总工作表、
月度活动、三张财务报表、日记账、预算、周期规则以及科目表。一个文件，即可打印、
发送邮件或归档——这正是电子表格所擅长的事情。

报表包由 CLI 打印的*同一批*报表 DTO 装配而成，因此 Excel 里的数字与屏幕上显示
的数字永远不会互相矛盾：两者都出自 :mod:`centsible.application.reporting` 里的纯
构建函数。
"""

from __future__ import annotations

from datetime import date

from centsible.application.base import ServiceBase
from centsible.application.budget_service import BudgetService
from centsible.application.export_service import ExportService
from centsible.application.recurring_service import RecurringService
from centsible.application.report_service import ReportService
from centsible.application.workbook import CellValue, Column, ColumnKind, Sheet, Workbook
from centsible.domain.periods import DatePeriod
from centsible.errors import ValidationError

__all__ = ["ReportPackService"]


class ReportPackService(ServiceBase):
    """Assemble the reporting pack for a period.

    为一个会计期间装配报表包。"""

    def __init__(
        self,
        uow_factory,
        clock,
        settings=None,
        *,
        reports: ReportService,
        budgets: BudgetService,
        recurring: RecurringService,
        exports: ExportService,
    ) -> None:
        super().__init__(uow_factory, clock, settings)
        self._reports = reports
        self._budgets = budgets
        self._recurring = recurring
        self._exports = exports

    # ------------------------------------------------------------------- build
    # ------------------------------------------------------------------- 构建
    def build(
        self,
        period: DatePeriod | None = None,
        *,
        as_of: date | None = None,
        months: int = 12,
    ) -> Workbook:
        if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 120:
            raise ValidationError("months must be between 1 and 120", field="months")

        effective = period or self._reports.current_month()
        day = as_of or (
            effective.end if effective.end != date.max else self._clock.today()
        )
        currency = self._settings.currency

        trial = self._reports.trial_balance(effective)
        income = self._reports.income_statement(effective)
        sheet = self._reports.balance_sheet(day)
        activity = self._reports.activity(months, until=day)
        budgets = self._budgets.report(
            effective
            if _bounded(effective)
            else DatePeriod.for_month(day.year, day.month)
        )
        rules = self._recurring.list()
        transactions = self._exports.ordered_transactions(effective)
        all_time_balances = {
            row.code: row.balance.amount
            for row in self._reports.trial_balance().rows
        }
        with self._uow_factory() as uow:
            accounts = uow.accounts.list(include_archived=True)

        posted = sum(1 for transaction in transactions if not transaction.is_void)

        return Workbook(
            title="Centsible report pack",
            metadata=(
                ("generated", self._clock.now().isoformat(timespec="seconds")),
                ("period", effective.label),
                ("as_of", day.isoformat()),
                ("currency", currency),
            ),
            sheets=(
                self._summary_sheet(day, effective, len(accounts), len(transactions),
                                    posted, trial.balanced, sheet.balanced,
                                    income.net_income.amount,
                                    sheet.assets.total.amount,
                                    sheet.liabilities.total.amount,
                                    sheet.equity.total.amount),
                _activity_sheet(activity),
                _trial_balance_sheet(trial),
                _income_sheet(income),
                _balance_sheet_sheet(sheet),
                _budget_sheet(budgets),
                _journal_sheet(transactions, effective),
                _recurring_sheet(rules),
                _accounts_sheet(accounts, all_time_balances),
            ),
        )

    # ---------------------------------------------------------------- summary
    # ---------------------------------------------------------------- 汇总工作表
    def _summary_sheet(
        self,
        as_of: date,
        period: DatePeriod,
        account_count: int,
        transaction_count: int,
        posted_count: int,
        trial_balanced: bool,
        sheet_balanced: bool,
        net_income,
        assets,
        liabilities,
        equity,
    ) -> Sheet:
        rows = (
            ("Generated", self._clock.now().isoformat(timespec="seconds")),
            ("Period", period.label),
            ("As of", as_of.isoformat()),
            ("Currency", self._settings.currency),
            ("Accounts", account_count),
            ("Transactions", transaction_count),
            ("Transactions (posted)", posted_count),
            ("Trial balance", "balanced" if trial_balanced else "NOT BALANCED"),
            ("Net income", net_income),
            ("Total assets", assets),
            ("Total liabilities", liabilities),
            ("Total equity", equity),
            ("Balance sheet", "balanced" if sheet_balanced else "NOT BALANCED"),
        )
        return Sheet(
            title="Summary",
            heading="Centsible report pack",
            subtitle=f"generated {self._clock.now().isoformat(timespec='seconds')}",
            columns=(
                Column("item", ColumnKind.TEXT, width=26),
                Column("value", ColumnKind.TEXT, width=44),
            ),
            rows=rows,
        )


# ------------------------------------------------------------------ builders
# ------------------------------------------------------------------ 构建函数
def _activity_sheet(activity) -> Sheet:
    return Sheet(
        title="Monthly activity",
        heading="Monthly activity",
        subtitle=f"amounts in {_currency_of(activity)}",
        columns=(
            Column("month", ColumnKind.TEXT, width=10),
            Column("income", ColumnKind.MONEY, width=14),
            Column("expenses", ColumnKind.MONEY, width=14),
            Column("net", ColumnKind.MONEY, width=14),
        ),
        rows=tuple(
            (month.month, month.income.amount, month.expenses.amount, month.net.amount)
            for month in activity.months
        ),
        footer=(
            "Total",
            activity.total_income.amount,
            activity.total_expenses.amount,
            activity.total_net.amount,
        ),
    )


def _trial_balance_sheet(trial) -> Sheet:
    return Sheet(
        title="Trial balance",
        heading="Trial balance",
        subtitle=f"{trial.period.label}  -  amounts in {trial.currency}",
        columns=(
            Column("code", ColumnKind.TEXT, width=10),
            Column("account", ColumnKind.TEXT, width=30),
            Column("type", ColumnKind.TEXT, width=10),
            Column("debit", ColumnKind.MONEY, width=14),
            Column("credit", ColumnKind.MONEY, width=14),
            Column("balance", ColumnKind.MONEY, width=14),
        ),
        rows=tuple(
            (row.code, row.name, row.type.value, row.debit.amount, row.credit.amount,
             row.balance.amount)
            for row in trial.rows
        ),
        footer=("", "", "Total", trial.total_debit.amount, trial.total_credit.amount, None),
    )


def _income_sheet(income) -> Sheet:
    rows: list[tuple[CellValue, ...]] = []
    for item in income.revenue.items:
        rows.append(("Income", item.code, item.name, item.amount.amount))
    rows.append(("Income", "", "Total income", income.revenue.total.amount))
    for item in income.expenses.items:
        rows.append(("Expenses", item.code, item.name, item.amount.amount))
    rows.append(("Expenses", "", "Total expenses", income.expenses.total.amount))
    rows.append(("", "", "Net income", income.net_income.amount))
    return Sheet(
        title="Income statement",
        heading="Income statement",
        subtitle=f"{income.period.label}  -  amounts in {income.currency}",
        columns=(
            Column("section", ColumnKind.TEXT, width=12),
            Column("code", ColumnKind.TEXT, width=10),
            Column("account", ColumnKind.TEXT, width=34),
            Column("amount", ColumnKind.MONEY, width=14),
        ),
        rows=tuple(rows),
    )


def _balance_sheet_sheet(sheet) -> Sheet:
    rows: list[tuple[CellValue, ...]] = []
    for item in sheet.assets.items:
        rows.append(("Assets", item.code, item.name, item.amount.amount))
    rows.append(("Assets", "", "Total assets", sheet.assets.total.amount))
    for item in sheet.liabilities.items:
        rows.append(("Liabilities", item.code, item.name, item.amount.amount))
    rows.append(("Liabilities", "", "Total liabilities", sheet.liabilities.total.amount))
    for item in sheet.equity.items:
        rows.append(("Equity", item.code, item.name, item.amount.amount))
    rows.append(("Equity", "", "Total equity", sheet.equity.total.amount))
    rows.append(("", "", "Liabilities + equity", sheet.total_liabilities_and_equity.amount))
    return Sheet(
        title="Balance sheet",
        heading="Balance sheet",
        subtitle=f"as of {sheet.as_of.isoformat()}  -  amounts in {sheet.currency}",
        columns=(
            Column("section", ColumnKind.TEXT, width=14),
            Column("code", ColumnKind.TEXT, width=10),
            Column("account", ColumnKind.TEXT, width=34),
            Column("amount", ColumnKind.MONEY, width=14),
        ),
        rows=tuple(rows),
        footer=("", "", "Balance sheet balanced",
                "yes" if sheet.balanced else "NO"),
    )


def _budget_sheet(budgets) -> Sheet:
    return Sheet(
        title="Budgets",
        heading="Budgets",
        subtitle=f"{budgets.period.label}  -  amounts in {budgets.currency}",
        columns=(
            Column("code", ColumnKind.TEXT, width=10),
            Column("account", ColumnKind.TEXT, width=30),
            Column("planned", ColumnKind.MONEY, width=14),
            Column("actual", ColumnKind.MONEY, width=14),
            Column("variance", ColumnKind.MONEY, width=14),
            Column("status", ColumnKind.TEXT, width=10),
        ),
        rows=tuple(
            (
                row.account.code,
                row.account.name,
                row.budgeted.amount,
                row.actual.amount,
                row.variance.amount,
                "ok" if row.favorable else "watch",
            )
            for row in budgets.rows
        ),
        footer=(
            "",
            "Total",
            budgets.total_budgeted.amount,
            budgets.total_actual.amount,
            budgets.total_variance.amount,
            None,
        ),
    )


def _journal_sheet(transactions, period: DatePeriod) -> Sheet:
    rows: list[tuple[CellValue, ...]] = []
    for transaction in transactions:
        for posting in transaction.postings:
            rows.append(
                (
                    transaction.id,
                    transaction.date,
                    transaction.description,
                    transaction.payee,
                    transaction.reference or "",
                    transaction.tag_text,
                    transaction.status.value.upper(),
                    posting.account_code,
                    posting.amount,
                    posting.memo,
                )
            )
    return Sheet(
        title="Journal",
        heading="Journal",
        subtitle=f"{period.label}",
        columns=(
            Column("id", ColumnKind.NUMBER, width=8),
            Column("date", ColumnKind.DATE, width=12),
            Column("description", ColumnKind.TEXT, width=36),
            Column("payee", ColumnKind.TEXT, width=20),
            Column("reference", ColumnKind.TEXT, width=16),
            Column("tags", ColumnKind.TEXT, width=16),
            Column("status", ColumnKind.TEXT, width=10),
            Column("account", ColumnKind.TEXT, width=12),
            Column("amount", ColumnKind.MONEY, width=14),
            Column("memo", ColumnKind.TEXT, width=24),
        ),
        rows=tuple(rows),
    )


def _recurring_sheet(rules) -> Sheet:
    return Sheet(
        title="Recurring rules",
        heading="Recurring rules",
        subtitle=f"{len(rules)} rule(s)",
        columns=(
            Column("id", ColumnKind.NUMBER, width=8),
            Column("name", ColumnKind.TEXT, width=26),
            Column("schedule", ColumnKind.TEXT, width=16),
            Column("next", ColumnKind.DATE, width=12),
            Column("ends", ColumnKind.DATE, width=12),
            Column("amount", ColumnKind.MONEY, width=14),
            Column("state", ColumnKind.TEXT, width=10),
        ),
        rows=tuple(
            (
                rule.id,
                rule.name,
                rule.schedule_label,
                rule.next_date,
                rule.end_date,
                rule.template_total,
                "active" if rule.active else "paused",
            )
            for rule in rules
        ),
    )


def _accounts_sheet(accounts, balances) -> Sheet:
    return Sheet(
        title="Accounts",
        heading="Chart of accounts",
        subtitle=f"{len(accounts)} account(s)",
        columns=(
            Column("code", ColumnKind.TEXT, width=10),
            Column("name", ColumnKind.TEXT, width=30),
            Column("type", ColumnKind.TEXT, width=10),
            Column("parent", ColumnKind.TEXT, width=10),
            Column("archived", ColumnKind.TEXT, width=10),
            Column("balance", ColumnKind.MONEY, width=14),
            Column("description", ColumnKind.TEXT, width=30),
        ),
        rows=tuple(
            (
                account.code,
                account.name,
                account.type.value,
                account.parent_code or "",
                "yes" if account.archived else "",
                balances.get(account.code),
                account.description,
            )
            for account in accounts
        ),
    )


# ------------------------------------------------------------------ helpers
# ------------------------------------------------------------------ 辅助函数
def _bounded(period: DatePeriod) -> bool:
    return period.start != date.min and period.end != date.max


def _currency_of(report) -> str:
    return getattr(report, "currency", "")
