from stock_master.models import Stock
from stock_master.repositories.stock_repository import StockRepository


def test_insert_update_get_and_duplicate_primary_key(tmp_path):
    repository = StockRepository(tmp_path / "stocks.db")
    repository.create_tables()

    first = repository.upsert(Stock("2330", "台積電", "TWSE"))
    assert first.inserted_count == 1
    assert first.updated_count == 0
    assert repository.get_by_code("2330") == Stock("2330", "台積電", "TWSE")

    second = repository.upsert(Stock("2330", "台積電（更新）", "TWSE"))
    assert second.inserted_count == 0
    assert second.updated_count == 1
    assert repository.get_by_code("2330").stock_name == "台積電（更新）"

    all_stocks = repository.get_all()
    assert len(all_stocks) == 1


def test_schema_and_market_index_exist(tmp_path):
    repository = StockRepository(tmp_path / "stocks.db")
    repository.create_tables()

    import sqlite3

    with sqlite3.connect(tmp_path / "stocks.db") as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(stocks)")
        }
        indexes = {
            row[1] for row in connection.execute("PRAGMA index_list(stocks)")
        }
    assert {"stock_code", "stock_name", "market", "created_at", "updated_at"} <= columns
    assert "idx_stocks_market" in indexes

