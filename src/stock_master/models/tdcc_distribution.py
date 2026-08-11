"""TDCC shareholding-distribution domain model."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TDCCDistribution:
    """One stock's holding distribution for one date and holding level."""

    data_date: str
    stock_code: str
    holding_level: str
    shareholder_count: int
    share_count: int
    holding_ratio: float
