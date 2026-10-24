"""Use cases.

The application layer coordinates repositories (through the protocols in
:mod:`centsible.application.ports`) and the domain.  It knows the order in
which business rules must be checked and when a unit of work is committed, but
it never touches SQL, the file system or ``sys.argv``.

用例。

应用层协调仓储（通过 :mod:`centsible.application.ports` 中的端口接口）与领域层。
它知道各项业务规则必须以什么顺序校验、工作单元何时提交，但绝不接触 SQL、
文件系统或 ``sys.argv``。
"""

__all__ = [
    "account_service",
    "base",
    "budget_service",
    "chart",
    "dto",
    "export_service",
    "import_service",
    "ledger_service",
    "ports",
    "recurring_service",
    "report_pack",
    "report_service",
    "reporting",
    "settings",
    "validators",
    "workbook",
]
