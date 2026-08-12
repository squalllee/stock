"""Pydantic boundary schemas for the Web platform.

The query service deliberately returns plain dictionaries so it can also be
used by HTML pages.  These schemas document the public JSON vocabulary and
are available to callers that want to validate or extend the API contract.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class StockSchema(BaseModel):
    stock_code: str
    stock_name: str
    market: str


class StockListSchema(BaseModel):
    items: list[StockSchema]
    limit: int
    offset: int
    has_more: bool


class PriceSchema(BaseModel):
    trade_date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    trade_volume: int
    trade_value: int
    transaction_count: int | None = None
    market_average_price: float | None = None


class MarginSchema(BaseModel):
    trade_date: str
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


class MarginEstimateSchema(BaseModel):
    trade_date: str
    estimated_margin_avg_cost: float
    margin_financing_ratio: float
    estimated_financing_per_share: float
    close_price: float
    estimated_maintenance_ratio: float
    estimated_130_price: float
    model_version: str
    estimated: bool = True


class TDCCSchema(BaseModel):
    data_date: str
    holding_level: str
    shareholder_count: int
    share_count: int
    holding_ratio: float


class HistoryPageSchema(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    stock_code: str
    from_date: str = Field(alias="from")
    to_date: str = Field(alias="to")
    items: list[Any]
    limit: int
    offset: int
    has_more: bool
