"""Stock domain model."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Stock:
    """A listed or OTC common stock in the master table."""

    stock_code: str
    stock_name: str
    market: str

