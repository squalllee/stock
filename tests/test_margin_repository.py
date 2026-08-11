import sqlite3

import pytest

from stock_master.exceptions import DatabaseError
from stock_master.models import MarginHistory, Stock
from stock_master.repositories.margin_repository import MarginHistoryRepository
from stock_master.repositories.stock_repository import StockRepository


def make_repository(tmp_path):
    db_path = tmp_path / "data" / "stocks.db"
    StockRepository(db_path).upsert_many(
        [Stock("2330", "台積電", "TWSE"), Stock("3105", "穩懋", "TPEX")]
    )
    repository = MarginHistoryRepository(db_path)
    repository.create_tables()
    return repository


def margin(
    trade_date="2026-08-07",
    stock_code="2330",
    *,
    margin_balance=100659,
):
    return MarginHistory(
        trade_date=trade_date,
        stock_code=stock_code,
        market="TWSE" if stock_code == "2330" else "TPEX",
        margin_buy=1234,
        margin_sell=567,
        margin_cash_redemption=8,
        margin_previous_balance=100000,
        margin_balance=margin_balance,
        short_buy=10,
        short_sell=20,
        short_stock_redemption=3,
        short_previous_balance=4000,
        short_balance=3987,
        offsetting_volume=5,
    )


def test_create_margin_table_and_indexes(tmp_path):
    repository = make_repository(tmp_path)

    with sqlite3.connect(repository.db_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(margin_history)")
        }
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(margin_history)")
        }

    assert {
        "trade_date",
        "stock_code",
        "market",
        "margin_buy",
        "margin_balance",
        "short_balance",
        "offsetting_volume",
        "created_at",
        "updated_at",
    } <= columns
    assert {
        "idx_margin_stock_code",
        "idx_margin_trade_date",
        "idx_margin_stock_date",
    } <= indexes


def test_upsert_is_idempotent_and_preserves_different_dates(tmp_path):
    repository = make_repository(tmp_path)

    first = repository.upsert(margin())
    second = repository.upsert(margin(margin_balance=101000))
    third = repository.upsert(margin(trade_date="2026-08-08"))

    assert (first.inserted_count, first.updated_count) == (1, 0)
    assert (second.inserted_count, second.updated_count) == (0, 1)
    assert (third.inserted_count, third.updated_count) == (1, 0)
    assert len(repository.get_by_stock_code("2330")) == 2
    assert repository.get_by_stock_and_date("2330", "2026-08-07").margin_balance == 101000
    assert repository.get_latest_by_stock_code("2330").trade_date == "2026-08-08"
    assert repository.get_latest_trade_date() == "2026-08-08"


def test_get_range_and_stock_date_queries(tmp_path):
    repository = make_repository(tmp_path)
    repository.upsert_many(
        [
            margin("2026-08-07"),
            margin("2026-08-08"),
            margin("2026-08-07", "3105"),
        ]
    )

    assert [item.stock_code for item in repository.get_range("2026-08-07", "2026-08-07")] == [
        "2330",
        "3105",
    ]
    assert [item.trade_date for item in repository.get_range("2026-08-07", "2026-08-08", "2330")] == [
        "2026-08-07",
        "2026-08-08",
    ]


def test_repository_enforces_stock_foreign_key(tmp_path):
    repository = make_repository(tmp_path)

    with pytest.raises(DatabaseError):
        repository.upsert(margin(stock_code="9999"))
