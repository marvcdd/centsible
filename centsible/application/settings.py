"""Ledger-wide settings shared by the application services.

各应用服务共享的账簿级设置。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from centsible.domain.money import (
    DEFAULT_CURRENCY,
    Money,
    coerce_decimal,
    currency_exponent,
    normalize_currency,
    quantize,
)
from centsible.errors import ValidationError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from centsible.config import Config

__all__ = ["LedgerSettings"]


@dataclass(frozen=True, slots=True)
class LedgerSettings:
    """The two facts every service needs to talk about money and periods.

    Keeping this tiny object (instead of passing a whole ``Config`` around)
    makes services trivial to construct in tests.

    每个服务在处理金额与会计期间时都需要的两个事实。

    保留这个小对象（而不是到处传递整个 ``Config``），服务在测试中就极易构造。
    """

    currency: str = DEFAULT_CURRENCY
    fiscal_year_start_month: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", normalize_currency(self.currency))
        month = self.fiscal_year_start_month
        if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
            raise ValidationError(
                "fiscal year start month must be between 1 and 12",
                field="fiscal_year_start_month",
            )

    @classmethod
    def from_config(cls, config: "Config") -> "LedgerSettings":
        return cls(
            currency=config.currency,
            fiscal_year_start_month=config.fiscal_year_start_month,
        )

    @property
    def exponent(self) -> int:
        return currency_exponent(self.currency)

    def quantize(self, value: Decimal) -> Decimal:
        return quantize(value, self.exponent)

    def money(self, value: Decimal | int | str) -> Money:
        return Money(coerce_decimal(value), self.currency)

    def zero(self) -> Money:
        return Money.zero(self.currency)
