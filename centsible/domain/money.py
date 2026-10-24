"""Exact monetary arithmetic.

Amounts are never represented as ``float``: binary floating point cannot hold
0.10 exactly, and a ledger that drifts by a cent after ten thousand entries is
worthless.  Centsible keeps :class:`decimal.Decimal` values in the domain and
integer *minor units* (cents) in the database, so every balance is exact and
every report reconciles.

Two representations exist on purpose:

``Decimal``
    Currency-agnostic bookkeeping amounts.  The ledger itself is single
    currency, so postings do not repeat the currency code on every row.
``Money``
    A ``Decimal`` plus its currency code, used at the edges (reports, CLI, CSV
    import) where mixing currencies by accident would be a real bug.

Rounding is half-up, the convention used by commercial accounting.

精确的货币运算。

金额从不以 ``float`` 表示：二进制浮点数无法精确表示 0.10，而一本在录入一万笔
分录后偏差一分的账簿毫无价值。Centsible 在领域层保存 :class:`decimal.Decimal`
值，在数据库中保存整数*最小货币单位*（分），因此每个余额都是精确的，每份报表
都能对得上账。

故意保留两种表示：

``Decimal``
    与币种无关的记账金额。账簿本身是单一币种，因此分录不必在每一行重复币种代码。
``Money``
    一个 ``Decimal`` 加上币种代码，用于边界处（报表、CLI、CSV 导入）——在这些
    地方意外混用币种，就是实实在在的缺陷。

舍入采用 half-up，即商业会计通行的进位约定。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import total_ordering
from typing import Iterable

from centsible.errors import CurrencyMismatchError, ValidationError

__all__ = [
    "DEFAULT_CURRENCY",
    "MAX_EXPONENT",
    "Money",
    "coerce_decimal",
    "currency_exponent",
    "format_amount",
    "from_minor",
    "normalize_currency",
    "parse_amount",
    "parse_decimal",
    "quantize",
    "sum_decimals",
    "to_minor",
]

DEFAULT_CURRENCY = "CNY"
MAX_EXPONENT = 8

#: ISO 4217 minor-unit exponents for the currencies we know about.  Unknown but
#: syntactically valid codes fall back to two decimal places.
#: 本模块已知币种的 ISO 4217 最小货币单位指数。语法合法但未收录的代码
#: 回退到两位小数。
_CURRENCY_EXPONENTS: dict[str, int] = {
    "AUD": 2,
    "BRL": 2,
    "CAD": 2,
    "CHF": 2,
    "CNY": 2,
    "DKK": 2,
    "EUR": 2,
    "GBP": 2,
    "HKD": 2,
    "IDR": 2,
    "INR": 2,
    "MXN": 2,
    "MYR": 2,
    "NOK": 2,
    "NZD": 2,
    "PHP": 2,
    "PLN": 2,
    "SEK": 2,
    "SGD": 2,
    "THB": 2,
    "USD": 2,
    "USDT": 2,
    "CLP": 0,
    "ISK": 0,
    "JPY": 0,
    "KRW": 0,
    "VND": 0,
    "XAF": 0,
    "XOF": 0,
    "BTC": 8,
    "ETH": 8,
}

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_AMOUNT_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")


# ------------------------------------------------------------------ primitives
# ------------------------------------------------------------------ 基础工具
def normalize_currency(code: str) -> str:
    """Validate and canonicalise an ISO 4217 currency code.

    校验并规范化 ISO 4217 币种代码。"""

    if not isinstance(code, str):
        raise ValidationError("currency must be a three letter ISO code", field="currency")
    text = code.strip().upper()
    if not _CURRENCY_RE.match(text):
        raise ValidationError(
            f"{code!r} is not a three letter ISO currency code", field="currency"
        )
    return text


def currency_exponent(code: str) -> int:
    """Number of decimal places used by ``code`` (2 by default).

    ``code`` 使用的小数位数（默认为 2）。"""

    return _CURRENCY_EXPONENTS.get(normalize_currency(code), 2)


def quantize(value: Decimal, exponent: int) -> Decimal:
    """Round ``value`` to ``exponent`` decimal places, half-up.

    Negative zero is normalised to zero so that formatting never prints "-0.00".

    把 ``value`` 舍入到 ``exponent`` 位小数，采用 half-up 进位。

    负零会被归一化为零，因此格式化时永远不会打印出 "-0.00"。
    """

    if not isinstance(value, Decimal):
        raise TypeError(f"quantize() expects Decimal, got {type(value).__name__}")
    if isinstance(exponent, bool) or not isinstance(exponent, int):
        raise TypeError("exponent must be an int")
    if not 0 <= exponent <= MAX_EXPONENT:
        raise ValueError(f"exponent must be between 0 and {MAX_EXPONENT}")

    rounded = value.quantize(Decimal(1).scaleb(-exponent), rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = rounded.copy_abs()
    return rounded


def parse_decimal(text: str) -> Decimal:
    """Parse human input such as ``"1,234.50"``, ``"-12"`` or ``"(7.25)"``.

    Parentheses denote a negative amount, matching the convention of exported
    bank statements.  Thousands separators, underscores and spaces are ignored.

    解析 ``"1,234.50"``、``"-12"``、``"(7.25)"`` 这类人工输入。

    圆括号表示负数金额，与导出的银行对账单惯例一致。千分位分隔符、下划线以及
    空格都会被忽略。
    """

    if isinstance(text, Decimal):
        return text
    if not isinstance(text, str):
        raise ValidationError(f"cannot interpret {type(text).__name__} as an amount", field="amount")

    raw = text.strip()
    if not raw:
        raise ValidationError("amount is required", field="amount")

    negative = False
    if raw.startswith("(") and raw.endswith(")"):
        negative = True
        raw = raw[1:-1].strip()

    cleaned = (
        raw.replace(",", "")
        .replace("_", "")
        .replace("\u00a0", "")
        .replace(" ", "")
    )
    if cleaned[:1] in {"+", "-"}:
        if cleaned[0] == "-":
            negative = not negative
        cleaned = cleaned[1:]

    if not _AMOUNT_RE.match(cleaned):
        raise ValidationError(f"{text!r} is not a valid amount", field="amount")

    try:
        value = Decimal(cleaned)
    except InvalidOperation as exc:  # pragma: no cover - regex already guards this
        raise ValidationError(f"{text!r} is not a valid amount", field="amount") from exc

    return -value if negative else value


def coerce_decimal(value: Decimal | int | str) -> Decimal:
    """Turn the supported amount types into a finite ``Decimal``.

    ``float`` is rejected on purpose: silently accepting it would reintroduce
    the rounding errors this module exists to avoid.

    把受支持的金额类型转换为有限的 ``Decimal``。

    故意拒绝 ``float``：静默接受它会重新引入本模块正是要避免的舍入误差。
    """

    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, bool):
        raise ValidationError("a boolean is not an amount", field="amount")
    elif isinstance(value, int):
        result = Decimal(value)
    elif isinstance(value, float):
        raise ValidationError(
            "float amounts are not accepted because they cannot represent cents exactly; "
            "pass a string or a Decimal instead",
            field="amount",
        )
    elif isinstance(value, str):
        result = parse_decimal(value)
    else:
        raise ValidationError(
            f"cannot interpret {type(value).__name__} as an amount", field="amount"
        )

    if not result.is_finite():
        raise ValidationError("amount must be a finite number", field="amount")
    return result


def parse_amount(text: str | Decimal | int, *, exponent: int, field: str = "amount") -> Decimal:
    """Parse and quantise an amount, rejecting excess decimal places.

    解析金额并舍入到指定小数位，超出的小数位一律拒绝。"""

    value = coerce_decimal(text)
    quantized = quantize(value, exponent)
    if quantized != value:
        raise ValidationError(
            f"at most {exponent} decimal place(s) are allowed, got {value}", field=field
        )
    return quantized


def to_minor(amount: Decimal, exponent: int) -> int:
    """Convert a major-unit ``Decimal`` to integer minor units.

    把主单位的 ``Decimal`` 转换为整数最小货币单位。"""

    quantized = quantize(amount, exponent)
    scaled = quantized.scaleb(exponent)
    if scaled != scaled.to_integral_value():  # pragma: no cover - quantum guarantees this
        raise ValidationError(f"{amount} does not fit {exponent} decimal places")
    return int(scaled)


def from_minor(minor: int, exponent: int) -> Decimal:
    """Convert integer minor units back to a major-unit ``Decimal``.

    把整数最小货币单位转换回主单位的 ``Decimal``。"""

    if isinstance(minor, bool) or not isinstance(minor, int):
        raise TypeError("minor units must be an int")
    return quantize(Decimal(minor).scaleb(-exponent), exponent)


def format_amount(value: Decimal, *, exponent: int = 2, thousands: bool = True) -> str:
    """Render ``value`` with a fixed number of decimals and optional grouping.

    把 ``value`` 渲染成固定小数位、可选千分位分组的文本。"""

    rounded = quantize(value, exponent)
    negative = rounded < 0
    text = f"{abs(rounded):f}"
    if exponent:
        integer, _, fraction = text.partition(".")
        fraction = (fraction + "0" * exponent)[:exponent]
    else:
        integer = text.partition(".")[0]
        fraction = ""
    if thousands:
        integer = f"{int(integer):,}"
    body = f"{integer}.{fraction}" if exponent else integer
    return f"-{body}" if negative else body


def sum_decimals(values: Iterable[Decimal]) -> Decimal:
    """Exact sum of an iterable of ``Decimal`` values (``sum`` starts at int 0).

    对一组 ``Decimal`` 值做精确求和（``sum`` 的起始值是 int 0）。"""

    total = Decimal(0)
    for value in values:
        total += value
    return total


# ----------------------------------------------------------------------- Money
# ----------------------------------------------------------------------- 金额
@total_ordering
@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount in a specific currency.

    ``Money`` refuses to mix currencies: adding CNY to USD raises
    :class:`~centsible.errors.CurrencyMismatchError` instead of silently
    producing a meaningless number.

    指定币种下的一个精确金额。

    ``Money`` 拒绝混用币种：把 CNY 与 USD 相加会抛出
    :class:`~centsible.errors.CurrencyMismatchError`，而不是静默算出一个毫无
    意义的数字。
    """

    amount: Decimal
    currency: str = DEFAULT_CURRENCY

    def __post_init__(self) -> None:
        currency = normalize_currency(self.currency)
        amount = quantize(coerce_decimal(self.amount), currency_exponent(currency))
        object.__setattr__(self, "amount", amount)
        object.__setattr__(self, "currency", currency)

    # ------------------------------------------------------------- factories
    # ------------------------------------------------------------- 工厂方法
    @classmethod
    def zero(cls, currency: str = DEFAULT_CURRENCY) -> "Money":
        return cls(Decimal(0), currency)

    @classmethod
    def of(cls, value: Decimal | int | str, currency: str = DEFAULT_CURRENCY) -> "Money":
        return cls(coerce_decimal(value), currency)

    @classmethod
    def from_minor(cls, minor: int, currency: str = DEFAULT_CURRENCY) -> "Money":
        return cls(from_minor(minor, currency_exponent(currency)), currency)

    @classmethod
    def parse(cls, text: str | Decimal | int, currency: str = DEFAULT_CURRENCY) -> "Money":
        return cls(parse_amount(text, exponent=currency_exponent(currency)), currency)

    @classmethod
    def sum(cls, values: Iterable["Money"], currency: str | None = None) -> "Money":
        items = list(values)
        if not items:
            return cls.zero(currency or DEFAULT_CURRENCY)
        target = normalize_currency(currency) if currency else items[0].currency
        total = cls.zero(target)
        for item in items:
            # ``__add__`` validates the currency, so a mixed list raises instead
            # of silently producing a meaningless total.
            # ``__add__`` 会校验币种，因此混用币种的列表会直接抛错，
            # 而不是静默得出一个毫无意义的合计。
            total = total + item
        return total

    # ------------------------------------------------------------ properties
    # ------------------------------------------------------------ 属性
    @property
    def exponent(self) -> int:
        return currency_exponent(self.currency)

    @property
    def minor_units(self) -> int:
        return to_minor(self.amount, self.exponent)

    @property
    def sign(self) -> int:
        if self.amount > 0:
            return 1
        if self.amount < 0:
            return -1
        return 0

    @property
    def is_zero(self) -> bool:
        return self.amount == 0

    @property
    def is_positive(self) -> bool:
        return self.amount > 0

    @property
    def is_negative(self) -> bool:
        return self.amount < 0

    # ------------------------------------------------------------- arithmetic
    # ------------------------------------------------------------- 算术运算
    def _check(self, other: object) -> "Money":
        if not isinstance(other, Money):
            raise TypeError(f"expected Money, got {type(other).__name__}")
        if other.currency != self.currency:
            raise CurrencyMismatchError(
                f"cannot combine {self.currency} with {other.currency}", field="currency"
            )
        return other

    def __add__(self, other: "Money") -> "Money":
        return Money(self.amount + self._check(other).amount, self.currency)

    def __radd__(self, other: object) -> "Money":
        if isinstance(other, int) and other == 0:
            return self
        raise TypeError(f"cannot add Money to {type(other).__name__}")

    def __sub__(self, other: "Money") -> "Money":
        return Money(self.amount - self._check(other).amount, self.currency)

    def __rsub__(self, other: object) -> "Money":
        if isinstance(other, int) and other == 0:
            return -self
        raise TypeError(f"cannot subtract Money from {type(other).__name__}")

    def __neg__(self) -> "Money":
        return Money(-self.amount, self.currency)

    def __abs__(self) -> "Money":
        return Money(abs(self.amount), self.currency)

    def __mul__(self, factor: int | Decimal) -> "Money":
        if isinstance(factor, bool) or not isinstance(factor, (int, Decimal)):
            raise TypeError("Money can only be scaled by an int or Decimal")
        return Money(self.amount * Decimal(factor), self.currency)

    def __rmul__(self, factor: int | Decimal) -> "Money":
        return self.__mul__(factor)

    def __truediv__(self, divisor: int | Decimal) -> "Money":
        if isinstance(divisor, bool) or not isinstance(divisor, (int, Decimal)):
            raise TypeError("Money can only be divided by an int or Decimal")
        if Decimal(divisor) == 0:
            raise ZeroDivisionError("cannot divide money by zero")
        return Money(self.amount / Decimal(divisor), self.currency)

    def __lt__(self, other: "Money") -> bool:
        return self.amount < self._check(other).amount

    # -------------------------------------------------------------- rendering
    # -------------------------------------------------------------- 渲染输出
    def format(self, *, with_currency: bool = False, thousands: bool = True) -> str:
        text = format_amount(self.amount, exponent=self.exponent, thousands=thousands)
        return f"{text} {self.currency}" if with_currency else text

    def __str__(self) -> str:
        return self.format(with_currency=True)

    def to_dict(self) -> dict[str, str]:
        return {
            "amount": format_amount(self.amount, exponent=self.exponent, thousands=False),
            "currency": self.currency,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "Money":
        return cls.parse(data["amount"], currency=data["currency"])
