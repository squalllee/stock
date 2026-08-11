import sqlite3

import pytest

from stock_master.exceptions import DatabaseError
from stock_master.models import PriceHistory, Stock
from stock_master.repositories.price_repository import PriceHistoryRepository
from stock_master.repositories.stock_repository import StockRepository


def make_repository(tmp_path):
    db_path = tmp_path / "data" / "stocks.db"
    StockRepository(db_path).upsert_many(
        [Stock("2330", "台積電", "TWSE"), Stock("3105", "穩懋", "TPEX")]
    )
    repository = PriceHistoryRepository(db_path)
    repository.create_tables()
    return repository


def price(trade_date="2026-08-07", stock_code="2330", close=2370.0):
    return PriceHistory(
        trade_date=trade_date,
        stock_code=stock_code,
        market="TWSE" if stock_code == "2330" else "TPEX",
        trade_volume=1_000_000,
        trade_value=2_000_000_000,
        open_price=2300.0,
        high_price=2400.0,
        low_price=2280.0,
        close_price=close,
        transaction_count=1000,
    )


def test_price_repository_schema_and_upsert(tmp_path):
    repository = make_repository(tmp_path)
    first = repository.upsert(price())
    second = repository.upsert(price(close=2380.0))
    third = repository.upsert(price("2026-08-08"))

    assert (first.inserted_count, first.updated_count) == (1, 0)
    assert (second.inserted_count, second.updated_count) == (0, 1)
    assert (third.inserted_count, third.updated_count) == (1, 0)
    assert repository.get_by_stock_and_date("2330", "2026-08-07").close_price == 2380.0
    assert repository.get_latest_trade_date() == "2026-08-08"
    with sqlite3.connect(repository.db_path) as connection:
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(price_history)")
        }
    assert "idx_price_stock_date" in indexes


def test_price_repository_range_and_foreign_key(tmp_path):
    repository = make_repository(tmp_path)
    repository.upsert_many([price(), price("2026-08-08"), price(stock_code="3105")])

    assert [item.trade_date for item in repository.get_range("2026-08-07", "2026-08-08", "2330")] == [
        "2026-08-07",
        "2026-08-08",
    ]
    with pytest.raises(DatabaseError):
        repository.upsert(price(stock_code="9999"))
