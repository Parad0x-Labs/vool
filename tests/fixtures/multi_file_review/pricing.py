"""Price calculation for checkout."""

from dataclasses import dataclass

MEMBER_RATE = 0.10
TAX_RATE = 0.21


@dataclass
class Line:
    sku: str
    unit_price: float
    qty: int


def subtotal(lines: list[Line]) -> float:
    return round(sum(line.unit_price * line.qty for line in lines), 2)


def apply_coupon(amount: float, coupon_pct: float) -> float:
    if not 0 <= coupon_pct <= 0.5:
        raise ValueError("coupon out of range")
    return amount * (1 - coupon_pct)


def member_price(amount: float, is_member: bool) -> float:
    return amount * (1 - MEMBER_RATE) if is_member else amount


def total(lines: list[Line], *, coupon_pct: float = 0.0, is_member: bool = False) -> float:
    base = subtotal(lines)
    discounted = apply_coupon(base, coupon_pct)
    if is_member:
        discounted = member_price(discounted, is_member)
        discounted = apply_coupon(discounted, coupon_pct)
    with_tax = discounted * (1 + TAX_RATE)
    return round(with_tax, 2)
