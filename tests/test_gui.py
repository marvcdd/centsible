"""Tests for the tkinter window (skipped when no display is available).

窗口界面（tkinter）的测试；没有可用显示环境时自动跳过。"""

from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from centsible.domain.transactions import Posting

# A Tk instance needs a display; on a headless machine this probe fails and the
# whole module is skipped instead of erroring.
# Tk 实例需要显示环境；在无头机器上这个探测会失败，此时跳过整个模块而不是报错。
HAS_TK = False
try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:  # pragma: no cover - Python built without tkinter | 未带 tkinter 的 Python
    pass
else:
    try:
        _probe = tk.Tk()
        _probe.withdraw()
        ttk.Frame(_probe).pack()
        _probe.update()
        _probe.destroy()
        del _probe
        HAS_TK = True
    except tk.TclError:  # pragma: no cover - no display | 没有显示环境
        HAS_TK = False

from centsible.bootstrap import build_services
from centsible.config import Config
from centsible.infrastructure.clock import FixedClock
from tests.support import DEFAULT_TODAY


@unittest.skipUnless(HAS_TK, "tkinter window tests need a display")
class WindowTests(unittest.TestCase):
    """The window is a thin view: what it shows must match the services.

    窗口只是薄视图：它所显示的内容必须与服务一致。
    """

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.config = Config(
            database_path=Path(self._tempdir.name) / "ledger.db", currency="CNY"
        )
        self.services = build_services(
            self.config, clock=FixedClock(DEFAULT_TODAY)
        )
        self.addCleanup(self.services.close)
        self.services.accounts.ensure_chart("default")
        self.services.ledger.record(
            date=DEFAULT_TODAY,
            description="Office rent",
            postings=(_posting("5100", "1200"), _posting("1010", "-1200")),
        )
        self.services.ledger.record(
            date=DEFAULT_TODAY,
            description="Coffee",
            postings=(_posting("5800", "45.50"), _posting("1010", "-45.50")),
        )

        # Dialogs block the interpreter; record their calls instead.
        # 对话框会阻塞解释器；改为记录调用。
        patcher = mock.patch("centsible.interface.gui.app.messagebox")
        self.messagebox = patcher.start()
        self.addCleanup(patcher.stop)

        self.root = tk.Tk()
        self.root.withdraw()
        from centsible.interface.gui.app import CentsibleApp

        self.app = CentsibleApp(self.root, self.services, self.config)
        # close() destroys the root as well; no separate destroy cleanup.
        # close() 本身会销毁根窗口；不再单独注册销毁清理。
        self.addCleanup(self.app.close)

    def test_window_lists_the_journal_and_the_trial_balance(self) -> None:
        self.assertEqual(len(self.app.journal_tree.tree.get_children()), 2)
        # The trial balance shows one row per account with activity.
        # 试算平衡表为每个有发生额的科目显示一行。
        self.assertGreaterEqual(len(self.app.overview_tree.tree.get_children()), 3)

    def test_entry_form_records_a_balanced_transaction(self) -> None:
        self.app.entry_date.delete(0, "end")
        self.app.entry_date.insert(0, DEFAULT_TODAY.isoformat())
        self.app.entry_description.insert(0, "Client payment")
        self.app.entry_postings.insert("1.0", "1010=2500\n4000=-2500")

        self.app._save_entry()

        self.assertEqual(self.services.ledger.stats()["transactions"], 3)
        self.assertIn("已保存凭证", self.app.status.cget("text"))

    def test_entry_form_rejects_an_unbalanced_transaction(self) -> None:
        self.app.entry_postings.insert("1.0", "1010=2500\n4000=-2000")
        self.app._save_entry()

        # The error went to the message box, not into the journal.
        # 错误被弹窗拦截，而不是进入日记账。
        self.assertTrue(self.messagebox.showerror.called)
        self.assertEqual(self.services.ledger.stats()["transactions"], 2)

    def test_voiding_from_the_journal_tab(self) -> None:
        with mock.patch(
            "centsible.interface.gui.app.simpledialog.askstring",
            return_value="duplicated entry",
        ):
            self.app.journal_tree.tree.selection_set("1")
            self.app._journal_void()

        self.assertTrue(self.services.ledger.get(1).is_void)
        self.messagebox.showerror.assert_not_called()

    def test_reports_tab_renders_the_trial_balance(self) -> None:
        self.app.report_kind.set("试算平衡表")
        self.app._report_refresh()

        self.assertTrue(self.app.report_title.cget("text").startswith("试算平衡表"))
        self.assertGreaterEqual(len(self.app.report_tree.tree.get_children()), 3)


@unittest.skipUnless(HAS_TK, "tkinter window tests need a display")
class HeadlessWindowTests(unittest.TestCase):
    """A missing display must surface as a configuration error, not a traceback.

    缺少显示环境时必须以配置错误的形式出现，而不是抛出 traceback。"""

    def test_no_display_becomes_a_configuration_error(self) -> None:
        from centsible.errors import ConfigurationError
        from centsible.interface.gui import app as gui_app

        config = Config(database_path=Path(":memory:"))
        with mock.patch.object(
            gui_app.tk, "Tk", side_effect=gui_app.tk.TclError("no display name")
        ):
            with self.assertRaises(ConfigurationError) as caught:
                gui_app.run_gui(config)
        self.assertEqual(caught.exception.exit_code, 78)
        self.assertIn("graphical display", str(caught.exception))


def _posting(code: str, amount: str) -> Posting:
    return Posting(code, Decimal(amount))


if __name__ == "__main__":  # pragma: no cover - manual runs
    unittest.main()
