# 业务规则清单

本文是 Centsible 的「会计准则手册」。每一条规则都对应代码里的实现与测试，方便审计与二次开发。

---

## 1. 记账模型

### 1.1 五种科目

| 类型 | 正常余额 | 增加方向 | 报表归属 |
| --- | --- | --- | --- |
| `asset` 资产 | 借 | 借方 | 资产负债表 |
| `liability` 负债 | 贷 | 贷方 | 资产负债表 |
| `equity` 权益 | 贷 | 贷方 | 资产负债表 |
| `income` 收入 | 贷 | 贷方 | 利润表 |
| `expense` 费用 | 借 | 借方 | 利润表 |

`AccountType.natural_balance(net_debit)` 负责把「借 − 贷」的原始差额转换成该科目的自然方向：负债/权益/收入会翻转符号，因此在报表里贷方余额显示为正数。

### 1.2 分录符号

- `Posting.amount > 0`：借方
- `Posting.amount < 0`：贷方
- 一张凭证所有分录之和必须 **恰好为 0**（`Decimal` 精确比较，不允许容差）

### 1.3 凭证必须满足

| 规则 | 错误类型 |
| --- | --- |
| 日期必须是 `date`（拒绝 `datetime`） | `ValidationError` |
| 描述必填，≤ 200 字符 | `ValidationError` |
| 至少两条分录 | `ValidationError` |
| 分录金额不得为 0 | `ValidationError` |
| 分录合计为 0 | `UnbalancedEntryError`（`ValidationError` 子类） |
| payee ≤ 120、reference ≤ 64、memo ≤ 500 | `ValidationError` |
| 标签：小写、去重、排序、可带 `#`、≤ 10 个、每个 ≤ 32 字符 | `ValidationError` |

### 1.4 已过账凭证不可修改

没有「编辑凭证」命令。修正方式只有两种：

1. `tx void <id> --reason ...`：作废并留痕；
2. 再记一张方向相反的凭证（例如退款、红冲）。

---

## 2. 记账时的校验（按顺序）

`LedgerService.record()` 依次检查：

1. **领域校验**（构造 `Transaction` 时）：见 1.3。
2. **日期不能在未来**：`date > today` 时拒绝，除非调用方显式 `allow_future=True`（CLI 为 `--allow-future`，周期分录内部为真）。
3. **期间未关账**：`date <= lock_date` 时拒绝，抛 `LedgerLockedError`（退出码 6）。
4. **科目可记账**：
   - 必须存在 → `NotFoundError`（退出码 3）；
   - 不能已归档 → `ConflictError`（4）；
   - 不能是有子科目的「汇总科目」 → `ValidationError`（2）。
5. **引用唯一**：`reference` 非空且已存在 → `ConflictError`（4），异常上下文里带已有凭证 id。

任何一步失败，整个事务回滚 —— 不会出现「写了一半」。

---

## 3. 作废（void）

```
tx void <id> --reason "重复录入"
```

规则：

- **必须给出原因**，否则 `ValidationError`；
- 只能作废状态为 `posted` 的凭证；已作废 → `ConflictError`；
- 凭证所在的**日期**必须处于未关账期间（否则会篡改已出报表）→ `LedgerLockedError`；
- 作废不删除任何行：状态改为 `void`、写入 `void_reason`、写一条 `audit_log`；
- 作废后的凭证不再计入任何余额、报表与导出（`--posted-only` 之外仍可见，用于审计）。

**语义边界**：作废等价于「这笔业务从未发生」，因此历史时点的报表会随之变化。如果业务需要「按时点保留更正」（例如 3 月关账后 4 月才发现错账），应改为在 4 月记一张冲销凭证，而不是作废 3 月的凭证。

### 3.1 反作废（restore）

```
tx restore <id> [--reason "..."]
```

| 规则 | 说明 |
| --- | --- |
| 前提 | 凭证当前必须是 `void`；否则 `ConflictError` |
| 期间 | 凭证日期必须在未关账期间，否则 `LedgerLockedError` |
| 结果 | 状态回到 `posted`、清空 `void_reason`，重新计入所有余额 |
| 审计 | 记录 `transaction.restore`，detail 中保留原「作废原因」与本次恢复说明 |
| 用途 | 手滑作废的兜底，避免只能靠补账修正 |

---

## 4. 关账（期间锁定）

| 操作 | 规则 |
| --- | --- |
| `journal lock --date D` | `D` 不得晚于今天；不得早于当前关账日（只能向前推进）；写审计记录 |
| 记账/作废 | 日期 `≤ lock_date` 一律拒绝（退出码 6） |
| `journal unlock` | 必须 `--force`；清除关账日期并写审计记录 |
| 未来日期 | 不受关账影响，但默认仍拒绝（见 2.2） |

设计意图：关账把「已经出过的报表」冻结起来；要改动只能显式重开，并留下审计痕迹。

---

## 5. 会计科目

| 操作 | 规则 |
| --- | --- |
| 新增 | 代码唯一（自动转大写，`[0-9A-Z][0-9A-Z._-]*`，≤ 32 字符）；名称必填 ≤ 120 |
| 父科目 | 必须存在且未归档；**类型必须相同**；不能是自己；不能形成环 |
| 修改 | 只能改名称、描述、父科目；**代码与类型不可变**（它们是所有分录与报表的稳定键） |
| 归档 | 有未归档子科目 → 拒绝；**余额不为 0 → 拒绝**（先结转再归档）；归档后不能再记账 |
| 恢复 | 直接把 `archived` 置回 `false` |
| 删除 | 有子科目 → 拒绝；有任何分录引用 → 拒绝（请归档）；只有「从未使用」的科目能删 |

科目排序处处按 `code` 升序，保证报表稳定。

---

## 6. 报表

### 6.1 试算平衡表（trial balance）

- 范围：期间内（或全部）已过账分录；
- 每行：`借方合计`、`贷方合计`、`自然余额`；
- 结果必须满足 `Σ借方 == Σ贷方`，否则 `balanced=False`（这种情况说明数据被破坏，`db check` 会报错）。

### 6.2 利润表（income statement）

```
收入合计 = Σ income 科目自然余额（贷为正）
费用合计 = Σ expense 科目自然余额（借为正）
净利润   = 收入合计 − 费用合计
```

默认期间为**当前月**；可 `--month` 或 `--from/--to`。

### 6.3 资产负债表（balance sheet）

```
资产 = Σ asset 自然余额
负债 = Σ liability 自然余额
权益 = Σ equity 自然余额 + 以前年度损益留存 + 本年利润
本年利润   = 本财年收入 − 本财年费用
留存收益   = 以前年度收入 − 以前年度费用
恒等式：资产 = 负债 + 权益
```

「本财年」由配置的 `fiscal_year_start_month` 决定（默认 1 月）。系统不做结转分录，因此留存收益与本年利润是**计算出来的**，避免出现「未结转导致权益缺失」的经典问题。

### 6.4 明细账（account ledger）

- 期初余额 = 该科目在期间开始日之前的自然余额；
- 每条分录给出借/贷发生额与**累计余额**（自然方向）；
- 期末余额 = 期初 + 期间发生净额。

### 6.5 月度汇总（summary）

按自然月给出收入、费用、净额，以及区间合计。

---

## 7. 预算

| 规则 | 说明 |
| --- | --- |
| 只允许利润表科目 | 资产/负债/权益科目设置预算 → `ValidationError` |
| 金额非负 | 预算是「计划值」，方向由科目类型决定 |
| 精度 | 最多等于币种小数位（CNY 为 2），否则拒绝 |
| 唯一性 | 同一科目同一月份只有一条，重复设置即覆盖 |
| 差异 | `差异 = 计划 − 实际` |
| 有利/不利 | 费用：实际 ≤ 计划 → 有利；收入：实际 ≥ 计划 → 有利 |
| 复制 | `budget copy` 按月整批复制，默认不覆盖已有计划 |
| 报表期间 | 必须有界（不能是「全部时间」） |

报表会同时列出「有预算」与「有实际发生」的科目，因此未预算的支出不会被隐藏。

---

## 8. 周期分录（standing entries）

| 规则 | 说明 |
| --- | --- |
| 模板 | 必须配平、至少两行；科目在创建时校验可记账 |
| 唯一名 | 规则名唯一 |
| 频率 | `daily / weekly / monthly / quarterly / yearly`，可带 `interval`（每 N 个周期） |
| 锚定 | 以 `start_date` 为锚点，月末自动夹取：1 月 31 日 → 2 月 28 日 → 3 月 31 日（不会漂移成 28 日） |
| `end_date` | 到期后规则自动转为 `paused` |
| 运行 | `recurring run [--until D]` 把到期时点物化成真实凭证，并把 `next_date` 推到最后一个已处理时点之后 |
| 幂等 | 同一规则重复运行不会重复生成 |
| 出错 | 某时点失败（关账、科目归档……）会记录到 `issues`，**仍然前进**，避免一条坏规则永久阻塞队列 |
| 未来日期 | 允许（房租常在月初预提）；关账限制仍然生效 |
| 暂停/恢复/删除 | 删除规则不影响它已生成的凭证 |

---

## 9. 对账单导入

支持 **CSV/TSV/TXT 与 Excel（`.xlsx`/`.xlsm`）**，两种格式走同一套规则：

| 规则 | 说明 |
| --- | --- |
| 每行一张凭证 | 借/贷：`statement account` 与 `counter account`，金额互为相反数 |
| 金额方向 | 正数=钱进账单账户；`--amount-sign -1` 可整体翻转 |
| 拆列 | 可配置「支出列 / 收入列」代替单一金额列（`amount = 收入 − 支出`；两列都填正数） |
| 列定位 | 支持表头名或 0 起始列号；无表头时必须用列号（两种格式通用） |
| Excel 选表 | `--sheet NAME`（或序号）；缺省取第一个工作表；找不到表名会列出可用表 |
| Excel 类型 | 日期单元格按真实日期解析（识别内置/自定义日期格式与 1904 日期系统），数字单元格直接作为金额 |
| 日期格式 | 文本日期默认 ISO，可用 `--date-format` 指定 `strftime` 格式 |
| 去重指纹 | `sha256(科目 | 日期 | 金额 | 外部引用)`；无引用列时用「日期 + 金额 + 描述」合成 |
| 幂等 | 同一文件重复导入 → 全部跳过；同一文件导入到不同科目 → 视为不同数据；CSV 与 Excel 导入同一份数据也会相互去重（指纹与格式无关） |
| 文件内重复行 | 同一文件里出现第二次相同引用号的行，默认按「重复导出」跳过，并在结果里单独计为 `repeated`（与「账本中已存在」的 `duplicates` 分开报告）；若确实是同一天两笔相同消费，用 `--keep-duplicate-lines` 全部导入。重复行在指纹里带 `#n` 序号，因此该开关依然是幂等的 |
| 科目匹配规则 | `--match-rule PATTERN=ACCOUNT`（可重复）：PATTERN 忽略大小写地出现在该行描述或收款方中时，该行的对方科目改用 ACCOUNT，否则回落到 `--counter-account`。多条规则按顺序尝试、先匹配者生效。规则科目必须存在、可记账且不是对账单科目本身。只有被规则命中的行才把解析出的科目写进指纹，因此：改规则只会把受影响的行重新导入到新科目（旧分类保留到手动作废），从未启用规则的导入仍然逐行去重 |
| 容错 | 单行解析失败只记录问题行与行号，其余照常导入；退出码 2 表示有失败行 |
| 精度 | 金额小数位超过币种精度 → 该行失败 |
| 批次 | 每次导入写一条 `import_batches`（文件、校验和、读取/导入/跳过行数） |
| 预览 | `--dry-run` 只统计与校验，不写任何数据 |
| 格式识别 | 按扩展名分派（`.csv/.tsv/.txt` 与 `.xlsx/.xlsm`），其他类型直接拒绝并列出支持的类型 |

导出侧：`export workbook` 产出九张工作表的报表包（Summary、月度活动、试算平衡、利润表、资产负债表、预算、凭证流水、周期规则、科目表）；`export journal --out *.xlsx` 按扩展名自动改写成类型化的 Excel 凭证表。报表包与终端报表使用同一套 DTO 与纯函数，数字不可能不一致。

---

## 10. 金额、精度与舍入

| 规则 | 说明 |
| --- | --- |
| 类型 | 领域层 `Decimal`；数据库整数分；展示用 `Money` |
| 拒绝浮点 | 传入 `float` 直接报错，避免二进制误差 |
| 解析 | 允许千分位逗号、空格、下划线、`+/-`、以及会计负数写法 `(1,234.56)` |
| 量化 | 按币种精度四舍五入（half-up），`-0.00` 归一化为 `0.00` |
| 精度校验 | 输入小数位多于币种精度 → `ValidationError`（不做静默舍入） |
| 零金额分录 | 拒绝（几乎总是录入错误） |
| 币种混算 | `Money` 相加/相减/比较必须同币种，否则 `CurrencyMismatchError` |
| 未知币种 | 三位字母代码合法即接受，精度默认 2（JPY/KRW 等为 0，BTC/ETH 为 8） |
| 币种固定 | 首次 `init` 把币种写入 `meta['currency']`，之后不可更改；配置与账本不一致时以账本为准并告警 |

---

## 11. 期间与财年

- 期间是**闭区间**（含首尾日期）；
- 支持 `YYYY-MM`、`YYYY-Qn`、`YYYY-MM-DD` 三种写法；
- `--month` 与 `--from/--to` 互斥；
- 只给 `--from` 表示「从该日至今后所有」，只给 `--to` 表示「至今为止」；
- 月份加减会夹取到月末：`2025-01-31 + 1 月 = 2025-02-28`；
- 财年起始月可配置（默认 1），用于拆分「本年利润」与「以前年度损益」。

---

## 12. 审计

所有状态变更都会写 `audit_log`：

| action | 触发 |
| --- | --- |
| `account.create` / `account.update` / `account.archive` / `account.restore` / `account.delete` / `account.import_chart` | 科目变更 |
| `transaction.record` / `transaction.void` / `transaction.restore` | 凭证变更 |
| `ledger.initialise` / `ledger.close` / `ledger.unlock` | 建账币种、关账与重开 |
| `budget.set` / `budget.clear` / `budget.copy` | 预算变更 |
| `recurring.create` / `recurring.pause` / `recurring.resume` / `recurring.delete` / `recurring.run` | 周期规则 |
| `statement.import` | 对账单导入 |

`journal audit`（或 `journal audit --limit N`）查看最近记录；`audit_log` 只追加，不更新、不删除。

---

## 13. 一致性检查

`db check` 做三件事：

1. SQLite `PRAGMA integrity_check` 与 `PRAGMA foreign_key_check`；
2. 试算平衡校验：全量凭证的借方合计必须等于贷方合计；
3. **逐张凭证**校验：每张凭证自己的分录合计必须为零（作废的也算）。

第 3 项不是第 2 项的子集：把 5 分从一张凭证挪到另一张凭证时，账簿合计仍然平衡，
但两张凭证各自都不平衡了。这正是「手工改过数据库」最容易留下的痕迹，所以 `db check`
逐张核对，并输出 `unbalanced_transactions`（JSON）便于脚本定位。

任何一项失败 → 退出码 5。数据异常时优先使用 `db backup` 保留现场，再排查。

### 13.1 从备份恢复（`db restore`）

| 规则 | 说明 |
| --- | --- |
| 先校验后覆盖 | 备份必须存在、通过 `PRAGMA integrity_check`、表结构版本不高于本程序、并且包含 Centsible 必需的表（`accounts`/`transactions`/`postings`/`meta`），否则拒绝恢复 |
| 校验先于确认 | 备份缺失或损坏时报 3 / 5，而不是要求 `--force`；只有备份本身可用时，才轮到“是否覆盖”这道确认 |
| 有凭证需确认 | 当前账簿已有凭证时，必须显式 `--force`；空账簿或不存在时直接恢复 |
| 不能自覆盖 | 备份与账簿是同一个文件时拒绝 |
| 恢复留痕 | 恢复成功后，动作本身（`database.restore` + 文件名）写入恢复后账簿的审计轨迹 |
| 不可用于内存库 | 只有文件账簿可以恢复 |

失败退出码：文件不存在 3、校验失败 5、缺少 `--force` 4。

---

## 14. 规则与测试的对应关系

| 规则 | 主要测试 |
| --- | --- |
| 借贷平衡、分录符号 | `tests/test_transactions_domain.py` |
| 金额精度、舍入、币种 | `tests/test_money.py` |
| 期间与财年 | `tests/test_periods.py` |
| 报表恒等式 | `tests/test_reporting.py`、`tests/test_report_service.py` |
| 记账校验、关账、作废 | `tests/test_ledger_service.py` |
| 科目层级与归档 | `tests/test_account_service.py` |
| 预算 | `tests/test_budget_service.py` |
| 周期分录 | `tests/test_recurring_service.py` |
| Excel 读写（往返、错误、openpyxl 互操作） | `tests/test_xlsx.py` |
| OOXML 命名空间精确、包内无明文 URL 字面量 | `tests/test_xlsx.py`（NamespaceHygieneTests） |
| 导入幂等与容错 | `tests/test_import_service.py` |
| 文件内重复行（跳过 / `--keep-duplicate-lines`） | `tests/test_import_service.py`、`tests/test_statements.py` |
| 科目匹配规则（`--match-rule`） | `tests/test_import_service.py`、`tests/test_cli.py` |
| 从备份恢复（`db restore`） | `tests/test_cli.py`（DatabaseCliTests） |
| 双界面开关与入口路由（`[interface]`） | `tests/test_config.py`、`tests/test_launcher.py` |
| 窗口界面（总览/凭证/录入/报表） | `tests/test_gui.py`（无显示环境自动跳过） |
| 全部恒等式（随机生成的数据） | `tests/test_invariants.py` |
| CLI 退出码与输出 | `tests/test_cli.py` |

运行全部测试：

```bash
python -m unittest discover -s tests -t . -v
```
