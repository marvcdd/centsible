"""Every command module, in the order they appear in ``--help``.

全部命令模块，按它们在 ``--help`` 中出现的顺序排列。"""

from __future__ import annotations

import argparse

from centsible.interface.cli.commands import (
    accounts,
    budgets,
    imports,
    recurring,
    reports,
    system,
    transactions,
)

__all__ = ["COMMAND_MODULES", "register_all"]

COMMAND_MODULES = (system, accounts, transactions, reports, budgets, recurring, imports)


def register_all(subparsers: argparse._SubParsersAction) -> None:
    for module in COMMAND_MODULES:
        module.register(subparsers)
