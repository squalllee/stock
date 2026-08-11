import sqlite3

from stock_master.models import MarginEstimate, Stock
from stock_master.repositories.margin_estimate_repository import MarginEstimateRepository
from stock_master.repositories.stock_repository import StockRepository


def estimate(trade_date="2026-08-07", version="v1", cost=100.0):
    return MarginEstimate(
        trade_date=trade_date,
        stock_code="2330",
        estimated_margin_avg_cost=cost,
        margin_financing_ratio=0.6,
        estimated_financing_per_share=cost * 0.6,
        close_price=90.0,
        estimated_maintenance_ratio=150.0,
        estimated_130_price=78.0,
        model_version=version,
    )


def test_estimate_repository_is_versioned(tmp_path):
    db_path = tmp_path / "data" / "stocks.db"
    StockRepository(db_path).upsert(Stock("2330", "台積電", "TWSE"))
    repository = MarginEstimateRepository(db_path)
    repository.create_tables()

    first = repository.upsert(estimate())
    second = repository.upsert(estimate(cost=101.0))
    third = repository.upsert(estimate(version="v2"))

    assert (first.inserted_count, first.updated_count) == (1, 0)
    assert (second.inserted_count, second.updated_count) == (0, 1)
    assert (third.inserted_count, third.updated_count) == (1, 0)
    assert len(repository.get_range("2026-08-07", "2026-08-07", "2330", "v1")) == 1
    assert repository.get_by_stock_and_date("2330", "2026-08-07", "v1").estimated_margin_avg_cost == 101.0
    with sqlite3.connect(db_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(margin_estimates)")}
    assert {"model_version", "created_at"} <= columns

