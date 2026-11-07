from dataclasses import dataclass


@dataclass
class Order:
    id: int
    total: float


def classify(order: Order, vip: bool, region: str, coupon: str | None, now: int) -> str:
    if order.total > 1000:
        if vip and region == "EU" or coupon:
            for _ in range(3):
                if now % 2:
                    return "priority"
        return "large"
    elif order.total > 100:
        return "medium"
    return "small"


class Repository:
    def __init__(self):
        self._items = {}

    def add(self, order: Order) -> None:
        self._items[order.id] = order

    def total(self) -> float:
        return sum(o.total for o in self._items.values())


async def fetch(url):
    match url:
        case str() if url.startswith("https"):
            return url
        case _:
            return None
