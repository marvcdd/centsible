"""Tests for the bilingual comment style and for the tools that enforce it.

双语注释规范，以及校验该规范的工具的测试。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import ast_guard  # noqa: E402
import check_comments  # noqa: E402


class CommentStyleTests(unittest.TestCase):
    """The checker finds every unit that is missing one of the two languages.

    检查器能找出每一处缺少其中一种语言的单元。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _write(self, name: str, body: str) -> Path:
        path = self.dir / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_project_is_fully_bilingual(self) -> None:
        report = check_comments.audit_paths(
            [str(ROOT / "centsible"), str(ROOT / "tests"), str(ROOT / "tools")],
            root=ROOT,
        )
        self.assertEqual(report.violations, [], "\n".join(
            item.format() for item in report.violations[:15]
        ))

    def test_chinese_only_comment_is_reported(self) -> None:
        path = self._write("sample.py", '# 只有中文的注释\nX = 1\n')
        report = check_comments.audit_file(path)
        self.assertEqual(len(report.violations), 1)
        self.assertEqual(report.violations[0].missing, "english")

    def test_english_only_docstring_is_reported(self) -> None:
        path = self._write("sample.py", '"""English only."""\nX = 1\n')
        report = check_comments.audit_file(path)
        self.assertEqual(len(report.violations), 1)
        self.assertEqual(report.violations[0].kind, "docstring")
        self.assertEqual(report.violations[0].missing, "chinese")

    def test_bilingual_docstring_passes(self) -> None:
        body = '"""English only.\n\n仅有英文（此处为中文）。"""\nX = 1\n'
        report = check_comments.audit_file(self._write("sample.py", body))
        self.assertEqual(report.violations, [])

    def test_comment_block_counts_as_one_unit(self) -> None:
        body = "# First line.\n# Second line.\n# Third line.\nX = 1\n"
        report = check_comments.audit_file(self._write("sample.py", body))
        self.assertEqual(len(report.violations), 1)
        self.assertEqual(report.violations[0].missing, "chinese")

    def test_bilingual_block_passes(self) -> None:
        body = "# First line.\n# 第一行。\nX = 1\n"
        report = check_comments.audit_file(self._write("sample.py", body))
        self.assertEqual(report.violations, [])

    def test_trailing_comment_must_be_inline_bilingual(self) -> None:
        body = "X = 1  # Default page size\n"
        report = check_comments.audit_file(self._write("sample.py", body))
        self.assertEqual(len(report.violations), 1)

    def test_trailing_comment_with_both_languages_passes(self) -> None:
        body = "X = 1  # Default page size | 默认分页大小\n"
        report = check_comments.audit_file(self._write("sample.py", body))
        self.assertEqual(report.violations, [])

    def test_tool_directives_are_exempt(self) -> None:
        body = (
            "# type: ignore[attr-defined]\n"
            "# noqa\n"
            "X = 1  # noqa: E501\n"
            "Y = 2  # type: ignore\n"
        )
        report = check_comments.audit_file(self._write("sample.py", body))
        self.assertEqual(report.violations, [])

    def test_breakdown_of_missing_language(self) -> None:
        body = '"""English."""\n# 中文注释\nY = 2  # English only\n'
        report = check_comments.audit_file(self._write("sample.py", body))
        missing = sorted(item.missing for item in report.violations)
        self.assertEqual(missing, ["chinese", "chinese", "english"])


class AstGuardTests(unittest.TestCase):
    """The guard separates comment edits from real code edits.

    守卫能区分“只改注释”与“真的改了代码”。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.work = base / "work"
        self.pristine = base / "pristine"
        self.work.mkdir()
        self.pristine.mkdir()
        self._cwd = Path.cwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, self._cwd)

    def _pair(self, before: str, after: str) -> ast_guard.Result:
        (self.pristine / "centsible").mkdir(exist_ok=True)
        (self.work / "centsible").mkdir(exist_ok=True)
        (self.pristine / "centsible" / "sample.py").write_text(before, encoding="utf-8")
        (self.work / "centsible" / "sample.py").write_text(after, encoding="utf-8")
        return ast_guard.compare_file(
            Path("centsible/sample.py"), self.pristine
        )

    def test_identical_file_is_same(self) -> None:
        body = '"""Doc."""\n\nX = 1\n'
        self.assertEqual(self._pair(body, body).status, "same")

    def test_comment_edit_is_invisible(self) -> None:
        result = self._pair("X = 1\n", "# Add a comment.\nX = 1\n")
        self.assertEqual(result.status, "same")

    def test_docstring_edit_is_allowed(self) -> None:
        result = self._pair('"""Doc."""\n', '"""Doc.\n\n文档。"""\n')
        self.assertEqual(result.status, "docstrings")
        self.assertTrue(result.ok)

    def test_code_edit_is_rejected(self) -> None:
        result = self._pair("X = 1\n", "# Comment.\nX = 2\n")
        self.assertEqual(result.status, "code")
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
