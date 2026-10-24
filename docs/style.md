# 注释与文档字符串规范 / Comment and docstring style

> 本项目要求**每一处注释、每一个 docstring 都同时提供中文与英文**。
> Every comment and every docstring in Centsible must carry **both Chinese and English**.

原因很简单：代码的读者不止一种。有人靠英文读代码，有人靠中文读得更快；把两种语言并排放，
两边都不用猜。这是一个可以机械校验的规范，因此不会随人而变。

The rule exists because the code has two audiences. Keeping both languages side by side
means neither has to guess. It is also mechanically checkable, so it cannot rot.

---

## 1. 三条硬规则 / Three hard rules

| 规则 | 说明 |
| --- | --- |
| **只改注释** | 一次“加中文”的提交只允许改注释和 docstring。任何可执行代码的变化都是 bug，用 `tools/ast_guard.py` 验证。 |
| **只增不改** | 已有的英文原封不动地保留（不重写、不缩写、不纠正拼写）；你的工作是在它旁边**增加**中文。 |
| **术语一致** | 会计术语按 [附录术语表](#7-术语表--glossary) 统一翻译，不许同一个概念出现两种译法。 |

| Rule | Meaning |
| --- | --- |
| **Comments only** | A "add Chinese" change may touch comments and docstrings only. Any change to executable code is a bug; `tools/ast_guard.py` proves it. |
| **Additive only** | Existing English stays verbatim (no rewording, no shortening, no typo fixes). Your job is to *add* the Chinese next to it. |
| **One glossary** | Accounting terms follow the [glossary](#7-术语表--glossary); one concept gets one translation. |

---

## 2. 文档字符串 / Docstrings

英文正文保持不变，空一行，然后写中文译文。中文译文沿用原有的段落结构（原文几段，译文几段）。

The English body stays as it is, then one blank line, then the Chinese translation,
mirroring the paragraph structure of the original.

单行：

```python
def record(self, ...) -> Transaction:
    """Validate and store one balanced journal entry.

    校验并保存一笔平衡的凭证。"""
```

多行：

```python
class LedgerService(ServiceBase):
    """Recording, voiding and locking journal entries.

    This service is the gatekeeper of the journal.  Before a transaction reaches
    the database it must be balanced and well formed.

    日记账凭证的记录、作废与锁账。

    本服务是日记账的守门人。凭证在进入数据库之前必须平衡、格式合规。
    """
```

要点 / Notes:

- 结束引号的位置不变（原来是单独一行就还是单独一行，原来贴在句末就还贴在句末）。
  The closing quotes stay where they were.
- Sphinx 字段名不翻译，只翻译字段说明：
  `:param date: Value date of the entry. / 凭证的记账日期。`
- 类、函数、模块的 docstring 一视同仁，没有例外。
  Module, class and function docstrings all follow the same rule.
- 不要在 docstring 里写“中文：/ English:”这类多余的标签，语言本身就能分开。
  No "Chinese:" / "English:" labels; the scripts are self-evident.

---

## 3. 独立成行的注释块 / Standalone comment blocks

英文行留在原处，紧跟其后逐行补上中文，缩进与注释前缀（`#`、`#:`、`##`）完全一致。

Keep the English lines where they are; add the Chinese directly below with the same
indentation and the same prefix (`#`, `#:`, `##`).

```python
#: Key in the ``meta`` table holding the last closed day.
#: meta 表中保存最后已关闭日的键。
LOCK_DATE_KEY = "lock_date"
```

多段注释按段翻译，空注释行（只有一个 `#`）保留，用于分隔段落。
Multi-paragraph blocks are translated paragraph by paragraph; bare `#` separator lines
are kept.

```python
#: Key in the ``meta`` table holding the ledger's currency.
#:
#: Amounts are stored as integer minor units, so the currency decides what a
#: stored number *means*.
#:
#: meta 表中保存账簿币种的键。
#:
#: 金额以整数最小货币单位存储，因此币种决定了这个数字“意味着什么”。
```

分隔线注释（banner）同样双语，第二行重复英文关键词即可，不必强行造词：

```python
# ---------------------------------------------------------------------- add
# ---------------------------------------------------------------------- 新增 (add)
def cmd_add(ctx: CliContext, args: argparse.Namespace) -> int:
```

---

## 4. 行尾注释 / Trailing comments

中文接在同一行，用 ` | ` 分隔，英文在前。

Append the Chinese to the same line, separated by ` | `, English first.

```python
size = 20  # Default page size | 默认分页大小
```

如果这一行会超过 100 个显示列，就把中文单独放到代码行的**下一行**，缩进对齐：

If the line would exceed 100 display columns, put the Chinese on its own line directly
**below** the code line, at the same indentation:

```python
amounts = some_very_long_expression(...)  # Amounts in posting order
# 按分录顺序排列的金额
```

---

## 5. 中文写作要求 / Writing the Chinese

- 译“意思”，不译“单词”。优先让句子像中文技术写作，而不是英语语序的转写。
  Translate the meaning, not the words.
- 使用全角标点：`，。；：（）——、`；反引号、代码标识符、数字保持 ASCII。
  Full-width punctuation for Chinese; code and identifiers stay ASCII.
- 不要出现“验证并存储一个平衡的日志条目”这种机翻腔——本项目里 transaction 是**凭证**，
  journal 是**日记账**。
  No machine-translation tone; use the project glossary.
- 注释里不要新增原文没有的判断、承诺或示例。原文说多少就说多少。
  Do not invent claims, promises or examples that the original does not make.
- 中文段落之间用空行分段，与英文段落一一对应。
  Chinese paragraphs mirror English paragraphs.

反例 / Anti-patterns:

| ✗ 不要这样 | ✓ 应该这样 |
| --- | --- |
| 只加中文，把英文删了 | 英文原样保留，中文另起一段 |
| 中文塞进英文句子中间 | 两种语言各自成块/成行 |
| 一个概念两种译法（transaction 时而“交易”时而“凭证”） | 统一用“凭证” |
| 用半角逗号写长句 | 用全角，`，。；：` |

---

## 6. 校验 / How it is enforced

```bash
# 1) 注释与 docstring 是否双语
python tools/check_comments.py                # 全项目
python tools/check_comments.py centsible      # 只看某个目录

# 2) 这次改动是否真的只改了注释（与原始副本比较 AST）
python tools/ast_guard.py --against <未改动的副本> --all

# 3) 规范本身也被测试守着
python -m unittest tests.test_comment_style -v
```

`check_comments.py` 的判定是启发式的：出现两个以上拉丁词（或一个五字母以上的词）算“有英文”，
出现两个以上中日韩字符算“有中文”。`# noqa`、`# type: ignore`、shebang、编码声明这类
工具指令自动豁免。

The check is heuristic: two Latin words (or one word of five letters) count as "has
English"; two CJK characters count as "has Chinese". Tool directives such as `# noqa`,
`# type: ignore`, the shebang and encoding cookies are exempt.

退出码 / Exit codes: `0` 全部合规，`1` 存在不合规的单元。

---

## 7. 术语表 / Glossary

| English | 中文 | 备注 |
| --- | --- | --- |
| transaction | 凭证 | 一张记账凭证，不是“交易” |
| posting / entry line | 分录 | 凭证里的一行借贷 |
| journal | 日记账 | 凭证的集合 |
| ledger | 账簿 | 整个账套 |
| account | 科目 | 会计科目 |
| chart of accounts | 科目表 | 默认科目清单 |
| debit / credit | 借方 / 贷方 | 金额符号由正负表示 |
| period | 会计期间 | 如 2025-03 |
| lock (a period) | 锁账 | 关账后不可再改 |
| void | 作废 | 保留行、排除出所有余额 |
| trial balance | 试算平衡表 | |
| balance sheet | 资产负债表 | |
| income statement | 利润表 | |
| subledger / ledger report | 明细账 | |
| budget | 预算 | |
| variance | 差异 | 预算与实际之差 |
| recurring rule | 周期规则 | 常设分录 |
| bank statement | 银行对账单 | 导入的来源文件 |
| import batch | 导入批次 | |
| fingerprint | 指纹 | 去重用的哈希 |
| dedupe | 去重 | |
| minor units | 最小货币单位 | 存储用整数“分” |
| rounding | 舍入 | half-up |
| payee | 收款方 | |
| reference | 参考号 | 幂等键 |
| tag | 标签 | |
| unit of work | 工作单元 | 一次事务边界 |
| repository | 仓储 | 持久化适配器 |
| port | 端口接口 | `Protocol` |
| adapter | 适配器 | 基础设施实现 |
| workbook / worksheet | 工作簿 / 工作表 | Excel |
| header / footer | 表头 / 页脚 | 表格与工作表 |
| schema / migration | 表结构 / 迁移 | |
| bootstrap | 启动装配 | 组合根 |
| dataclass | 数据类 | |
| smoke test | 冒烟测试 | |
