"""Order lifecycle: place, cancel lines, refund."""

from stock import Stock


class Order:
    def __init__(self, order_id: str, lines: dict[str, int]) -> None:
        self.order_id = order_id
        self.lines = dict(lines)
        self.state = "open"


def place(order: Order, stock: Stock) -> None:
    for sku, qty in order.lines.items():
        stock.reserve(sku, qty)
    order.state = "placed"


def cancel_line(order: Order, stock: Stock, sku: str, qty: int) -> None:
    if order.state != "placed":
        raise RuntimeError("only placed orders can change")
    current = order.lines.get(sku, 0)
    if qty > current:
        raise ValueError("cannot cancel more than ordered")
    order.lines[sku] = current - qty
    if order.lines[sku] == 0:
        del order.lines[sku]
        stock.release(sku, current)


def cancel(order: Order, stock: Stock) -> None:
    for sku, qty in list(order.lines.items()):
        stock.release(sku, qty)
    order.lines.clear()
    order.state = "cancelled"
