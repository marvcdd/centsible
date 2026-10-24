"""Tests for the entry point router that picks CLI or GUI.

入口路由（在 CLI 与 GUI 之间选择）的测试。"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from centsible.config import Config
from centsible.interface.launcher import decide, run


class DecideTests(unittest.TestCase):
    """Routing decisions are pure and follow the documented rules.

    路由决策是纯函数，并遵守文档中写明的规则。
    """

    def setUp(self) -> None:
        self.cli_only = Config()  # defaults: cli on, gui off / 默认：CLI 开、GUI 关
        self.both = Config(gui_enabled=True)
        self.gui_only = Config(cli_enabled=False, gui_enabled=True)

    def test_subcommands_mean_the_command_line(self) -> None:
        self.assertEqual(decide(self.cli_only, ["init"]), "cli")
        self.assertEqual(decide(self.cli_only, ["report", "balance-sheet"]), "cli")
        self.assertEqual(decide(self.cli_only, ["--json", "tx", "list"]), "cli")

    def test_gui_flag_wins(self) -> None:
        self.assertEqual(decide(self.cli_only, ["--gui"]), "gui")
        self.assertEqual(decide(self.cli_only, ["init", "--gui"]), "gui")

    def test_cli_flag_wins_even_with_gui_enabled(self) -> None:
        self.assertEqual(decide(self.both, ["--cli"]), "cli")
        self.assertEqual(decide(self.both, ["--cli", "init"]), "cli")

    def test_bare_invocation_follows_the_configuration(self) -> None:
        self.assertEqual(decide(self.cli_only, []), "cli")
        self.assertEqual(decide(self.both, []), "gui")
        self.assertEqual(decide(self.gui_only, []), "gui")

    def test_config_plumbing_is_not_a_command(self) -> None:
        # ``--config PATH`` alone is still a bare invocation.
        # 单独的 ``--config PATH`` 仍然算无参数调用。
        self.assertEqual(decide(self.cli_only, ["--config", "x.toml"]), "cli")
        self.assertEqual(decide(self.both, ["--config", "x.toml"]), "gui")


class RunTests(unittest.TestCase):
    """The enabled/disabled switches are enforced with clear errors.

    启用/禁用开关会被强制执行，并给出清晰的错误信息。
    """

    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tempdir.cleanup)
        self.directory = Path(self._tempdir.name)

    def write(self, interface_lines: str) -> Path:
        path = self.directory / "centsible.toml"
        path.write_text(
            f'[database]\npath = "{(self.directory / "ledger.db").as_posix()}"\n'
            f"[interface]\n{interface_lines}",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def invoke(path: Path, *arguments: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = run(["--config", str(path), *arguments])
        return code, out.getvalue(), err.getvalue()

    def test_disabled_command_line_refuses_commands(self) -> None:
        path = self.write("cli = false\ngui = true\n")
        code, _out, err = self.invoke(path, "tx", "list")
        self.assertEqual(code, 78)
        self.assertIn("command line interface is disabled", err)

    def test_disabled_command_line_still_serves_version(self) -> None:
        path = self.write("cli = false\ngui = true\n")
        # argparse exits the process for --version; SystemExit(0) is the success
        # signal here.
        # argparse 对 --version 直接退出进程；此处 SystemExit(0) 即成功信号。
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                run(["--config", str(path), "--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("centsible", out.getvalue())

    def test_disabled_window_refuses_the_flag(self) -> None:
        path = self.write("cli = true\ngui = false\n")
        code, _out, err = self.invoke(path, "--gui")
        self.assertEqual(code, 78)
        self.assertIn("windowed interface is disabled", err)

    def test_enabled_window_is_delegated_to_the_app(self) -> None:
        path = self.write("gui = true\n")
        with mock.patch(
            "centsible.interface.gui.app.run_gui", return_value=0
        ) as run_gui:
            code, _out, _err = self.invoke(path)
        self.assertEqual(code, 0)
        run_gui.assert_called_once()

    def test_the_window_takes_no_further_arguments(self) -> None:
        path = self.write("gui = true\n")
        with mock.patch("centsible.interface.gui.app.run_gui") as run_gui:
            code, _out, err = self.invoke(path, "--gui", "init")
        self.assertEqual(code, 78)
        self.assertIn("takes no further arguments", err)
        run_gui.assert_not_called()

    def test_dangling_config_flag_is_a_usage_error(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = run(["--config"])
        self.assertEqual(code, 78)
        self.assertIn("--config needs the path", err.getvalue())

    def test_config_flag_refuses_a_flag_as_its_value(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = run(["--config", "--gui"])
        self.assertEqual(code, 78)
        self.assertIn("--config needs the path", err.getvalue())

    def test_cli_force_flag_reaches_the_parser(self) -> None:
        path = self.write("gui = true\n")
        with self.assertRaises(SystemExit) as caught:
            run(["--config", str(path), "--cli", "--version"])
        self.assertEqual(caught.exception.code, 0)


if __name__ == "__main__":  # pragma: no cover - manual runs
    unittest.main()
