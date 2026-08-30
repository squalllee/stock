"""Daily margin-trading history domain model."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarginHistory:
    """One stock's official margin-trading facts for one trading date.

    All quantity fields use trading units (張), matching the official TWSE and
    TPEx margin reports. Derived values such as estimated financing cost do not
    belong in this raw-data model.
    """

    trade_date: str
    stock_code: str
    market: str

    margin_buy: int
    margin_sell: int
    margin_cash_redemption: int
    margin_previous_balance: int
    margin_balance: int

    short_buy: int
    short_sell: int
    short_stock_redemption: int
    short_previous_balance: int
    short_balance: int

    offsetting_volume: int | None = None
    # Official reports expose the next-day/approved financing limit and, for
    # TPEx, the resulting utilization percentage.  TWSE utilization is
    # derived from today's balance divided by that limit.
    margin_limit: int | None = None
    margin_utilization: float | None = None
