"""Logging configuration.

The CLI is the primary interface, so logging is quiet by default: warnings and
errors go to stderr, everything else only reaches a log file when one is
configured.  Debug runs are one flag away and never change program behaviour.

日志配置。

CLI 是主要接口，因此日志默认保持静默：警告与错误输出到 stderr，其余内容只有在
配置了日志文件时才会写入文件。调试运行只差一个开关，且从不改变程序行为。
"""

from __future__ import annotations

import logging
import sys
from typing import IO

from centsible.config import Config

__all__ = ["LOGGER_NAME", "configure_logging", "get_logger"]

LOGGER_NAME = "centsible"
_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str = LOGGER_NAME) -> logging.Logger:
    return logging.getLogger(name)


def configure_logging(
    config: Config,
    *,
    stream: IO[str] | None = None,
    verbose: bool = False,
) -> logging.Logger:
    """Install handlers on the ``centsible`` logger (idempotent).

    在 ``centsible`` logger 上安装处理器（幂等）。"""

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    console_level = logging.DEBUG if verbose else logging.WARNING
    console = logging.StreamHandler(stream or sys.stderr)
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
    logger.addHandler(console)

    log_file = config.resolved_log_file()
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
        except OSError as exc:  # pragma: no cover - environment dependent
            logger.warning("cannot open the log file %s: %s", log_file, exc)
        else:
            file_handler.setLevel(getattr(logging, config.log_level, logging.INFO))
            file_handler.setFormatter(logging.Formatter(_FORMAT, _DATE_FORMAT))
            logger.addHandler(file_handler)

    return logger
