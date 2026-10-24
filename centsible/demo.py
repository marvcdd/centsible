"""Deterministic demo data.

``centsible demo`` turns an empty database into a small but realistic business
ledger: six months of trading for a design studio, with revenue, cost of sales,
rent, payroll, a laptop purchase, budgets and two standing entries.  It is
generated through the normal services, so it also proves that every rule holds.

The random variation uses a fixed seed: running the demo twice on two empty
databases produces identical figures, which makes screenshots and tests stable.

确定性的演示数据。

``centsible demo`` 把一个空数据库变成一份小而真实的经营账簿：一家设计工作室
六个月的经营数据，含收入、销货成本、房租、工资、一台笔记本电脑的购置、预算以及
两笔固定分录。这些数据全部经由常规服务生成，因此也顺带证明了各项规则都成立。

随机波动使用固定种子：在两个空数据库上各跑一次演示，得到的数字完全一致，
所以截图和测试都能保持稳定。
"""

from __future__ import annotations

import random
from datetime import date
from decimal import Decimal

from centsible.bootstrap import AppServices
from centsible.domain.money import quantize
from centsible.domain.periods import shift_months
from centsible.domain.transactions import Posting
from centsible.errors import ConflictError, ValidationError

__all__ = ["MAX_DEMO_MONTHS", "seed_demo"]

MAX_DEMO_MONTHS = 36
_SEED = 20240501


def seed_demo(services: AppServices, *, months: int = 6) -> dict[str, int]:
    """Populate an empty ledger with demo data and return what was created.

    用演示数据填充一份空账簿，并返回创建了哪些内容。"""

    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= MAX_DEMO_MONTHS:
        raise ValidationError(
            f"months must be an integer between 1 and {MAX_DEMO_MONTHS}", field="months"
        )

    ledger = services.ledger
    if ledger.search(limit=1):
        raise ConflictError(
            "this ledger already contains transactions; start from a fresh database "
            "with 'centsible demo --fresh'"
        )

    created_accounts = services.accounts.ensure_chart("default")
    services.ledger.record_ledger_currency(services.settings.currency)
    clock = services.clock
    today = clock.today()
    exponent = services.settings.exponent
    rng = random.Random(_SEED)

    counter = {"transactions": 0}

    def money(value: Decimal) -> Decimal:
        return quantize(value, exponent)

    def record(day: date, description: str, postings: list[tuple[str, Decimal]], *, payee: str = "", tags: tuple[str, ...] = ()) -> None:
        if day > today:
            return
        built = tuple(Posting(code, amount) for code, amount in postings)
        ledger.record(
            date=day,
            description=description,
            postings=built,
            payee=payee,
            tags=tags,
        )
        counter["transactions"] += 1

    first_month = date(today.year, today.month, 1)
    start = shift_months(first_month, -(months - 1))

    # ---------------------------------------------------------- opening capital
    # ---------------------------------------------------------- 期初资本
    record(
        start,
        "Opening capital",
        [("1010", money(Decimal("20000"))), ("3000", money(Decimal("-20000")))],
        payee="Owner",
        tags=("opening",),
    )

    for index in range(months):
        month_start = shift_months(start, index)

        def day(number: int) -> date:
            return date(month_start.year, month_start.month, number)

        sales = money(Decimal("9800") + Decimal(rng.randrange(0, 9)) * Decimal("137.5"))
        base = money(sales / Decimal("1.13"))
        tax = sales - base
        utilities = money(Decimal("180") + Decimal(rng.randrange(0, 6)) * Decimal("9.5"))

        record(day(2), "Office rent", [("1010", -money(Decimal("1200"))), ("5100", money(Decimal("1200")))], payee="Landlord Ltd", tags=("fixed",))
        record(day(4), "Software subscriptions", [("1010", -money(Decimal("99"))), ("5900", money(Decimal("99")))], payee="SaaS Provider", tags=("fixed",))
        record(
            day(8),
            "POS sales",
            [
                ("1010", sales),
                ("4000", -base),
                ("2200", -tax),
            ],
            payee="Card terminal",
            tags=("sales",),
        )
        record(
            day(10),
            "Cash sale",
            [("1000", money(Decimal("540"))), ("4000", money(Decimal("-540")))],
            tags=("sales",),
        )
        record(
            day(12),
            "Inventory purchase",
            [("1010", -money(Decimal("900"))), ("1200", money(Decimal("900")))],
            payee="Wholesale Co",
        )
        record(
            day(14),
            "Cost of goods sold",
            [("1200", money(Decimal("-560"))), ("5000", money(Decimal("560")))],
        )
        record(day(15), "Payroll", [("1010", -money(Decimal("3500"))), ("5200", money(Decimal("3500")))], payee="Payroll bureau", tags=("fixed",))
        record(
            day(18),
            "Client lunch",
            [("1010", -money(Decimal("85"))), ("5800", money(Decimal("85")))],
            payee="Bistro",
        )
        record(
            day(22),
            "Utilities",
            [("1010", -utilities), ("5300", utilities)],
            payee="City Utilities",
        )
        record(day(25), "Marketing campaign", [("1010", -money(Decimal("250"))), ("5400", money(Decimal("250")))], tags=("growth",))
        record(day(28), "Bank fees", [("1010", -money(Decimal("12"))), ("5600", money(Decimal("12")))])

        if index == 2:
            record(
                day(20),
                "Laptop purchase",
                [("1010", -money(Decimal("2400"))), ("1500", money(Decimal("2400")))],
                payee="Electronics store",
                tags=("capital",),
            )

    # ------------------------------------------------------------------ budgets
    # ------------------------------------------------------------------ 预算
    current_month = f"{today.year:04d}-{today.month:02d}"
    planned = {
        "5100": Decimal("1200"),
        "5200": Decimal("3500"),
        "5300": Decimal("250"),
        "5400": Decimal("300"),
        "5900": Decimal("100"),
    }
    for code, amount in planned.items():
        services.budgets.set(account_code=code, month=current_month, amount=money(amount))

    # ------------------------------------------------------- recurring entries
    # ------------------------------------------------------- 周期分录
    next_month = shift_months(first_month, 1)
    ledger_next = [("1010", -money(Decimal("1200"))), ("5100", money(Decimal("1200")))]
    services.recurring.create(
        name="Monthly rent",
        description="Office rent",
        frequency="monthly",
        postings=tuple(Posting(code, amount) for code, amount in ledger_next),
        start_date=date(next_month.year, next_month.month, 1),
        payee="Landlord Ltd",
        tags=("fixed",),
    )
    services.recurring.create(
        name="Software subscriptions",
        description="Software subscriptions",
        frequency="monthly",
        postings=(
            Posting("1010", -money(Decimal("99"))),
            Posting("5900", money(Decimal("99"))),
        ),
        start_date=date(next_month.year, next_month.month, 1),
        payee="SaaS Provider",
        tags=("fixed",),
    )

    return {
        "accounts": len(created_accounts),
        "transactions": counter["transactions"],
        "budgets": len(planned),
        "recurring_rules": 2,
    }
