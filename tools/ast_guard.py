#!/usr/bin/env python3
"""Prove that a comment-only edit really was comment-only.

Large refactors that are supposed to touch nothing but comments are easy to get
wrong: one stray keystroke turns ``x = 1`` into ``x = 2`` and the test suite may
still be green.  This tool compares a file against a pristine copy and fails when
anything other than comments and docstrings changed.

用于证明“只改注释”的改动确实只改了注释。

只改注释的重构很容易出错：一次误触可能把 ``x = 1`` 变成 ``x = 2``，而测试可能
恰好仍然是绿的。本工具把当前文件与未改动的原始副本逐项比较，只要注释和 docstring
之外有任何变化就报错。

How it works / 工作原理:

* Comments never appear in the AST, so any change to them is invisible here.
* Docstrings *do* appear in the AST, so every docstring is stripped from both
  trees before the comparison and then compared as text separately.
* What remains is executable code; a difference there is a real change.

* 注释不会进入 AST，因此对注释的任何改动在这里都是不可见的。
* docstring 会进入 AST，所以比较前先把两棵树里的 docstring 全部剥离，
  再单独按文本比较 docstring 的内容。
* 剩下的就是可执行代码；这里出现差异即是真实改动。

Usage / 用法::

    python tools/ast_guard.py --against /path/to/pristine centsible/domain/money.py
    python tools/ast_guard.py --against /path/to/pristine --all

Exit code / 退出码: 0 when only comments and docstrings changed, 1 otherwise.
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Result", "compare_file", "strip_docstrings", "main"]

# Node types that own a ``__doc__`` string.
# 拥有 ``__doc__`` 字符串的节点类型。
DOC_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass
class Result:
    """Outcome of comparing one file against its pristine twin.

    一个文件与其原始副本的比较结果。
    """

    path: str
    status: str  # "same" | "docstrings" | "code" | "missing" / 状态
    detail: str = ""

    @property
    def ok(self) -> bool:
        """Whether the comparison is acceptable.

        比较结果是否可接受。
        """
        return self.status in {"same", "docstrings"}


def strip_docstrings(tree: ast.AST) -> ast.AST:
    """Remove every docstring statement from the tree, in place.

    就地移除树中所有的 docstring 语句。

    A function whose body was only a docstring ends up with an empty body, which
    is fine for :func:`ast.dump` because we never compile the tree again.

    若某个函数体只有 docstring，剥离后函数体为空；因为我们不再编译这棵树，
    这对 :func:`ast.dump` 来说是可以接受的。
    """
    for node in ast.walk(tree):
        if not isinstance(node, DOC_OWNERS):
            continue
        body = getattr(node, "body", [])
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                del body[0]
    return tree


def _code_dump(tree: ast.AST) -> str:
    """Serialise the tree without docstrings, ignoring positions.

    把去掉 docstring、忽略位置信息的语法树序列化。
    """
    return ast.dump(strip_docstrings(tree), include_attributes=False)


def _docstrings(tree: ast.AST) -> list[str]:
    """Collect the docstrings of a tree in walk order.

    按遍历顺序收集整棵树里的 docstring。
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, DOC_OWNERS):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                found.append(doc)
    return found


def compare_file(path: Path, against: Path) -> Result:
    """Compare one file with the same relative path under ``against``.

    把一个文件与 ``against`` 目录下同相对路径的原始文件比较。
    """
    try:
        current = path.read_text(encoding="utf-8")
    except OSError as exc:
        return Result(str(path), "missing", f"cannot read current file: {exc}")
    try:
        relative = path.resolve().relative_to(Path.cwd().resolve())
    except ValueError:
        return Result(str(path), "missing", "file is outside the project root")
    pristine_path = against / relative
    if not pristine_path.exists():
        return Result(str(path), "missing", "no pristine copy (new file)")
    pristine = pristine_path.read_text(encoding="utf-8")
    if pristine == current:
        return Result(str(path), "same")
    try:
        current_tree = ast.parse(current)
        pristine_tree = ast.parse(pristine)
    except SyntaxError as exc:
        return Result(str(path), "code", f"syntax error: {exc}")
    # Read the docstrings before stripping, because stripping mutates the trees.
    # 先取出 docstring 再剥离：剥离操作会就地修改语法树。
    current_docs = _docstrings(current_tree)
    pristine_docs = _docstrings(pristine_tree)
    if _code_dump(current_tree) != _code_dump(pristine_tree):
        return Result(str(path), "code", "executable code changed")
    if current_docs != pristine_docs:
        return Result(str(path), "docstrings", "docstrings changed (expected)")
    return Result(str(path), "same")


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point of the command line interface.

    命令行入口。
    """
    parser = argparse.ArgumentParser(
        description="Verify that a rewrite changed comments and docstrings only.",
        # 中文说明见上方 docstring。
        # The Chinese description is in the module docstring above.
    )
    parser.add_argument(
        "--against",
        required=True,
        help="root of the pristine copy",
        # 参数：未改动的原始副本根目录。
        # Root directory of the untouched copy.
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="files to compare (default: every Python file below the cwd)",
        # 参数：要比较的文件，默认是当前目录下所有 Python 文件。
        # Files to compare; defaults to every Python file below the cwd.
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="compare every Python file below the current directory",
        # 参数：比较当前目录下的所有 Python 文件。
        # Compare every Python file below the current directory.
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="only print problems",
        # 参数：只打印有问题的文件。
        # Only print the files that fail.
    )
    args = parser.parse_args(argv)

    against = Path(args.against)
    if args.all or not args.files:
        targets = [
            item
            for item in sorted(Path(".").rglob("*.py"))
            if not ({".venv", ".idea", "__pycache__"} & set(item.parts))
            and "egg-info" not in str(item)
        ]
    else:
        targets = [Path(item) for item in args.files]

    failures = 0
    for target in targets:
        result = compare_file(target, against)
        if result.ok:
            if not args.quiet:
                mark = "OK" if result.status == "same" else "DOC"
                print(f"{mark:<3} {result.path} {result.detail}".rstrip())
            continue
        failures += 1
        print(f"FAIL {result.path}: {result.status}: {result.detail}")
    print(f"checked={len(targets)} failures={failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
