#!/usr/bin/env python3
"""Audit that every comment and docstring in the project is bilingual.

The house style of Centsible asks each comment and each docstring to carry both
a Chinese and an English version, so that the code stays readable for readers of
either language.  This script walks the tree, extracts every comment and
docstring with :mod:`tokenize` and :mod:`ast`, and reports the units that are
missing one of the two languages.

校验项目中每一处注释与文档字符串是否都是中英双语。

Centsible 的代码规范要求每处注释、每个 docstring 都同时提供中文与英文两个版本，
以便任何语言的读者都能无障碍阅读。本脚本遍历源码树，用 tokenize 与 ast
提取全部注释和 docstring，并报告缺少其中一种语言的单元。

Usage / 用法::

    python tools/check_comments.py                 # whole project / 校验整个项目
    python tools/check_comments.py centsible       # only one subtree / 只校验某个目录
    python tools/check_comments.py --stats         # summary only / 只看统计
    python tools/check_comments.py --json          # machine readable / 机器可读输出

The check is heuristic: a unit "has English" when it contains at least two Latin
words (or one word of five letters), and "has Chinese" when it contains at least
two CJK characters.  Tool directives such as ``# noqa`` or ``# type: ignore``
are exempt.

校验是启发式的：出现至少两个拉丁词（或一个五字母以上的词）即视为“有英文”，
出现至少两个中日韩字符即视为“有中文”。``# noqa``、``# type: ignore``
之类的工具指令不参与检查。

Exit code / 退出码: 0 when everything is bilingual, 1 when violations exist.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
import tokenize
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["Unit", "Violation", "Report", "audit_file", "collect_files", "main"]


# Characters that count as "Chinese" for the purpose of this check.
# 本检查认定哪些字符算作“中文”。
CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# A Latin word: two or more ASCII letters.
# 一个拉丁词：由两个及以上 ASCII 字母组成。
WORD_RE = re.compile(r"[A-Za-z]{2,}")

# Comment texts that are tool directives rather than prose are exempt.
# 这些注释是给工具看的指令而不是说明文字，因此豁免检查。
EXEMPT_RE = re.compile(
    r"^(?:"
    r"!|"  # shebang / 解释器指令
    r"[^A-Za-z]*coding[:=]|"  # PEP 263 encoding cookie / 编码声明
    r"-\*-.*-\*-|"  # Emacs style file variables / Emacs 风格文件变量
    r"type:|noqa|pylint:|mypy:|pyright:|ruff:|flake8:|isort:|black:|"
    r"fmt:|pragma:|nosec|coverage:"
    r")",
    re.IGNORECASE,
)

# Directories that never contain project sources.
# 这些目录永远不会包含项目源码。
SKIP_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".idea",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)

# Suffixes considered "code with comments".
# 会被视为“带注释的代码”的文件后缀。
SUFFIXES = frozenset({".py"})


@dataclass(frozen=True)
class Unit:
    """One comment block or one docstring that must be bilingual.

    一处必须双语的单元：一个注释块或一个 docstring。
    """

    path: str
    line: int
    kind: str  # "comment" | "docstring" / 种类
    text: str

    @property
    def has_chinese(self) -> bool:
        """Whether the unit contains at least two CJK characters.

        该单元是否至少包含两个中日韩字符。
        """
        return len(CJK_RE.findall(self.text)) >= 2

    @property
    def has_english(self) -> bool:
        """Whether the unit contains a Latin word long enough to count.

        该单元是否包含足够长的拉丁词（视为“有英文”）。
        """
        words = WORD_RE.findall(self.text)
        return len(words) >= 2 or any(len(word) >= 5 for word in words)

    @property
    def compliant(self) -> bool:
        """Whether both languages are present.

        是否中文与英文齐备。
        """
        return self.has_chinese and self.has_english


@dataclass
class Violation:
    """A unit that fails the bilingual requirement.

    不满足双语要求的一处单元。
    """

    path: str
    line: int
    kind: str
    missing: str  # "chinese" | "english" | "both" / 缺失的语言
    excerpt: str

    def as_dict(self) -> dict[str, object]:
        """Return a JSON friendly view.

        返回便于 JSON 序列化的视图。
        """
        return {
            "path": self.path,
            "line": self.line,
            "kind": self.kind,
            "missing": self.missing,
            "excerpt": self.excerpt,
        }

    def format(self) -> str:
        """Render one human readable line.

        渲染成一行人类可读的文本。
        """
        return f"{self.path}:{self.line}: {self.kind} missing {self.missing}: {self.excerpt}"


@dataclass
class Report:
    """Audit result for one or many files.

    一个或多个文件的校验结果。
    """

    units: list[Unit] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)
    files: int = 0

    def extend(self, other: "Report") -> None:
        """Merge another report into this one.

        把另一份报告合并进来。
        """
        self.units.extend(other.units)
        self.violations.extend(other.violations)
        self.files += other.files


def _clean_comment(text: str) -> str:
    """Strip the ``#`` or ``#:`` marker off a comment token.

    去掉注释记号开头的 ``#`` 或 ``#:`` 标记。
    """
    body = text.lstrip("#")
    return body.lstrip(":").strip()


def _standalone_runs(
    comments: Sequence[tuple[int, int, str]],
) -> Iterator[list[tuple[int, int, str]]]:
    """Group comments into blocks of consecutive, equally indented lines.

    把注释按“连续、同缩进”的规则聚成若干块。
    """
    run: list[tuple[int, int, str]] = []
    for row, col, text in comments:
        if run:
            prev_row, prev_col, _ = run[-1]
            if row == prev_row + 1 and col == prev_col:
                run.append((row, col, text))
                continue
            yield run
        run = [(row, col, text)]
    if run:
        yield run


def audit_file(path: Path, *, root: Path | None = None) -> Report:
    """Audit a single Python file and return its report.

    校验单个 Python 文件并返回报告。
    """
    report = Report(files=1)
    display = str(path.relative_to(root)) if root is not None else str(path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:  # pragma: no cover - exotic
        report.violations.append(
            Violation(display, 0, "file", "both", f"cannot read file: {exc}")
        )
        return report

    # Docstrings first: they are the bulk of the prose.
    # 先处理 docstring：它们构成了说明文字的主体。
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover - broken file
        report.violations.append(
            Violation(display, exc.lineno or 0, "syntax", "both", exc.msg)
        )
        return report
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        doc = ast.get_docstring(node, clean=False)
        if not doc or not doc.strip():
            continue
        line = getattr(node.body[0], "lineno", getattr(node, "lineno", 1))
        report.units.append(Unit(display, line, "docstring", doc))

    # Then comments: a block of own-line comments is one unit, a trailing
    # comment on a code line is a unit of its own and must carry both languages
    # inline.
    # 再处理注释：独立成行的连续注释算一块；代码行行尾的注释单独算一处，
    # 且必须把两种语言写在同一行内。
    lines = source.splitlines()
    standalone: list[tuple[int, int, str]] = []
    trailing: list[tuple[int, int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        row, col = token.start
        if EXEMPT_RE.match(_clean_comment(token.string)):
            continue
        prefix = lines[row - 1][:col] if row - 1 < len(lines) else ""
        if prefix.strip():
            trailing.append((row, col, token.string))
        else:
            standalone.append((row, col, token.string))

    for row, _col, text in trailing:
        body = _clean_comment(text)
        if body:
            report.units.append(Unit(display, row, "comment", body))
    for run in _standalone_runs(standalone):
        joined = "\n".join(_clean_comment(item[2]) for item in run)
        if joined.strip():
            report.units.append(Unit(display, run[0][0], "comment", joined))

    for unit in report.units:
        if unit.compliant:
            continue
        if unit.has_chinese:
            missing = "english"
        elif unit.has_english:
            missing = "chinese"
        else:
            missing = "both"
        excerpt = " ".join(unit.text.split())[:90]
        report.violations.append(
            Violation(unit.path, unit.line, unit.kind, missing, excerpt)
        )
    return report


def collect_files(paths: Sequence[str]) -> list[Path]:
    """Expand the given arguments into a list of Python files.

    把命令行参数展开成 Python 文件列表。
    """
    found: list[Path] = []
    seen: set[Path] = set()

    def remember(item: Path) -> None:
        resolved = item.resolve()
        if resolved not in seen:
            seen.add(resolved)
            found.append(item)

    for raw in paths:
        base = Path(raw)
        if base.is_file():
            remember(base)
            continue
        for item in base.rglob("*"):
            if not item.is_file() or item.suffix not in SUFFIXES:
                continue
            if set(item.parts) & SKIP_DIRS or "egg-info" in str(item):
                continue
            remember(item)
    return sorted(found)


def audit_paths(paths: Sequence[str], *, root: Path | None = None) -> Report:
    """Audit every file below the given paths.

    校验给定路径下的所有文件。
    """
    total = Report()
    for path in collect_files(paths):
        total.extend(audit_file(path, root=root))
    return total


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the command line interface.

    命令行入口。
    """
    parser = argparse.ArgumentParser(
        description="Check that comments and docstrings carry Chinese and English.",
        # 帮助文本：说明本命令的用途。
        # Describe what the command does.
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=["centsible", "tests", "tools"],
        help="files or directories to audit (default: centsible tests tools)",
        # 位置参数：要校验的文件或目录，默认是 centsible、tests、tools。
        # Positional arguments: the files or directories to audit.
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="print a summary instead of every violation",
        # 选项：只打印汇总，不逐条列出违规。
        # Print a summary instead of listing each violation.
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit JSON on stdout",
        # 选项：以 JSON 形式输出到标准输出。
        # Emit the report as JSON on standard output.
    )
    parser.add_argument(
        "--max-report",
        type=int,
        default=200,
        help="maximum number of violations to list (default: 200)",
        # 选项：最多列出多少条违规，默认 200 条。
        # Cap on how many violations are printed.
    )
    args = parser.parse_args(argv)

    report = audit_paths(args.paths)
    compliant = sum(1 for unit in report.units if unit.compliant)
    if args.json:
        payload = {
            "files": report.files,
            "units": len(report.units),
            "compliant": compliant,
            "violations": len(report.violations),
            "details": [item.as_dict() for item in report.violations[: args.max_report]],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if not report.violations else 1

    print(
        f"files={report.files} units={len(report.units)} "
        f"compliant={compliant} violations={len(report.violations)}"
    )
    if report.violations and not args.stats:
        for violation in report.violations[: args.max_report]:
            print(violation.format())
        hidden = len(report.violations) - args.max_report
        if hidden > 0:
            print(f"... {hidden} more violation(s) not shown")
        print(
            f"\n{len(report.violations)} unit(s) still need the missing language; "
            f"see docs/style.md for the house style."
        )
    return 0 if not report.violations else 1


if __name__ == "__main__":
    sys.exit(main())
