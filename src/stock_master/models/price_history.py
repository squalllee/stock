"""Daily official trading facts and the exact market-average calculation."""

from dataclasses import dataclass


def calculate_market_average_price(
    trade_value: int,
    trade_volume: int,
) -> float | None:
    """Return ``trade_value / trade_volume`` or None when volume is zero."""

    if trade_volume <= 0:
        return None
    return trade_value / trade_volume


@dataclass(frozen=True, slots=True)
class PriceHistory:
    """One stock's official daily trading facts.

    ``trade_volume`` is shares and ``trade_value`` is TWD.  Providers are
    responsible for converting source-specific lots/thousand-TWD units before
    constructing this model.
    """

    trade_date: str
    stock_code: str
    market: str

    trade_volume: int
    trade_value: int

    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    close_price: float | None = None

    transaction_count: int | None = None

    @property
    def market_average_price(self) -> float | None:
        """Exact all-market average price derived from official raw values."""

        return calculate_market_average_price(self.trade_value, self.trade_volume)
