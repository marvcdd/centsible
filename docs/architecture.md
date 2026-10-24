# 架构说明

本文说明 Centsible 的分层、依赖方向、关键模式，以及为什么这样划分。

## 1. 目标与约束

| 目标 | 落地方式 |
| --- | --- |
| 业务逻辑可读、可测 | 领域层纯 Python 对象，无 I/O；报表由纯函数生成 |
| 数据永不因浮点误差而错 | `Decimal` + 整数分存储，舍入策略集中在一处 |
| 换存储不改业务 | 服务只依赖 `application/ports.py` 的 `Protocol` |
| 出错信息可读、可脚本化 | 统一错误层次 + 稳定退出码 + `--json` |
| 只有标准库 | 不使用 ORM、模板引擎、CLI 框架、测试框架 |

## 2. 分层与依赖方向

```
        interface (argparse, 表格渲染, 文本/JSON)
             │  depends on
             ▼
        application (用例, 端口, DTO, 纯函数报表)
             │  depends on
             ▼
        domain (Money, Account, Transaction, DatePeriod, ...)

        infrastructure (SQLite, CSV, 时钟, 日志)
             │  implements interfaces declared in application/ports.py
             └──────────────► application / domain
```

规则：

- `domain` 不 import 任何其他层，只 import 标准库和 `centsible.errors`。
- `application` 可以 import `domain`，但不能 import `infrastructure`（唯一例外是组合根之外无）。
- `infrastructure` 实现 `application.ports` 里的协议，可以 import `application` 与 `domain`。
- `interface` 依赖 `application`（服务）与 `bootstrap`（组合根），也直接使用少量 `infrastructure` 的纯工具（如 `csvio.write_csv`、`jsonio.write_json`）完成文件写入。
- `centsible/bootstrap.py` 是**唯一**同时了解所有层的地方。

可以这样检查依赖是否被破坏：

```bash
# domain 里不应出现 sqlite3 / argparse / pathlib 之类
grep -R "import sqlite3\|import argparse" centsible/domain || echo "domain is clean"
```

## 3. 模块职责

### domain

| 模块 | 职责 | 关键不变量 |
| --- | --- | --- |
| `money.py` | 金额解析、量化、格式化、`Money` 值对象 | 不允许 `float`；币种不可混算；按币种精度四舍五入 |
| `accounts.py` | 科目、五种科目类型、正常余额方向 | 代码唯一且规范化为大写；子科目类型必须与父科目一致（服务层校验） |
| `transactions.py` | `Posting`、`Transaction`、标签规范化 | 至少两行；金额之和必须为 0；金额不得为 0 |
| `periods.py` | 期间、月份算术、财年 | 期间闭区间；月末夹取；月份/季度/年份解析 |
| `budgets.py` | 预算与差异 | 预算非负；差异方向随科目类型解释 |
| `recurrence.py` | 周期规则与排期 | 模板必须配平；按起始日锚定，避免「31 号漂移」 |

### application

| 模块 | 职责 |
| --- | --- |
| `ports.py` | 七个仓储协议、`UnitOfWork`、`Clock`、`StatementReader`，以及 `AccountTotals`、`LedgerLine` 等读模型 |
| `dto.py` | 报表与操作结果的不可变快照，全部可 `to_dict()` |
| `reporting.py` | **纯函数**：输入「科目 + 聚合金额」，输出报表 DTO |
| `report_service.py` | 只做取数（1~2 条聚合查询）+ 调用纯函数 |
| `ledger_service.py` | 记账闸门：日期、关账、科目可记账性、引用唯一性；作废；关账 |
| `account_service.py` | 科目增删改归档、层级与环检测、默认科目表安装 |
| `budget_service.py` | 预算设置/清理/复制/对比 |
| `recurring_service.py` | 规则 CRUD、到期物化（幂等、错误不阻塞） |
| `import_service.py` | 对账单 → 凭证，指纹去重，逐行容错 |
| `export_service.py` | 生成导出数据（CSV 行 / JSON 备份 / 类型化凭证工作簿），由 interface 落盘 |
| `workbook.py` | 工作簿/工作表 DTO：类型化列（文本/金额/日期/数字）、标题/副标题/合计行 |
| `report_pack.py` | 九张工作表的 Excel 报表包，复用文本报表同一套 DTO 与纯函数 |
| `validators.py` | 跨服务共享校验（科目存在且可记账） |
| `settings.py` | 币种、精度、财年起始月 |

### infrastructure

| 模块 | 职责 |
| --- | --- |
| `schema.py` | 版本化 DDL（写在 Python 里，随包分发，无需附带 `.sql` 文件） |
| `database.py` | 连接配置（外键、WAL、busy_timeout）、迁移、完整性检查、备份 |
| `repositories.py` | 七个仓储实现，负责行 ↔ 领域对象、金额 ↔ 分的转换 |
| `uow.py` | 工作单元：一个事务、一套仓储、显式 `commit` |
| `csvio.py` | CSV 写出与读取（`write_csv` / `read_csv_rows`） |
| `statements.py` | 对账单读取器：CSV/TSV 与 Excel 共用一套列映射解析管线，按后缀分派 |
| `xlsx.py` | 零依赖的 `.xlsx` 读写（`zipfile` + `ElementTree`）：数字/日期单元格、冻结表头、自动筛选、共享字符串、日期样式识别 |
| `jsonio.py` | JSON 备份 |
| `clock.py` | `SystemClock` / `FixedClock`（测试冻结时间） |
| `logging_setup.py` | 日志（默认安静，`--verbose` 或配置文件开启） |

### interface

| 模块 | 职责 |
| --- | --- |
| `launcher.py` | 入口路由：按 `[interface]` 配置与调用方式选择 CLI / 窗口，禁用时给出配置错误 |
| `cli/main.py` | argparse 装配、`CentsibleError` → 退出码、输出流编码兜底 |
| `cli/context.py` | 惰性构建服务（`config show` 不会创建数据库）、文本/JSON 输出 |
| `cli/table.py` | 按**显示宽度**对齐的表格（中文占两列），键值渲染 |
| `cli/parsing.py` | `ACCOUNT=AMOUNT[:MEMO]`、`--debit/--credit`、期间参数 |
| `cli/commands/*` | 每个命令组一个模块：只做参数 → 服务调用 → 渲染 |
| `gui/app.py` | 零依赖 tkinter 窗口：总览/凭证/录入/报表四页，只调服务、不含业务逻辑 |

### tools（开发期，不参与运行时）

| 模块 | 职责 |
| --- | --- |
| `check_comments.py` | 用 `tokenize`/`ast` 提取全部注释与 docstring，报告缺少中/英的单元；可接 CI |
| `ast_guard.py` | 把当前文件与原始副本的「剥离 docstring 后的 AST」对比，证明改动只落在注释上 |

这两个脚本不被 `centsible` 包导入，也不进入 wheel，属于仓库的开发护栏；规范本身由 `tests/test_comment_style.py` 守着（见 [style.md](style.md)）。

## 4. 运行时流程

### 记一笔凭证

```
tx add
  └─ parsing.parse_posting_specs()        # 文本 → Posting（可自动配平）
  └─ LedgerService.record()
       ├─ Transaction(...)                 # 领域校验：≥2 行、合计为 0、描述/标签/日期
       ├─ _transaction()                   # 开启 UoW（BEGIN IMMEDIATE）
       ├─ 日期校验（未来日期、关账期间）
       ├─ 科目校验（存在、未归档、是叶子科目）
       ├─ 引用唯一性
       ├─ TransactionRepository.add()      # 写 transactions + postings
       ├─ AuditRepository.record()
       └─ commit
```

### 出资产负债表

```
report balance-sheet
  └─ ReportService.balance_sheet(as_of)
       ├─ SELECT 科目（含归档，保证历史可读）
       ├─ SELECT 累计发生额（截至 as_of）
       ├─ SELECT 本财年发生额（fiscal_year_start .. as_of）
       └─ reporting.build_balance_sheet(...)   # 纯函数
            ├─ 资产/负债/权益按正常方向取自然余额
            ├─ 以前年度损益 → 留存收益
            ├─ 本财年损益   → 本年利润
            └─ 校验：资产 == 负债 + 权益
```

## 5. 关键模式

### 值对象与不变量

`Money`、`Account`、`Posting`、`Transaction`、`DatePeriod`、`Budget`、`RecurringRule` 都是 `frozen` dataclass，构造即校验（`__post_init__`）。这让「不可能存在的凭证」在系统中无法被表示：一旦 `Transaction` 构造成功，它就是平衡的。

### 符号约定

分录金额正数=借、负数=贷。好处：

- 平衡校验是 `sum(amounts) == 0`；
- 冲销/取反是 `-amount`；
- 借贷两列只是 `max(amount, 0)` 与 `max(-amount, 0)`。

### 端口与适配器

服务构造函数只接受 `UnitOfWorkFactory`、`Clock`、`LedgerSettings`，以及（导入服务的）`StatementReader`。因此：

- 生产环境注入 `SqliteUnitOfWorkFactory`；
- 测试可注入内存实现，或使用真实 SQLite 临时文件；
- 时间被 `FixedClock` 冻结，报表与关账测试不会因「今天」变化而飘。

### 工作单元

`SqliteUnitOfWork`：

- `__enter__`：建立连接 → `BEGIN IMMEDIATE` → 懒创建仓储；
- `commit()`：`COMMIT`；
- `__exit__`：异常或未提交 → `ROLLBACK`，然后关闭连接。

服务层用 `ServiceBase._transaction(uow)` 统一处理「加入已有事务」与「自建事务」：

```python
with self._transaction(uow) as (work, owned):
    ...
    # owned=True 时，退出上下文时自动 commit
```

导入服务因此可以在**一个**事务里创建批次、逐行调用 `LedgerService.record(..., uow=uow)`、更新指纹，全部成功或全部回滚。

### 组合根

```python
services = build_services(config, clock=FixedClock(date(2025, 3, 15)))
services.ledger.record(...)
services.reports.balance_sheet()
```

`AppServices` 是一个冻结的 dataclass，聚合数据库、时钟、UoW 工厂与全部服务。CLI 通过 `CliContext` 惰性构建它（`config show` 之类命令因此不会碰数据库）。

### 报表 = 纯函数

`build_trial_balance` / `build_income_statement` / `build_balance_sheet` / `build_account_ledger` / `build_budget_report` / `build_activity` 都只接收领域对象与 `AccountTotals`。因此：

- 单测不需要数据库（`tests/test_reporting.py`）；
- 会计恒等式的验证（资产 = 负债 + 权益）只写一次；
- 服务层只负责「取哪些数」。

### 零依赖 Excel

`.xlsx` 是 ZIP 里的几个 XML 部件，所以 `infrastructure/xlsx.py` 用 `zipfile` + `xml.etree.ElementTree` 直接读写，不引入 `openpyxl`/`xlsxwriter`：

- **写侧**：多工作表、标题/副标题行、粗体表头、冻结表头、自动筛选、列宽；金额是真数字单元格（`#,##0.00`），日期是真日期单元格（`yyyy-mm-dd`）；文本走共享字符串表，中文/emoji/特殊字符原样保存。
- **读侧**：按名/按序号选表；共享字符串与内联字符串、数字、布尔、公式缓存值；识别内置与自定义的日期/时间格式（含 1904 日期系统），把日期单元格还原成 `date`，把数字还原成 `Decimal`/`int`。
- **统一管线**：`statements.py` 里 CSV 与 XLSX 读取器共用同一套「列映射 → 单元格解析 → 逐行问题收集」逻辑，两种格式永远不会漂移出两套语义。
- **互操作验证**：`tests/test_xlsx.py` 用 openpyxl 双向验证（我们写的它读得懂，它写的我们读得懂）。openpyxl 只出现在测试的 `skipUnless` 分支里，生产路径零依赖。
- **无网络**：模块里出现的 `http` 只是 OOXML 命名空间名的一部分（如 `spreadsheetml/2006/main`），属于 ISO/IEC 29500 规定的不透明标识符，永远不会被当作地址访问；读写路径不打开任何 socket，读取器还用条目数与解压后字节数两道预算挡住构造过的归档，并在解析前拒绝任何带 DTD 或实体声明的部件（OOXML 用不到它们，而实体正是让几字节 XML 膨胀成几 GB 的手段）。静态扫描器报出的「使用未加密的 HTTP 协议」是误报，`infrastructure/xlsx.py` 改为分片拼出协议名来消掉它，并由 `tests/test_xlsx.py` 的 `NamespaceHygieneTests` 守住两条：包内不得出现明文 URL 字面量，且命名空间必须与规范逐字节一致。
- **元数据用 UTC**：`docProps/core.xml` 的 `dcterms:created/modified` 按 W3CDTF 写成 UTC（带 `Z`），本地时间绝不会被贴上 UTC 标签；`tests/test_xlsx.py` 的 `PackageSafetyTests` 固定了这一点。

`report_pack.py` 把报表 DTO 映射成 `workbook.py` 里类型化的工作簿（`ColumnKind.TEXT/MONEY/DATE/NUMBER`），因此 Excel 报表包与终端报表出自同一份数据、同一套纯函数。

### 双语注释

代码的注释与 docstring **一律中英双语**：英文原句保留在前，中文紧随其后；行尾注释用 ` | ` 分隔。这样做的原因不是装饰，而是让两种语言的读者都不必猜会计术语（例如 transaction 固定译作「凭证」而不是「交易」）。格式、术语表与写作要求见 [style.md](style.md)。

这条规范之所以可信，是因为它不依赖自觉：

- `tools/check_comments.py` 把每处注释、每个 docstring 当作一个「单元」判定是否双语，`# noqa` 之类的工具指令自动豁免；
- `tools/ast_guard.py` 证明这类改动只碰注释——注释根本不进 AST，docstring 先被剥离再比较，剩下的差异就是真的改了代码；
- `tests/test_comment_style.py` 把上述两点变成回归测试，因此新代码忘了写中文会立刻红。

### 双前端与入口路由

CLI 与窗口（tkinter，标准库）是**同一个服务层的两种视图**：`gui/app.py` 不包含任何
业务逻辑，每个数字都来自与文本报表相同的 DTO 与纯函数，因此两个界面永远一致。
`interface/launcher.py` 是唯一的路由点：

- 带子命令 → CLI；`--gui` → 窗口；`--cli` → CLI；无参数 → 按 `[interface]` 配置。
- 配置用 `[interface] cli / gui` 两个布尔开关控制各自启用/禁用（内置默认
  `cli=true, gui=false`），两者全禁会被配置校验拒绝；被禁用的前端报清晰的配置
  错误（退出码 78），`--version`/`--help` 等诊断输出不受影响。
- 路由决策是纯函数（`decide()`），因此整张决策表可以用单元测试穷举，窗口本身
  则只在有显示环境时测试（`tests/test_gui.py` 自动跳过无头环境）。

tkinter 属于标准库，窗口前端因此没有破坏项目的零依赖承诺；若某个 Python 环境
确实没有 tkinter，启动窗口时会得到一条说明性错误而不是 traceback。

## 6. 数据模型

```
accounts(id, code UNIQUE, name, type, parent_code → accounts.code, description, archived, created_at)
transactions(id, date, description, payee, reference UNIQUE?, tags JSON, status posted|void,
             import_batch → import_batches.id, void_reason, created_at)
postings(id, transaction_id → transactions.id ON DELETE CASCADE, position,
         account_code → accounts.code, amount_minor INTEGER ≠ 0, memo)
budgets(id, account_code, month, amount_minor ≥ 0, note, UNIQUE(account_code, month))
recurring_rules(id, name UNIQUE, description, payee, frequency, interval, start_date,
                next_date, end_date, tags, active, created_at)
recurring_postings(id, rule_id → recurring_rules.id ON DELETE CASCADE, position,
                   account_code, amount_minor, memo)
import_batches(id, source, filename, checksum, imported_at, rows_read/imported/skipped)
import_entries(id, batch_id → import_batches.id, fingerprint UNIQUE, transaction_id)
audit_log(id, at, actor, action, entity, entity_id, detail)
meta(key PRIMARY KEY, value)
```

设计说明：

- 金额统一存**整数分**，读取时用 `from_minor()` 转换；币种精度由 `LedgerSettings` 决定。
- 日期存 ISO `TEXT`，可按字典序比较与范围查询。
- 分录引用科目**代码**而非代理键，直接看库也是可读的；`ON UPDATE CASCADE` 为未来「改代码」留了余地（当前服务层禁止改代码）。
- `reference` 上的唯一索引是「部分索引」（`WHERE reference IS NOT NULL`），允许大量无引用凭证。
- `meta` 保存两类「账本事实」：`lock_date`（关账日）与 `currency`（建账币种）。币种一旦写入即不可更改——所有金额都是该币种的整数分，换币种等于让旧数字改变含义。`build_services()` 打开账本时会对比配置与账本币种，以账本为准并输出警告。
- 迁移版本记录在 `PRAGMA user_version`，迁移失败整步回滚。

## 7. 错误处理

`centsible/errors.py` 定义层次化异常，每个都可映射到稳定退出码：

```
CentsibleError (1)
├─ ConfigurationError (78)
├─ ValidationError (2)
│  ├─ CurrencyMismatchError
│  └─ UnbalancedEntryError
├─ NotFoundError (3)
├─ ConflictError (4)
│  └─ LedgerLockedError (6)
└─ DataError (5)
```

CLI 只在一处 `except CentsibleError`，打印 `error: ...` 并返回 `exc.exit_code`；`--verbose` 时附带 traceback。编程错误（`TypeError`、`KeyError`）故意不被捕获，以免掩盖 bug。窗口前端遵守同一套约定：构建服务失败、或 Tk 连不上显示环境，都会转成配置错误（78）并给出可操作的提示，而不是把 traceback 甩给用户。

## 8. 测试策略

| 层 | 测试方式 | 文件 |
| --- | --- | --- |
| domain | 纯单元测试，无 I/O | `test_money`, `test_accounts_domain`, `test_transactions_domain`, `test_periods` |
| 纯报表 | 只构造字典与科目对象 | `test_reporting` |
| infrastructure | 临时 SQLite 文件 + 内存库 | `test_database`, `test_repositories` |
| application | 真实 SQLite + `FixedClock` | `test_*_service` |
| interface | 直接调用 `main(argv)` 断言退出码与输出 | `test_cli`, `test_parsing`, `test_table` |
| 组合 | 演示数据驱动全链路 | `test_demo` |
| 不变量 | 固定种子随机生成 120 笔凭证，再从数据行重算全部报表恒等式 | `test_invariants` |
| 规范 | 扫描注释/docstring 的双语覆盖，并用 AST 对比证明语义未变 | `test_comment_style` |

所有测试都用临时目录，互不干扰；`FixedClock` 消除日期相关抖动。

`test_invariants` 是这套测试里唯一的随机源，但种子固定，因此失败仍可复现。它构造的
场景手写用例很难覆盖：跨两个财年的留存收益拆分、只有贷方的科目、同一天多笔相同金额、
以及「作废之后报表是否真的少了一笔」。断言形式都是恒等式（借方合计=贷方合计、
资产=负债+权益、关账前后数字不变），所以换演示数据也不用改测试。

## 9. 扩展指南

- **新增报表**：在 `reporting.py` 写纯函数 → 在 `dto.py` 定 DTO → 在 `report_service.py` 取数 → 在 `commands/reports.py` 加子命令 → 写纯函数测试。
- **新增导入格式**：实现 `StatementReader.read(path, mapping)`，在 `bootstrap.py` 里替换 `CsvStatementReader` 即可；导入服务不需要改动。
- **更换存储**：实现 `ports.py` 的七个协议 + `UnitOfWorkFactory`，替换 `bootstrap.py` 中的实现；服务与测试基本不动。
- **多币种**：`LedgerSettings` 已集中币种；需要新增汇率表、折算规则与报表列，属于较大改动。

## 10. 已知限制

- 单账本单本位币，无汇率折算与重估。
- 无多用户/权限模型；`audit_log.actor` 固定为 `cli`。
- 无数据库连接池；每次用例一条连接（CLI 场景足够，且更容易保证事务边界）。
- 报表在内存中聚合；百万级分录下需要改为增量余额表。
