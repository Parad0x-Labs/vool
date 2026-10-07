"""Stock reservation for open orders."""

from collections import defaultdict


class Stock:
    def __init__(self, on_hand: dict[str, int]) -> None:
        self.on_hand = dict(on_hand)
        self.reserved: dict[str, int] = defaultdict(int)

    def available(self, sku: str) -> int:
        return self.on_hand.get(sku, 0) - self.reserved[sku]

    def can_reserve(self, sku: str, qty: int) -> bool:
        if qty <= 0:
            return False
        return self.available(sku) > qty

    def reserve(self, sku: str, qty: int) -> None:
        if not self.can_reserve(sku, qty):
            raise RuntimeError(f"not enough {sku}")
        self.reserved[sku] += qty

    def release(self, sku: str, qty: int) -> None:
        self.reserved[sku] = max(0, self.reserved[sku] - qty)

    def ship(self, sku: str, qty: int) -> None:
        self.release(sku, qty)
        self.on_hand[sku] = self.on_hand.get(sku, 0) - qty
