"""Pure business objects and rules.

Nothing in this package performs I/O or knows about SQLite, argparse or the
file system.  A domain object can therefore always be constructed and tested in
isolation, which is what keeps the invariants of double entry bookkeeping
trustworthy.

纯粹的领域业务对象与规则。

本包内没有任何代码执行 I/O，也不了解 SQLite、argparse 或文件系统。因此领域
对象总能在隔离环境中被构造和测试，这也正是复式记账各项不变量始终可信的原因。
"""

__all__ = ["accounts", "budgets", "money", "periods", "recurrence", "transactions"]
