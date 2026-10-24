"""The windowed front end of Centsible.

The window is a thin view over the exact same services the CLI uses: it builds
no business logic of its own, so every figure it shows comes from the same pure
functions as the text reports.  Tkinter ships with the standard library, which
keeps the zero-dependency promise of the project intact.

Centsible 的窗口前端。

窗口只是同一套服务的薄视图，与 CLI 使用完全相同的服务：它不自带任何业务逻辑，
因此窗口里显示的每个数字都来自与文本报表相同的纯函数。tkinter 属于标准库，
项目的零依赖承诺因此不受影响。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Callable, Literal

from centsible.bootstrap import build_services
from centsible.config import Config
from centsible.domain.money import Money
from centsible.domain.periods import parse_date
from centsible.errors import CentsibleError, ConfigurationError
from centsible.interface.cli import formatting
from centsible.interface.cli.parsing import parse_posting_specs

__all__ = ["CentsibleApp", "run_gui"]

#: The heading anchors ``ttk.Treeview.column`` accepts.
#: ``ttk.Treeview.column`` 接受的表头对齐方式。
Anchor = Literal["nw", "n", "ne", "w", "center", "e", "sw", "s", "se"]


def run_gui(config: Config) -> int:
    """Open the window and run the Tk event loop until it closes.

    打开窗口并运行 Tk 事件循环，直到窗口关闭。"""

    root = _create_root()
    try:
        services = build_services(config)
    except CentsibleError:
        root.destroy()
        raise
    CentsibleApp(root, services, config)
    root.mainloop()
    return 0


def _create_root() -> tk.Tk:
    """Create the Tk root, or explain why no window can be opened.

    创建 Tk 根窗口；无法打开窗口时给出说明。"""

    try:
        return tk.Tk()
    except tk.TclError as exc:
        raise ConfigurationError(
            "the windowed interface needs a graphical display and Tk could not "
            "connect to one; use the command line interface instead"
        ) from exc


class CentsibleApp:
    """The main window: overview, journal, entry form and reports tabs.

    主窗口：总览、凭证、录入与报表四个页签。"""

    def __init__(self, root: tk.Tk, services: Any, config: Config) -> None:
        self.root = root
        self.services = services
        self.config = config
        self.settings = services.settings
        self._closed = False
        self._build()

    # ------------------------------------------------------------------- build
    # ------------------------------------------------------------------- 搭建界面
    def _build(self) -> None:
        self.root.title(f"Centsible - {self.services.database.display_path}")
        self.root.geometry("960x600")
        self.root.minsize(760, 480)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=6, pady=6)
        self._build_overview(notebook)
        self._build_journal(notebook)
        self._build_entry(notebook)
        self._build_reports(notebook)

        self.status = ttk.Label(self.root, text="就绪", anchor="w")
        self.status.pack(fill="x", padx=8, pady=(0, 4))
        self.refresh()
        self._journal_refresh()

    def close(self) -> None:
        """Close services once and destroy the window.

        只关闭一次服务并销毁窗口。"""

        if self._closed:
            return
        self._closed = True
        self.services.close()
        self.root.destroy()

    # ------------------------------------------------------------------- 总览
    # ------------------------------------------------------------------- overview
    def _build_overview(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="总览 Overview")

        self.overview_labels = ttk.Label(tab, text="", justify="left")
        self.overview_labels.pack(fill="x")

        self.overview_tree = _Table(
            tab,
            (
                ("code", 90, "w"),
                ("科目", 220, "w"),
                ("debit", 110, "e"),
                ("credit", 110, "e"),
                ("balance", 110, "e"),
            ),
        )
        self.overview_tree.pack(fill="both", expand=True, pady=(8, 4))
        ttk.Button(tab, text="刷新", command=self.refresh).pack(anchor="e")

    # ------------------------------------------------------------------- 凭证
    # ------------------------------------------------------------------- journal
    def _build_journal(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="凭证 Journal")

        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        ttk.Label(bar, text="状态").pack(side="left")
        self.journal_status = ttk.Combobox(
            bar, state="readonly", values=("全部", "posted", "void"), width=8
        )
        self.journal_status.set("全部")
        self.journal_status.pack(side="left", padx=(4, 10))
        ttk.Label(bar, text="搜索").pack(side="left")
        self.journal_text = ttk.Entry(bar, width=28)
        self.journal_text.pack(side="left", padx=4)
        ttk.Button(bar, text="查询", command=self._journal_refresh).pack(side="left")
        ttk.Button(bar, text="刷新", command=self._journal_refresh).pack(side="left")

        self.journal_tree = _Table(
            tab,
            (
                ("id", 50, "e"),
                ("日期", 90, "w"),
                ("摘要", 220, "w"),
                ("收款方", 140, "w"),
                ("金额", 110, "e"),
                ("状态", 60, "w"),
            ),
        )
        self.journal_tree.pack(fill="both", expand=True, pady=(8, 4))
        self.journal_tree.tree.bind("<Double-1>", lambda _event: self._journal_detail())

        actions = ttk.Frame(tab)
        actions.pack(fill="x")
        ttk.Button(actions, text="明细", command=self._journal_detail).pack(side="left")
        ttk.Button(actions, text="作废", command=self._journal_void).pack(side="left", padx=6)
        ttk.Button(actions, text="恢复", command=self._journal_restore).pack(side="left")

    def _journal_refresh(self) -> None:
        status = self.journal_status.get()
        status_value = None if status == "全部" else status
        try:
            transactions = self._guard(
                lambda: self.services.ledger.search(
                    status=status_value,
                    text=self.journal_text.get().strip() or None,
                    limit=None,
                )
            )
        except CentsibleError as exc:
            messagebox.showerror("Centsible", str(exc))
            return
        _clear(self.journal_tree.tree)
        for transaction in transactions:
            self.journal_tree.tree.insert(
                "",
                "end",
                iid=str(transaction.id),
                values=(
                    transaction.id,
                    transaction.date.isoformat(),
                    transaction.description,
                    transaction.payee,
                    self._money(self.settings.money(transaction.total_debits)),
                    formatting.status_text(transaction.status),
                ),
            )

    def _selected_id(self) -> int | None:
        selected = self.journal_tree.tree.selection()
        if not selected:
            messagebox.showinfo("Centsible", "先在列表中选择一张凭证。")
            return None
        return int(selected[0])

    def _journal_detail(self) -> None:
        transaction_id = self._selected_id()
        if transaction_id is None:
            return
        transaction = self._guard(
            lambda: self.services.ledger.get(transaction_id)
        )
        if transaction is None:
            return
        window = tk.Toplevel(self.root)
        window.title(f"凭证 #{transaction.id} Transaction #{transaction.id}")
        window.geometry("560x360")
        window.transient(self.root)
        header = ttk.Label(
            window,
            text=(
                f"{transaction.date.isoformat()}  {transaction.description}"
                f"{'  /  ' + transaction.payee if transaction.payee else ''}"
            ),
            font=("TkDefaultFont", 10, "bold"),
        )
        header.pack(anchor="w", padx=10, pady=(10, 4))
        tree = _Table(
            window,
            (("科目", 140, "w"), ("金额", 120, "e"), ("摘要 memo", 220, "w")),
        )
        tree.pack(fill="both", expand=True, padx=10, pady=6)
        for posting in transaction.postings:
            tree.tree.insert(
                "",
                "end",
                values=(
                    posting.account_code,
                    self._money(self.settings.money(posting.amount)),
                    posting.memo,
                ),
            )
        ttk.Button(window, text="关闭", command=window.destroy).pack(pady=(0, 8))

    def _journal_void(self) -> None:
        transaction_id = self._selected_id()
        if transaction_id is None:
            return
        reason = simpledialog.askstring(
            "作废 Void", "作废原因（必填，写入审计轨迹）：", parent=self.root
        )
        if not reason:
            return
        try:
            self.services.ledger.void(transaction_id, reason=reason)
        except CentsibleError as exc:
            messagebox.showerror("Centsible", str(exc))
            return
        self._journal_refresh()
        self.refresh()
        self.status.config(text=f"已作废凭证 #{transaction_id}")

    def _journal_restore(self) -> None:
        transaction_id = self._selected_id()
        if transaction_id is None:
            return
        try:
            self.services.ledger.restore(transaction_id)
        except CentsibleError as exc:
            messagebox.showerror("Centsible", str(exc))
            return
        self._journal_refresh()
        self.refresh()
        self.status.config(text=f"已恢复凭证 #{transaction_id}")

    # ------------------------------------------------------------------- 录入
    # ------------------------------------------------------------------- entry
    def _build_entry(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="录入 New entry")

        form = ttk.Frame(tab)
        form.pack(fill="x")
        self.entry_date = _field(form, "日期", row=0)
        self.entry_date.insert(0, self.services.clock.today().isoformat())
        self.entry_description = _field(form, "摘要", row=1)
        self.entry_payee = _field(form, "收款方", row=2)

        ttk.Label(tab, text="分录（每行一条：ACCOUNT=AMOUNT[:MEMO]，正=借 负=贷）").pack(anchor="w", pady=(10, 2))
        self.entry_postings = tk.Text(tab, height=8, font=("Consolas", 10))
        self.entry_postings.pack(fill="x")
        ttk.Button(tab, text="保存", command=self._save_entry).pack(anchor="e", pady=6)

    def _save_entry(self) -> None:
        lines = [
            line.strip()
            for line in self.entry_postings.get("1.0", "end").splitlines()
            if line.strip()
        ]
        if not lines:
            messagebox.showinfo("Centsible", "至少写一行分录。")
            return
        try:
            day = parse_date(
                self.entry_date.get().strip(),
                date_format=self.config.date_format,
            )
            postings = parse_posting_specs(
                postings=lines, exponent=self.settings.exponent
            )
            transaction = self.services.ledger.record(
                date=day,
                description=self.entry_description.get().strip() or "(untitled)",
                postings=postings,
                payee=self.entry_payee.get().strip(),
            )
        except CentsibleError as exc:
            messagebox.showerror("Centsible", str(exc))
            return
        self.entry_description.delete(0, "end")
        self.entry_payee.delete(0, "end")
        self.entry_postings.delete("1.0", "end")
        self._journal_refresh()
        self.refresh()
        self.status.config(text=f"已保存凭证 #{transaction.id}")

    # ------------------------------------------------------------------- 报表
    # ------------------------------------------------------------------- reports
    def _build_reports(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook, padding=8)
        notebook.add(tab, text="报表 Reports")

        bar = ttk.Frame(tab)
        bar.pack(fill="x")
        ttk.Label(bar, text="报表").pack(side="left")
        self.report_kind = ttk.Combobox(
            bar,
            state="readonly",
            width=26,
            values=("试算平衡表", "利润表（本月）", "资产负债表", "月度汇总（6 个月）"),
        )
        self.report_kind.set("试算平衡表")
        self.report_kind.pack(side="left", padx=6)
        ttk.Button(bar, text="生成", command=self._report_refresh).pack(side="left")

        self.report_title = ttk.Label(tab, text="")
        self.report_title.pack(anchor="w", pady=(8, 2))
        self.report_tree = _Table(
            tab,
            (("code", 90, "w"), ("项目", 260, "w"), ("金额", 130, "e")),
        )
        self.report_tree.pack(fill="both", expand=True, pady=(0, 4))

    def _report_refresh(self) -> None:
        kind = self.report_kind.get()
        try:
            if kind == "试算平衡表":
                self._report_trial_balance()
            elif kind == "利润表（本月）":
                self._report_income()
            elif kind == "资产负债表":
                self._report_balance_sheet()
            else:
                self._report_activity()
        except CentsibleError as exc:
            messagebox.showerror("Centsible", str(exc))

    def _report_trial_balance(self) -> None:
        report = self.services.reports.trial_balance()
        self._show_report(
            (
                ("code", 90, "w"),
                ("科目", 220, "w"),
                ("借方 debit", 110, "e"),
                ("贷方 credit", 110, "e"),
                ("余额 balance", 110, "e"),
            ),
            f"试算平衡表 Trial balance - {report.currency}",
            [
                (row.code, row.name, self._money(row.debit), self._money(row.credit), self._money(row.balance))
                for row in report.rows
            ],
        )

    def _report_income(self) -> None:
        report = self.services.reports.income_statement()
        rows: list[tuple[Any, ...]] = [
            ("", f"收入 Income - {report.currency}", ""),
            *(
                (item.code, item.name, self._money(item.amount))
                for item in report.revenue.items
            ),
            ("", "收入合计", self._money(report.revenue.total)),
            ("", f"费用 Expenses - {report.currency}", ""),
            *(
                (item.code, item.name, self._money(item.amount))
                for item in report.expenses.items
            ),
            ("", "费用合计", self._money(report.expenses.total)),
            ("", "净利润 Net income", self._money(report.net_income)),
        ]
        self._show_report(
            (("code", 90, "w"), ("项目", 260, "w"), ("金额", 130, "e")),
            f"利润表 Income statement - {report.period.start.isoformat()} .. {report.period.end.isoformat()}",
            rows,
        )

    def _report_balance_sheet(self) -> None:
        report = self.services.reports.balance_sheet()
        rows: list[tuple[Any, ...]] = [
            ("", f"资产 Assets - {report.currency}", ""),
            *(
                (item.code, item.name, self._money(item.amount))
                for item in report.assets.items
            ),
            ("", "资产合计", self._money(report.assets.total)),
            ("", f"负债 Liabilities - {report.currency}", ""),
            *(
                (item.code, item.name, self._money(item.amount))
                for item in report.liabilities.items
            ),
            ("", "负债合计", self._money(report.liabilities.total)),
            ("", f"权益 Equity - {report.currency}", ""),
            *(
                (item.code, item.name, self._money(item.amount))
                for item in report.equity.items
            ),
            ("", "权益合计", self._money(report.equity.total)),
        ]
        self._show_report(
            (("code", 90, "w"), ("项目", 260, "w"), ("金额", 130, "e")),
            f"资产负债表 Balance sheet - as of {report.as_of.isoformat()}",
            rows,
        )

    def _report_activity(self) -> None:
        report = self.services.reports.activity(months=6)
        rows = [
            (row.month, "收入 Income", self._money(row.income))
            for row in report.months
        ]
        rows += [
            (row.month, "费用 Expenses", self._money(row.expenses))
            for row in report.months
        ]
        rows += [
            (row.month, "净额 Net", self._money(row.net)) for row in report.months
        ]
        title = (
            f"月度汇总 Activity - {report.months[0].month} .. {report.months[-1].month}"
            if report.months
            else "月度汇总 Activity - 无数据"
        )
        self._show_report(
            (("code", 90, "w"), ("项目", 260, "w"), ("金额", 130, "e")),
            title,
            rows,
        )

    def _show_report(
        self,
        columns: tuple[tuple[str, int, Anchor], ...],
        title: str,
        rows: list[tuple[Any, ...]],
    ) -> None:
        self.report_title.config(text=title)
        tree = self.report_tree.tree
        tree.config(columns=[item[0] for item in columns])
        for key, width, anchor in columns:
            tree.heading(key, text=key)
            tree.column(key, width=width, anchor=anchor, stretch=True)
        _clear(tree)
        for row in rows:
            tree.insert("", "end", values=row)

    # ------------------------------------------------------------------ shared
    # ------------------------------------------------------------------ 共用
    def refresh(self) -> None:
        """Rebuild the overview: statistics plus the trial balance.

        重建总览：统计信息与试算平衡表。"""

        stats = self._guard(self.services.ledger.stats)
        if stats is None:
            return
        self.overview_labels.config(
            text=(
                f"科目 {stats['accounts']}    "
                f"凭证 {stats['transactions']}（已过账 {stats['posted_transactions']}）    "
                f"审计记录 {stats['audit_entries']}\n"
                f"期间：{stats['first_transaction'] or '-'} .. {stats['last_transaction'] or '-'}    "
                f"关账至：{stats['closed_through'] or '未关账'}    "
                f"本位币：{stats['ledger_currency'] or '?'}"
            )
        )
        trial = self._guard(self.services.reports.trial_balance)
        _clear(self.overview_tree.tree)
        if trial is None:
            return
        for row in trial.rows:
            self.overview_tree.tree.insert(
                "",
                "end",
                values=(
                    row.code,
                    row.name,
                    self._money(row.debit),
                    self._money(row.credit),
                    self._money(row.balance),
                ),
            )

    @staticmethod
    def _money(value: Money) -> str:
        return formatting.money(value)

    @staticmethod
    def _guard(action: Callable[[], Any]) -> Any:
        """Run a service call and turn its errors into a message box.

        执行一次服务调用，并把错误转换成弹窗。"""

        try:
            return action()
        except CentsibleError as exc:
            messagebox.showerror("Centsible", str(exc))
            return None


# --------------------------------------------------------------------- helpers
# --------------------------------------------------------------------- 辅助函数
def _field(parent: tk.Misc, label: str, *, row: int) -> ttk.Entry:
    """A labelled entry on a grid row; returns the entry widget.

    网格某一行上的带标签输入框；返回该输入框组件。"""

    ttk.Label(parent, text=label, width=8, anchor="e").grid(
        row=row, column=0, sticky="e", pady=2
    )
    entry = ttk.Entry(parent, width=46)
    entry.grid(row=row, column=1, sticky="we", pady=2)
    parent.columnconfigure(1, weight=1)
    return entry


class _Table(ttk.Frame):
    """A frame that owns a scrollable, read only :class:`ttk.Treeview`.

    The frame is what gets packed; the tree itself lives in ``.tree``, because a
    widget can only be managed by one geometry manager at a time.

    拥有一个带滚动条、只读 :class:`ttk.Treeview` 的容器。

    被 pack 的是这个容器；树本体在 ``.tree`` 属性里——因为一个组件同一时间只能
    由一个几何管理器管理。
    """

    def __init__(
        self, parent: tk.Misc, columns: tuple[tuple[str, int, Anchor], ...]
    ) -> None:
        super().__init__(parent)
        self.tree = ttk.Treeview(
            self, columns=[item[0] for item in columns], show="headings"
        )
        for key, width, anchor in columns:
            self.tree.heading(key, text=key)
            self.tree.column(key, width=width, anchor=anchor, stretch=True)
        ybar = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)


def _clear(tree: ttk.Treeview) -> None:
    tree.delete(*tree.get_children())
