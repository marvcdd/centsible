"""The ``centsible`` command line entry point.

Responsibilities: parse arguments, load configuration, build the context, call
the handler, and translate exceptions into readable messages with stable exit
codes.  No business logic lives here.

``centsible`` 命令行入口。

职责：解析参数、加载配置、构建上下文、调用处理器，并把异常转换成可读的消息
与稳定的退出码。这里不包含任何业务逻辑。
"""

from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path
from typing import Sequence

from centsible import __version__
from centsible.config import CONFIG_FILENAME, Config
from centsible.errors import CentsibleError
from centsible.infrastructure.logging_setup import configure_logging
from centsible.interface.cli.commands import register_all
from centsible.interface.cli.context import CliContext

__all__ = ["build_parser", "main"]

_DESCRIPTION = """\
Centsible - a small, exact double-entry bookkeeping system.

Every entry is balanced the moment it is recorded, reports reconcile by
construction, and nothing is ever deleted: corrections are posted as reversals.
Only the Python standard library is used.
"""

_EPILOG = """\
exit codes:
  0  success            3  not found              6  closed period
  1  runtime error      4  conflict               78 configuration problem
  2  invalid input      5  data integrity problem

examples:
  centsible init && centsible demo
  centsible report balance-sheet
  centsible tx add --date 2025-03-02 --desc "Office rent" \\
      --debit 5100=1200 --credit 1010=1200
  centsible report ledger 1010 --month 2025-03
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="centsible",
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=f"configuration file (default: ./{CONFIG_FILENAME} when it exists)",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        help="ledger database path, overriding the configuration",
    )
    parser.add_argument(
        "--json", action="store_true", help="machine readable JSON output"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="print debug details and tracebacks"
    )
    parser.add_argument("--version", action="version", version=f"centsible {__version__}")

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    register_all(subparsers)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_streams()
    parser = build_parser()
    args = parser.parse_args(argv)

    handler = getattr(args, "handler", None)
    if handler is None:
        parser.print_help()
        return 0

    try:
        config = Config.load(args.config)
        if args.db:
            # A path given on the command line is relative to the current
            # directory, unlike a relative path inside the config file.
            # 命令行上给出的路径相对于当前目录，
            # 而配置文件内部的相对路径则相对于配置文件所在目录。
            config = config.with_overrides(
                database_path=Path(args.db).expanduser().resolve()
            )
    except CentsibleError as exc:
        _report(exc, verbose=args.verbose)
        return exc.exit_code

    configure_logging(config, verbose=args.verbose)
    context = CliContext(config=config, json_output=args.json)
    try:
        return int(handler(context, args) or 0)
    except CentsibleError as exc:
        _report(exc, verbose=args.verbose)
        return exc.exit_code
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except BrokenPipeError:  # pragma: no cover - depends on the pipe consumer | 取决于管道的消费方
        return 0
    finally:
        context.close()


def _report(exc: CentsibleError, *, verbose: bool) -> None:
    if verbose:
        traceback.print_exc()
    print(f"error: {exc}", file=sys.stderr)


def _configure_streams() -> None:
    """Never crash on a character the console cannot encode.

    绝不因为控制台无法编码的字符而崩溃。"""

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (ValueError, OSError):  # pragma: no cover - unusual stream objects | 不常见的流对象
            pass


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
