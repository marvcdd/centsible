# Centsible

> 一个用**纯 Python 标准库**实现的复式记账（double-entry bookkeeping）引擎，提供命令行与窗口两种界面。

Centsible 面向个人、自由职业者和小微企业：它把每一笔经济业务记成一张**借贷必相等**的凭证，自动生成资产负债表、利润表、试算平衡表和明细账，并提供预算、周期分录、银行对账单导入、期间关账与完整审计轨迹。

- **零第三方依赖**：只需要 Python 3.11+，没有 pip 安装步骤，没有 ORM，没有 Web 框架。
- **金额绝不使用浮点数**：领域层用 `Decimal`，数据库用整数「分」，舍入规则固定为四舍五入（half-up）。
- **业务规则集中在领域层**：报表由纯函数生成，可以脱离数据库单独测试。
- **分层清晰、可维护**：domain → application → infrastructure → interface，依赖只向内。

```
$ python -m centsible demo
Demo ledger ready at /path/to/data/ledger.db

  months generated   6
  accounts           23
  transactions       64
  budgets            5
  recurring rules    2
```

---

## 快速开始

要求：Python **3.11** 或更高版本（用到 `tomllib`、`int | None` 等特性），无其他依赖。

```bash
# 1) 建账：创建 SQLite 账本 + 默认会计科目表（可重复执行，只补缺失科目）
python -m centsible init

# 2) 灌入演示数据（6 个月的经营流水、预算、周期分录）
python -m centsible demo

# 3) 看报表
python -m centsible report balance-sheet
python -m centsible report income --month 2025-03
python -m centsible report summary --months 6
```

重新开始：`python -m centsible demo --fresh`（会删除并重建账本文件）。

也可以把项目按标准包安装，获得 `centsible` 命令：

```bash
pip install -e .
centsible report trial-balance
```

---

## 功能一览

| 命令组 | 作用 |
| --- | --- |
| `init` / `demo` | 建账、灌入演示数据 |
| `account add/list/show/update/archive/restore/delete` | 会计科目表（支持父子层级） |
| `tx add/list/show/void/restore` | 记账凭证：录入、查询（`--status posted/void`）、作废、恢复 |
| `report trial-balance/income/balance-sheet/ledger/summary` | 试算平衡表、利润表、资产负债表、明细账、月度汇总 |
| `budget set/clear/list/report/copy` | 月度预算与实际对比 |
| `recurring add/list/run/pause/resume/delete/upcoming` | 周期分录（房租、订阅费……） |
| `import bank/batches` | 银行对账单导入（CSV/TSV **或 Excel**，幂等、逐行报错；文件内重复行默认跳过并单独报告，`--keep-duplicate-lines` 可导入；`--match-rule PATTERN=ACCOUNT` 按描述/收款方自动分流对方科目） |
| `export journal/backup` | 导出凭证（CSV，`.xlsx` 后缀自动改写成 Excel）/ 完整 JSON 备份 |
| `export workbook` | 九张工作表的 **Excel 报表包** |
| `journal status/lock/unlock/audit` | 期间关账、审计轨迹 |
| `db info/check/backup/restore` | 数据库状态、完整性 + 试算平衡 + 逐张凭证校验、物理备份、从备份恢复 |
| `config show` | 查看生效配置（不会创建/打开数据库） |

全局参数：`--config PATH`、`--db PATH`、`--json`、`--verbose`、`--version`。所有报表与查询命令都支持 `--json`，便于脚本消费。

---

## 记账语法

一张凭证至少两行，金额带符号：**正数=借方，负数=贷方**，合计必须为 0。

```bash
# 付房租：费用增加（借），银行存款减少（贷）
python -m centsible tx add --date 2025-03-02 --desc "办公室房租" \
    --debit  5100=1200 \
    --credit 1010=1200

# 等价写法：直接写带符号的金额
python -m centsible tx add --date 2025-03-02 --desc "办公室房租" \
    --posting 5100=1200 --posting 1010=-1200

# 三行凭证：POS 收款 4520，其中收入 4000、销项税 520
python -m centsible tx add --date 2025-03-08 --desc "POS 收款" \
    --debit 1010=4520:刷卡 --credit 4000=4000:销售收入 --credit 2200=520:销项税

# 允许省略一行金额，由系统自动配平
python -m centsible tx add --date 2025-03-08 --desc "POS 收款" \
    --debit 1010=4520 --credit 4000=4000 --credit 2200=
```

规则要点：

- 只允许**一行**省略金额，且配平后不得为 0；
- 科目代码会自动转为大写，必须存在、未归档、且不是「汇总科目」（有子科目的科目不能直接记账）；
- 默认拒绝未来日期（`--allow-future` 可放开），也拒绝落在关账期间内的日期；
- `--ref` 唯一，重复引用会直接报错，可作为外部系统的幂等键。

作废凭证不会删除数据：

```bash
python -m centsible tx void 7 --reason "重复录入"
```

该凭证保留在凭证列表里（状态 `VOID`、记录原因），但不再影响任何余额；落在**已关账期间**的凭证不允许作废，避免篡改已出的报表。手滑作废也不必补账——期间还没关账时可以恢复：

```bash
python -m centsible tx restore 7 --reason "作废错了编号"
```

恢复与作废都会写进审计轨迹（保留原作废原因），因此"改主意"同样是可追溯的。

---

## 常用工作流

```bash
# 导入银行流水：正数=进账，负数=出账；重复导入自动跳过
python -m centsible import bank statement.csv \
    --account 1010 --counter-account 5900

# 也可以直接导入 Excel（.xlsx/.xlsm），按工作表名或序号选表
python -m centsible import bank statement.xlsx \
    --account 1010 --counter-account 5900 --sheet 流水

# 表头不规范时指定列（列名或 0 起始的列序号均可，CSV 与 Excel 通用）
python -m centsible import bank export.csv \
    --account 1010 --counter-account 5500 \
    --date-col Date --amount-col Amount --desc-col Narrative \
    --date-format %d/%m/%Y --encoding gb18030

# 按描述/收款方自动分流对方科目：先匹配到的规则生效，未命中的行回落到 --counter-account
python -m centsible import bank card.csv --account 1010 --counter-account 5900 \
    --match-rule coffee=5800 --match-rule rent=5100 --match-rule landlord=5100

# 设预算并对比实际
python -m centsible budget set 5100 --month 2025-03 --amount 1500
python -m centsible budget report --month 2025-03
python -m centsible budget copy --from-month 2025-03 --to-month 2025-04

# 周期分录：先建模板，再按需「物化」成真实凭证（可先 --dry-run 预览）
python -m centsible recurring add "每月房租" --desc "办公室房租" \
    --frequency monthly --start 2025-04-01 \
    --debit 5100=1200 --credit 1010=1200
python -m centsible recurring run --until 2025-06-30
python -m centsible recurring upcoming --days 45

# 关账（此后该日期及之前不能再记账/改账）
python -m centsible journal lock --date 2025-03-31
python -m centsible journal status
python -m centsible journal unlock --force      # 重新开账需要显式 force

# 导出与备份
python -m centsible export journal --out journal.csv --month 2025-03
python -m centsible export journal --out journal.xlsx      # 按扩展名自动写 Excel
python -m centsible export workbook --out reports.xlsx --month 2025-03
python -m centsible export backup  --out backup.json
python -m centsible db backup --out ledger-2025-03-31.db   # 一致性物理备份
python -m centsible db check

# 从备份恢复：先校验（完整性/表结构/必需的表），有凭证的账簿必须显式 --force，
# 恢复动作本身也会写进恢复后账簿的审计轨迹
python -m centsible db restore ledger-2025-03-31.db --force
```

`export workbook` 产出一个九张工作表的 Excel 报表包：Summary、月度活动、试算平衡表、利润表、资产负债表、预算、凭证流水、周期规则、科目表。金额是真数字单元格（`#,##0.00`），日期是真日期单元格，表头冻结并带自动筛选——可以直接打印、邮件或归档。

---

## 两种界面：命令行 + 窗口

命令行与窗口**并存**，共用同一套服务与数据——窗口里显示的每个数字都来自与文本报表相同的纯函数，两者不可能不一致。启用哪个由配置文件的 `[interface]` 段控制（内置默认：`cli = true`、`gui = false`；两者不允许全禁）：

```toml
# centsible.toml
[interface]
cli = true     # 是否启用命令行界面（默认开启）
gui = true     # 是否启用窗口界面（默认关闭）
```

| 调用方式 | 行为 |
| --- | --- |
| `centsible <任意子命令>` | 命令行（CLI 被禁用时返回配置错误，退出码 78） |
| `centsible --gui` | 强制打开窗口（GUI 被禁用时返回配置错误） |
| `centsible --cli <命令>` | 强制命令行 |
| `centsible`（无参数） | `gui` 开启 → 弹出窗口；否则打印命令行帮助 |

要点：

- 窗口是**零依赖**的 tkinter（Python 标准库）实现，共四页：**总览**（账本统计 + 试算平衡表）、**凭证**（按状态/关键字查询、明细、作废、恢复）、**录入**（日期/摘要/收款方 + `ACCOUNT=AMOUNT[:MEMO]` 分录表单）、**报表**（试算平衡、利润表、资产负债表、月度汇总）。
- `--version`、`--help` 是诊断输出，任何配置下都可用。
- 如果某个 Python 环境没带 tkinter，`--gui` 会得到一条清晰的配置错误，而不是 traceback。

---

## 配置

默认读取当前目录下的 `centsible.toml`（不存在则用内置默认值），可用 `--config` 指定，或通过环境变量覆盖：

| 环境变量 | 含义 |
| --- | --- |
| `CENTSIBLE_CONFIG` | 配置文件路径 |
| `CENTSIBLE_DB` | 账本数据库路径 |
| `CENTSIBLE_CURRENCY` | 记账本位币 |
| `CENTSIBLE_LOG_LEVEL` | 日志级别 |

`init --write-config` 会写出带注释的配置文件：

```toml
[database]
path = "data/ledger.db"      # 相对路径相对于配置文件所在目录

[ledger]
currency = "CNY"             # ISO 4217 三位代码
fiscal_year_start_month = 1  # 1..12，用于划分「本年利润」与「以前年度损益」

[logging]
level = "INFO"
# file = "logs/centsible.log"

[format]
date = "%Y-%m-%d"
```

配置是**严格校验**的：未知小节、未知键、非法货币/月份/日志级别都会直接报错（退出码 78），而不是被静默忽略。

首次 `init` 会把币种写进账本的 `meta` 表，之后**不可更改**——所有金额都按该币种的"分"存储。如果配置里的币种与账本记录不一致，程序以**账本为准**并在 stderr 提示；`init --currency` 只对新账本有效，想在另一种币种下记账请换一个新的 `--db` 文件。

---

## 目录结构

```
.
├─ centsible/
│  ├─ domain/                 # 纯业务对象与规则（无 I/O）
│  │  ├─ money.py             # Decimal/Money、精度、舍入、格式化
│  │  ├─ accounts.py          # 科目类型、正常余额方向、层级
│  │  ├─ transactions.py      # Posting / Transaction，借贷平衡不变量
│  │  ├─ periods.py           # 期间、月份算术、财年
│  │  ├─ budgets.py           # 预算与差异分析
│  │  └─ recurrence.py        # 周期规则与排期
│  ├─ application/            # 用例层
│  │  ├─ ports.py             # 仓储/UoW/时钟/对账单读取端口（Protocol）
│  │  ├─ dto.py               # 报表与操作结果 DTO（含 to_dict）
│  │  ├─ reporting.py         # 纯函数：试算平衡/利润表/资产负债表/明细账/预算
│  │  ├─ validators.py        # 可记账科目校验等共享规则
│  │  ├─ account_service.py   # 科目用例
│  │  ├─ ledger_service.py    # 记账、作废、关账
│  │  ├─ report_service.py    # 取数 + 调用纯函数
│  │  ├─ budget_service.py    # 预算用例
│  │  ├─ recurring_service.py # 周期分录取数/物化
│  │  ├─ import_service.py    # 对账单导入（指纹幂等）
│  │  ├─ export_service.py    # 导出数据构建（CSV/JSON/Excel 凭证表）
│  │  ├─ workbook.py          # 工作簿/工作表 DTO（列类型：文本/金额/日期/数字）
│  │  ├─ report_pack.py       # 九张工作表的 Excel 报表包
│  │  └─ settings.py          # 币种/财年等账本设置
│  ├─ infrastructure/         # 适配器
│  │  ├─ schema.py            # 版本化 DDL（写在 Python 里）
│  │  ├─ database.py          # 连接、迁移、完整性检查、备份与恢复
│  │  ├─ repositories.py      # 七个 SQLite 仓储实现
│  │  ├─ uow.py               # 工作单元（一个事务一个 UoW）
│  │  ├─ csvio.py             # CSV 写出/读取
│  │  ├─ statements.py        # 对账单读取器（CSV/TSV 与 Excel 共用一套映射解析）
│  │  ├─ xlsx.py              # 零依赖的 .xlsx 读写（zipfile + ElementTree）
│  │  ├─ jsonio.py            # JSON 备份
│  │  ├─ clock.py             # SystemClock / FixedClock
│  │  └─ logging_setup.py     # 日志
│  ├─ interface/               # 两种前端，共用同一套服务
│  │  ├─ launcher.py            # 入口路由：按配置选择命令行 / 窗口
│  │  ├─ cli/                   # 命令行
│  │  │  ├─ main.py             # 参数解析、异常→退出码
│  │  │  ├─ context.py          # 惰性构建服务的执行上下文
│  │  │  ├─ table.py            # 中文/宽字符对齐的表格渲染
│  │  │  ├─ formatting.py       # 金额、日期、状态格式化
│  │  │  ├─ parsing.py          # ACCOUNT=AMOUNT[:MEMO]、期间参数
│  │  │  └─ commands/           # 7 个命令模块
│  │  └─ gui/app.py             # 零依赖 tkinter 窗口：总览/凭证/录入/报表
│  ├─ config.py                 # TOML 配置（含 [interface] 开关）
│  ├─ errors.py               # 错误层次 + 退出码
│  ├─ bootstrap.py            # 组合根（唯一知道所有层的地方）
│  └─ demo.py                 # 演示数据
├─ tests/                     # 520 个 unittest 用例
├─ tools/                     # 开发期工具，不是运行时依赖
│  ├─ check_comments.py       # 校验每处注释/docstring 是否中英双语
│  └─ ast_guard.py            # 用 AST 对比证明「只改注释」没动可执行代码
├─ docs/
│  ├─ architecture.md         # 分层、依赖方向、关键取舍
│  ├─ business-rules.md       # 完整业务规则清单
│  └─ style.md                # 注释规范：双语格式、术语表、校验方式
├─ pyproject.toml
└─ README.md
```

---

## 设计要点

1. **借符号表示借贷**：`Posting.amount` 正数=借、负数=贷，于是「借贷平衡」就是一次求和，作废/冲销都可以归约为取负。
2. **金额三态一致**：`Decimal`（领域）↔ 整数分（存储）↔ `Money`（展示/跨币种边界）。数据库里没有浮点列。
3. **纯函数报表**：`reporting.py` 只接收「科目 + 聚合金额」并返回 DTO，因此资产负债表可以只用字典测通，不需要数据库。
4. **端口/适配器**：服务只依赖 `ports.py` 里的 `Protocol`，SQLite 是可替换细节；测试里可以直接换成内存实现。
5. **工作单元**：一次用例一个 SQLite 事务，仓储共享同一连接；忘记 `commit` 会自动回滚，"部分写入" 不会发生。
6. **显式依赖注入**：`bootstrap.build_services()` 是唯一的组合根，其余对象全部构造注入，测试可以注入 `FixedClock` 冻结时间。
7. **业务规则前置**：未来日期、关账期间、归档科目、汇总科目、重复引用、非零余额归档……都在这条链路里被拒绝，并给出可读错误信息。
8. **审计优先**：`audit_log` 记录所有状态变更；凭证只作废不删除；关账只能前进不能后退（重开需 `--force`）。
9. **零依赖读写 Excel**：`.xlsx` 本质是 ZIP + XML，用 `zipfile` 与 `ElementTree` 直接读写。金额写成真数字单元格、日期写成真日期单元格、表头冻结 + 自动筛选；导入时能识别 Excel 的日期/数字格式。测试里用 openpyxl 做双向互操作验证（只用于测试，不是依赖）。
10. **注释与 docstring 全量双语**：每处注释、每个 docstring 都同时有中英两个版本——英文在前、中文紧随其后；行尾注释用 ` | ` 分隔（超长则中文另起一行）。术语按 [docs/style.md](docs/style.md) 统一，避免同一个概念出现两种译法。规范不靠自觉：`tools/check_comments.py` 可复跑校验，`tools/ast_guard.py` 用 AST 对比证明改动只落在注释上，`tests/test_comment_style.py` 把它变成回归测试。

更多细节见 [docs/architecture.md](docs/architecture.md)、[docs/business-rules.md](docs/business-rules.md) 与 [docs/style.md](docs/style.md)。

---

## 测试

```bash
# 全部用例（unittest，无需 pytest）
python -m unittest discover -s tests -t . -v

# 单个模块
python -m unittest tests.test_ledger_service -v
```

测试覆盖：金额/Money、科目与凭证不变量、期间算术、报表纯函数、SQLite 迁移与仓储、六个应用服务、CSV 导入、导出、配置、表格渲染、CLI 端到端（含退出码）。

其中两组值得单独说明：

- **不变量随机测试**（`tests/test_invariants.py`）：用固定种子生成 120 笔平衡凭证（跨两个财年、覆盖全部科目类型、部分作废），然后只从已存储的数据行出发重算——试算平衡、资产负债表平衡、逐科目余额、利润表与月度汇总互相印证、关账不改变任何报表数字。手写用例只能覆盖想得到的情况，这一组覆盖「没想到的」。
- **规范测试**（`tests/test_comment_style.py`）：扫描全项目的注释与 docstring，确保中英双语规范不会退化；配套的 `tools/check_comments.py` 与 `tools/ast_guard.py` 可以单独运行，用于审查一次「只改注释」的改动。

```bash
python tools/check_comments.py            # 注释/docstring 双语覆盖率
python tools/check_comments.py --json     # 机器可读，便于接 CI
python tools/ast_guard.py --against <原始副本> --all   # 证明代码语义零改动
```

---

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 1 | 运行时错误 / 中断 |
| 2 | 输入不合法（校验失败、借贷不平、日期非法……） |
| 3 | 对象不存在（科目、凭证、周期规则……） |
| 4 | 与当前状态冲突（重复引用、重复作废、关账后退……） |
| 5 | 数据完整性问题（`db check` 失败、迁移失败） |
| 6 | 期间已关账 |
| 78 | 配置错误 |

---

## 可扩展点

- **换存储**：实现 `ports.py` 中的七个协议即可（例如 PostgreSQL、内存测试替身）。
- **换币种/多币种**：`LedgerSettings` 已把币种与精度集中；若要支持外币，需要新增汇率表与折算规则。
- **新报表**：在 `reporting.py` 写纯函数 + 在 `report_service.py` 取数 + 在 `dto.py` 加 DTO。
- **新导入格式**：实现 `StatementReader` 协议即可（CSV 与 Excel 已各有一个实现；OFX、QIF 同理），导入逻辑与幂等指纹无需改动。
- **脚本化**：所有命令都有 `--json`，可被外部程序直接消费。

---

## 已知取舍

- **单账本单本位币**：不处理汇率与多币种重估，这是为保持「金额精确、报表可对账」而做的取舍。
- **关账是单向的**：`journal lock` 只能向前推进；重开需要显式 `--force`，并留下审计记录。
- **作废是「撤销 + 留痕」**：被作废凭证不计入任何余额，历史报表会随之后移；关账前可用 `tx restore` 撤销这次作废。若需要「按时点保留更正」，应改为冲销分录模式（在 `Transaction.voided` 之外扩展）。
- **Excel 支持是「够用就好」**：写侧不做公式、图表、合并单元格与宏；读侧取公式的缓存值，不做样式级解析。报表包与对账单导入不需要这些，写它们只会增加无价值的风险。
- **科目代码不可改**：它是所有分录与报表的稳定键；名称、描述、父子关系可以改。
- **没有用户体系**：`audit_log.actor` 默认 `cli`，需要多用户时应由调用方传入身份。

---

## 参与开发

- 运行测试：`python -m unittest discover -s tests -t . -v`（零依赖，不需要 pytest；没有 tkinter 的环境会自动跳过窗口测试）
- 注释规范：所有注释与 docstring 中英双语，用 `python tools/check_comments.py` 自检（CI 里也会跑）
- 改动「只碰注释」时，`tools/ast_guard.py --against <原始副本> --all` 能用 AST 对比证明代码语义没变
- 可选的静态检查（仅开发时安装，运行时仍然零依赖）：
  ```bash
  pip install mypy ruff
  python -m mypy centsible
  python -m ruff check centsible tests tools --select F,E9
  ```
- 提交前建议再跑一次 `python -m centsible --cli db check`

---

## License

MIT，见 [LICENSE](LICENSE)。
