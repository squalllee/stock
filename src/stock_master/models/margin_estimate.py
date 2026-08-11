"""Estimated margin-cost and maintenance model outputs."""

from dataclasses import dataclass

DEFAULT_MARGIN_MODEL_VERSION = "margin-cost-v1-wma-daily-market-average"


@dataclass(frozen=True, slots=True)
class MarginCostEstimate:
    """The cost-only output kept separate from maintenance calculations."""

    trade_date: str
    stock_code: str
    estimated_margin_avg_cost: float
    model_version: str


@dataclass(frozen=True, slots=True)
class MarginEstimate:
    """Combined persisted output produced by cost + maintenance estimators."""

    trade_date: str
    stock_code: str

    estimated_margin_avg_cost: float
    margin_financing_ratio: float
    estimated_financing_per_share: float
    close_price: float
    estimated_maintenance_ratio: float
    estimated_130_price: float
    model_version: str
