"""The error hierarchy used across Centsible.

Every failure the program raises on purpose is a :class:`CentsibleError`.  The
command line layer relies on this to turn exceptions into readable messages and
stable exit codes, and it never has to guess what a traceback means.

Exit codes follow ``sysexits.h`` conventions where a suitable value exists:

===== ==========================================================
code  meaning
===== ==========================================================
0     success
1     generic runtime failure
2     invalid input / validation failure
3     requested resource does not exist
4     request conflicts with the current state
5     stored data is corrupt or inconsistent
6     the ledger period is locked (closed)
78    configuration problem
===== ==========================================================

Centsible 中所有错误共用的错误层级。

程序主动抛出的每一种失败都是 :class:`CentsibleError`。命令行层依赖这一点，把异常
转换为可读的提示信息和稳定的退出码，因此永远无需去猜测 traceback 的含义。

在存在合适取值时，退出码遵循 ``sysexits.h`` 的约定：

===== ==========================================================
代码    含义
===== ==========================================================
0     成功
1     通用运行时失败
2     非法输入／校验失败
3     请求的资源不存在
4     请求与当前状态冲突
5     已存储的数据损坏或不一致
6     账簿的会计期间已锁账（已关闭）
78    配置问题
===== ==========================================================
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "CentsibleError",
    "ConfigurationError",
    "ConflictError",
    "CurrencyMismatchError",
    "DataError",
    "LedgerLockedError",
    "NotFoundError",
    "UnbalancedEntryError",
    "ValidationError",
]


class CentsibleError(Exception):
    """Base class for all deliberate Centsible failures.

    Parameters
    ----------
    message:
        Human readable explanation.  It is shown verbatim to the user, so it
        should be a complete sentence without a trailing period.
    context:
        Optional structured details (entity ids, amounts, ...) kept for the
        audit log and for tests.  They never leak into the plain-text output.

    Centsible 中所有主动抛出错误的基类。

    参数
    ----
    message:
        供人阅读的说明文字。它会原样展示给用户，因此应当是一个完整的句子，
        且结尾不加句号。
    context:
        可选的结构化细节（实体 id、金额等），保留给审计日志和测试使用。
        这些细节不会出现在纯文本输出中。
    """

    exit_code: int = 1

    def __init__(self, message: str, /, **context: Any) -> None:
        super().__init__(message)
        self.message = message
        self.context: dict[str, Any] = context

    def __str__(self) -> str:  # pragma: no cover - trivial | 实现简单，无需覆盖率统计
        return self.message

    def __repr__(self) -> str:  # pragma: no cover - debugging aid | 调试辅助
        return f"{type(self).__name__}({self.message!r})"


class ConfigurationError(CentsibleError):
    """The configuration file or the environment is unusable.

    配置文件或运行环境不可用。"""

    exit_code = 78


class ValidationError(CentsibleError):
    """Input violates a business rule.

    ``field`` names the offending input when one can be identified, which lets
    the CLI render ``--currency: must be ...`` instead of a bare sentence.

    输入违反了某条业务规则。

    能够定位到具体输入时，``field`` 会指明是哪一个，从而让 CLI 渲染出
    ``--currency: must be ...`` 这样的提示，而不是一句空泛的说明。
    """

    exit_code = 2

    def __init__(self, message: str, /, *, field: str | None = None, **context: Any) -> None:
        super().__init__(message, **context)
        self.field = field

    def __str__(self) -> str:
        if self.field:
            return f"{self.field}: {self.message}"
        return self.message


class NotFoundError(CentsibleError):
    """A referenced entity (account, transaction, rule, ...) does not exist.

    被引用的实体（科目、凭证、规则等）不存在。"""

    exit_code = 3


class ConflictError(CentsibleError):
    """The request is valid but contradicts the current state of the ledger.

    请求本身合法，但与账簿的当前状态相矛盾。"""

    exit_code = 4


class LedgerLockedError(ConflictError):
    """The affected date range belongs to a closed (locked) period.

    受影响的日期区间属于一个已关闭（已锁账）的会计期间。"""

    exit_code = 6


class DataError(CentsibleError):
    """Persisted data fails an integrity check - this is a bug or corruption.

    持久化的数据未能通过完整性检查——这要么是程序缺陷，要么是数据损坏。"""

    exit_code = 5


class CurrencyMismatchError(ValidationError):
    """Two monetary values in different currencies were combined.

    两个不同币种的金额值被合并到了一起。"""


class UnbalancedEntryError(ValidationError):
    """The debit and credit legs of an entry do not sum to zero.

    一笔凭证的借方分录与贷方分录合计不为零。"""
