"""Module docstring: not code."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:

    def typing_only(x: int) -> int:
        return x


def outer(a, b=1, *args, c: int = 2, **kwargs):
    """Docstring lines

    do not count.
    """

    # comment lines do not count
    def inner(y: str):
        return y

    return inner(str(a))


class Service:
    """Class docstring."""

    @staticmethod
    def build(name: str, *parts: str) -> "Service":
        return Service()

    @property
    def ready(self) -> bool:
        return True

    class Config:
        def load(cls_or_self, path):
            return path

    async def run(self, /, retries: int, *, timeout: float = 1.0) -> None:
        await self._go()
