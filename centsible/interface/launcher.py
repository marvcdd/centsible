"""Entry point router: command line and windowed front ends side by side.

Both front ends talk to the exact same services, so the window never disagrees
with the command line.  Which one answers depends on the configuration
(``[interface] cli / gui``) and on how Centsible was invoked:

* any subcommand            -> the command line front end;
* ``--cli``                 -> force the command line front end;
* ``--gui``                 -> force the windowed front end;
* no arguments, gui enabled -> the window; otherwise the CLI help.

A front end that the configuration disables answers with a clear configuration
error instead of pretending it does not exist.  ``--version``, ``-h`` and
``--help`` always work, even when the command line front end is disabled.

入口路由：命令行与窗口两种前端并存。

两种前端调用的是同一套服务，因此窗口与命令行给出的数字永远不会打架。由哪一个
前端来响应，取决于配置（``[interface] cli / gui``）与调用方式：

* 带任意子命令             -> 命令行前端；
* ``--cli``                 -> 强制命令行前端；
* ``--gui``                 -> 强制窗口前端；
* 无参数且 gui 已启用       -> 打开窗口；否则打印命令行帮助。

被配置禁用的前端会给出清晰的配置错误，而不是假装自己不存在。``--version``、
``-h`` 与 ``--help`` 始终可用，即使命令行前端被禁用。
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from centsible.config import Config
from centsible.errors import CentsibleError

__all__ = ["decide", "run"]

# Arguments that are diagnostics rather than interface usage.
# 这些参数属于诊断输出，而不是界面本身的使用。
_DIAGNOSTIC_FLAGS = frozenset({"--version", "-h", "--help"})


def decide(config: Config, argv: Sequence[str]) -> str:
    """Pick the front end for one invocation.

    ``--gui`` and ``--cli`` win over everything else; a subcommand means the
    command line; and a bare invocation opens the window only when the window
    is enabled.

    为一次调用挑选前端。

    ``--gui`` 与 ``--cli`` 的优先级最高；带子命令即命令行；无参数调用只有在
    窗口已启用时才打开窗口。
    """

    arguments = list(argv)
    if "--gui" in arguments:
        return "gui"
    if "--cli" in arguments:
        return "cli"
    if _drop_config_plumbing(arguments):
        return "cli"
    return "gui" if config.gui_enabled else "cli"


def run(argv: Sequence[str] | None = None) -> int:
    """Entry point used by ``python -m centsible`` and the console script.

    ``python -m centsible`` 与控制台脚本共用的入口。"""

    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        config = _load_config(arguments)
        choice = decide(config, arguments)
        if choice == "gui":
            return _run_gui(config, arguments)
        return _run_cli(config, arguments)
    except CentsibleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code


def _run_cli(config: Config, arguments: list[str]) -> int:
    if "--cli" in arguments:
        arguments.remove("--cli")
    # ``--config PATH`` is plumbing, not front end usage: it must not count as
    # "the command line was used" when deciding whether to refuse a disabled CLI.
    # ``--config PATH`` 属于管道，不是前端使用行为：在判断是否拒绝被禁用的 CLI 时，
    # 不应把它算作“使用了命令行”。
    usage_flags = _drop_config_plumbing(arguments)
    if not config.cli_enabled and any(
        flag not in _DIAGNOSTIC_FLAGS for flag in usage_flags
    ):
        from centsible.errors import ConfigurationError

        raise ConfigurationError(
            "the command line interface is disabled in the configuration; "
            "set [interface] cli = true, or use the windowed interface"
        )
    from centsible.interface.cli.main import main as cli_main

    return cli_main(arguments)


def _drop_config_plumbing(arguments: Sequence[str]) -> list[str]:
    """The arguments with ``--config PATH`` removed.

    去掉 ``--config PATH`` 之后剩余的参数。"""

    return [
        argument
        for index, argument in enumerate(arguments)
        if argument != "--config"
        and not (index > 0 and arguments[index - 1] == "--config")
    ]


def _run_gui(config: Config, arguments: list[str]) -> int:
    if not config.gui_enabled:
        from centsible.errors import ConfigurationError

        raise ConfigurationError(
            "the windowed interface is disabled in the configuration; "
            "set [interface] gui = true"
        )
    if "--gui" in arguments:
        arguments.remove("--gui")
    if _drop_config_plumbing(arguments):
        from centsible.errors import ConfigurationError

        raise ConfigurationError(
            "the windowed interface takes no further arguments; run "
            "'centsible --gui' to open the window"
        )
    try:
        from centsible.interface.gui.app import run_gui
    except ImportError as exc:
        from centsible.errors import ConfigurationError

        raise ConfigurationError(
            "the windowed interface needs tkinter, which this Python "
            "installation does not provide"
        ) from exc
    return run_gui(config)


def _load_config(arguments: Sequence[str]) -> Config:
    """Load the configuration, honouring a ``--config PATH`` flag.

    A dangling ``--config`` is a usage error rather than a silent fallback to
    the default file: guessing would quietly load another ledger's settings.

    加载配置，并尊重 ``--config PATH`` 参数。

    悬空的 ``--config`` 按用法错误处理，而不是悄悄回落到默认文件：靠猜只会悄悄
    加载到另一份账簿的设置。
    """

    path: str | None = None
    for index, argument in enumerate(arguments):
        if argument != "--config":
            continue
        following = arguments[index + 1] if index + 1 < len(arguments) else ""
        if not following or following.startswith("-"):
            from centsible.errors import ConfigurationError

            raise ConfigurationError("--config needs the path of a configuration file")
        path = following
    return Config.load(path)
