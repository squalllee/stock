import logging

import pytest

from stock_master.models import MarginHistory, PriceHistory, Stock
from stock_master.repositories.margin_repository import MarginHistoryRepository
from stock_master.repositories.price_repository import PriceHistoryRepository
from stock_master.repositories.stock_repository import StockRepository
from stock_master.services.margin_cost_estimator import MarginCostEstimator
from stock_master.services.margin_maintenance_estimator import MarginMaintenanceEstimator


def make_repositories(tmp_path):
    db_path = tmp_path / "data" / "stocks.db"
    StockRepository(db_path).upsert(Stock("2330", "台積電", "TWSE"))
    return (
        MarginHistoryRepository(db_path),
        PriceHistoryRepository(db_path),
    )


def margin(
    trade_date,
    *,
    previous=0,
    balance=10,
    buy=10,
    sell=0,
    redemption=0,
):
    return MarginHistory(
        trade_date=trade_date,
        stock_code="2330",
        market="TWSE",
        margin_buy=buy,
        margin_sell=sell,
        margin_cash_redemption=redemption,
        margin_previous_balance=previous,
        margin_balance=balance,
        short_buy=0,
        short_sell=0,
        short_stock_redemption=0,
        short_previous_balance=0,
        short_balance=0,
        offsetting_volume=0,
    )


def price(trade_date, average, close):
    return PriceHistory(
        trade_date=trade_date,
        stock_code="2330",
        market="TWSE",
        trade_volume=100,
        trade_value=int(average * 100),
        open_price=close,
        high_price=close,
        low_price=close,
        close_price=close,
        transaction_count=1,
    )


def test_maintenance_model_uses_close_price_and_ratio():
    result = MarginMaintenanceEstimator(0.60).estimate(
        "2026-08-07", "2330", 100.0, 90.0
    )

    assert result.estimated_financing_per_share == pytest.approx(60.0)
    assert result.estimated_maintenance_ratio == pytest.approx(150.0)
    assert result.estimated_130_price == pytest.approx(78.0)


def test_margin_cost_wma_sell_redemption_retain_cost_and_persist(tmp_path):
    margins, prices = make_repositories(tmp_path)
    margins.upsert_many(
        [
            margin("2026-08-07", previous=0, balance=10, buy=10),
            margin("2026-08-08", previous=10, balance=15, buy=10),
            margin("2026-08-09", previous=15, balance=10, buy=0, sell=5),
        ]
    )
    prices.upsert_many(
        [
            price("2026-08-07", 100.0, 90.0),
            price("2026-08-08", 120.0, 110.0),
            price("2026-08-09", 80.0, 100.0),
        ]
    )

    estimator = MarginCostEstimator(margins, prices)
    results = estimator.estimate_range("2330", "2026-08-07", "2026-08-09")

    assert [item.estimated_margin_avg_cost for item in results] == pytest.approx(
        [100.0, 110.0, 110.0]
    )
    assert results[0].estimated_maintenance_ratio == pytest.approx(150.0)
    assert len(estimator.estimate_repository.get_by_stock_code("2330")) == 3


def test_margin_cost_zero_balance_and_reconciliation_warning(tmp_path, caplog):
    margins, prices = make_repositories(tmp_path)
    margins.upsert_many(
        [
            margin("2026-08-07", previous=0, balance=10, buy=10),
            margin("2026-08-08", previous=9, balance=0, buy=0, sell=8),
        ]
    )
    prices.upsert_many([price("2026-08-07", 100.0, 100.0), price("2026-08-08", 90.0, 90.0)])
    estimator = MarginCostEstimator(margins, prices)

    with caplog.at_level(logging.WARNING):
        results = estimator.estimate_range("2330", "2026-08-07", "2026-08-08")

    assert results[1].estimated_margin_avg_cost == 0.0
    assert "continuity discrepancy" in caplog.text
    assert "official balance remains source of truth" in caplog.text


def test_missing_close_skips_only_that_estimate_row(tmp_path, caplog):
    margins, prices = make_repositories(tmp_path)
    margins.upsert_many(
        [
            margin("2026-08-07", previous=0, balance=10, buy=10),
            margin("2026-08-08", previous=10, balance=10, buy=0),
            margin("2026-08-09", previous=10, balance=15, buy=5),
        ]
    )
    prices.upsert_many(
        [
            price("2026-08-07", 100.0, 100.0),
            price("2026-08-08", 100.0, None),
            price("2026-08-09", 120.0, 120.0),
        ]
    )
    estimator = MarginCostEstimator(margins, prices)

    with caplog.at_level(logging.WARNING):
        results = estimator.estimate_range("2330", "2026-08-07", "2026-08-09")

    assert [item.trade_date for item in results] == ["2026-08-07", "2026-08-09"]
    assert results[-1].estimated_margin_avg_cost == pytest.approx(106.6666667)
    assert estimator.last_skipped_close_records == [("2330", "2026-08-08")]
    assert "Skipping this estimate row" in caplog.text


def test_missing_price_skips_unchanged_position_row(tmp_path, caplog):
    margins, prices = make_repositories(tmp_path)
    margins.upsert_many(
        [
            margin("2026-08-07", previous=0, balance=10, buy=10),
            margin("2026-08-08", previous=10, balance=10, buy=0),
            margin("2026-08-09", previous=10, balance=15, buy=5),
        ]
    )
    prices.upsert_many(
        [
            price("2026-08-07", 100.0, 100.0),
            price("2026-08-09", 120.0, 120.0),
        ]
    )
    estimator = MarginCostEstimator(margins, prices)

    with caplog.at_level(logging.WARNING):
        results = estimator.estimate_range("2330", "2026-08-07", "2026-08-09")

    assert [item.trade_date for item in results] == ["2026-08-07", "2026-08-09"]
    assert results[-1].estimated_margin_avg_cost == pytest.approx(106.6666667)
    assert estimator.last_skipped_price_records == [("2330", "2026-08-08")]
    assert "Missing price_history" in caplog.text


def test_first_active_snapshot_without_volume_uses_close_for_bootstrap(tmp_path, caplog):
    margins, prices = make_repositories(tmp_path)
    margins.upsert(
        margin("2026-08-10", previous=42, balance=42, buy=0)
    )
    prices.upsert(
        PriceHistory(
            trade_date="2026-08-10",
            stock_code="2330",
            market="TWSE",
            trade_volume=0,
            trade_value=0,
            close_price=100.0,
        )
    )
    estimator = MarginCostEstimator(margins, prices)

    with caplog.at_level(logging.WARNING):
        results = estimator.estimate_range("2330", "2026-08-10", "2026-08-10")

    assert results[0].estimated_margin_avg_cost == pytest.approx(100.0)
    assert "bootstrap fallback" in caplog.text


def test_first_active_snapshot_without_price_is_skipped(tmp_path, caplog):
    margins, prices = make_repositories(tmp_path)
    margins.upsert(
        margin("2026-08-10", previous=42, balance=42, buy=0)
    )
    prices.create_tables()
    estimator = MarginCostEstimator(margins, prices)

    with caplog.at_level(logging.WARNING):
        results = estimator.estimate_range("2330", "2026-08-10", "2026-08-10")

    assert results == []
    assert estimator.last_skipped_price_records == [("2330", "2026-08-10")]
    assert "Missing price_history" in caplog.text
