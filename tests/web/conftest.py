"""SQLite fixtures for Web/API tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from stock_master.models import (
    DEFAULT_MARGIN_MODEL_VERSION,
    MarginEstimate,
    MarginHistory,
    PriceHistory,
    Stock,
    TDCCDistribution,
)
from stock_master.repositories import (
    MarginEstimateRepository,
    MarginHistoryRepository,
    PriceHistoryRepository,
    StockRepository,
    TDCCDistributionRepository,
)
from stock_master.web import create_app


@pytest.fixture()
def web_db(tmp_path: Path) -> Path:
    database = tmp_path / "stocks.db"
    StockRepository(database).upsert_many(
        [
            Stock("2330", "台積電", "TWSE"),
            Stock("1101", "台泥", "TWSE"),
            Stock("3105", "穩懋", "TPEX"),
        ]
    )
    dates = ["2026-08-07", "2026-08-10", "2026-08-11"]
    PriceHistoryRepository(database).upsert_many(
        [
            PriceHistory(
                trade_date=value,
                stock_code="2330",
                market="TWSE",
                trade_volume=1_000 + index * 100,
                trade_value=(650 + index * 5) * (1_000 + index * 100),
                open_price=648 + index * 5,
                high_price=655 + index * 5,
                low_price=645 + index * 5,
                close_price=650 + index * 5,
                transaction_count=100 + index,
            )
            for index, value in enumerate(dates)
        ]
    )
    MarginHistoryRepository(database).upsert_many(
        [
            MarginHistory(
                trade_date=value,
                stock_code="2330",
                market="TWSE",
                margin_buy=10 + index,
                margin_sell=2,
                margin_cash_redemption=0,
                margin_previous_balance=100 + index,
                margin_balance=108 + index,
                short_buy=1,
                short_sell=3,
                short_stock_redemption=0,
                short_previous_balance=20,
                short_balance=18 - index,
                offsetting_volume=4,
            )
            for index, value in enumerate(dates)
        ]
    )
    MarginEstimateRepository(database).upsert_many(
        [
            MarginEstimate(
                trade_date=value,
                stock_code="2330",
                estimated_margin_avg_cost=600 + index * 2,
                margin_financing_ratio=0.6,
                estimated_financing_per_share=(600 + index * 2) * 0.6,
                close_price=650 + index * 5,
                estimated_maintenance_ratio=180 + index,
                estimated_130_price=(600 + index * 2) * 0.6 * 1.3,
                model_version=DEFAULT_MARGIN_MODEL_VERSION,
            )
            for index, value in enumerate(dates)
        ]
    )
    TDCCDistributionRepository(database).upsert_many(
        [
            TDCCDistribution(value, "2330", level, count, shares, ratio)
            for value, count, shares, ratio in (
                ("2026-08-01", 100, 100_000, 10.0),
                ("2026-08-08", 105, 110_000, 11.0),
            )
            for level in ("01", "02")
        ]
    )
    return database


@pytest.fixture()
def web_client(web_db: Path):
    with TestClient(create_app(web_db)) as client:
        yield client

