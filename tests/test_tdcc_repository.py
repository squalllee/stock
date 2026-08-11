import sqlite3

import pytest

from stock_master.exceptions import DatabaseError
from stock_master.models import Stock, TDCCDistribution
from stock_master.repositories.stock_repository import StockRepository
from stock_master.repositories.tdcc_repository import TDCCDistributionRepository


def make_repository(tmp_path):
    db_path = tmp_path / "data" / "stocks.db"
    StockRepository(db_path).upsert_many(
        [Stock("2330", "台積電", "TWSE"), Stock("3105", "穩懋", "TPEX")]
    )
    repository = TDCCDistributionRepository(db_path)
    repository.create_tables()
    return repository


def distribution(data_date="2026-08-07", level="15", ratio=12.34):
    return TDCCDistribution(
        data_date=data_date,
        stock_code="2330",
        holding_level=level,
        shareholder_count=123,
        share_count=456789,
        holding_ratio=ratio,
    )


def test_create_tdcc_table_and_indexes(tmp_path):
    repository = make_repository(tmp_path)

    with sqlite3.connect(repository.db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(tdcc_distributions)")
        }
        indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(tdcc_distributions)")
        }

    assert {
        "data_date",
        "stock_code",
        "holding_level",
        "shareholder_count",
        "share_count",
        "holding_ratio",
        "created_at",
        "updated_at",
    } <= columns
    assert {
        "idx_tdcc_stock_code",
        "idx_tdcc_data_date",
        "idx_tdcc_stock_date",
    } <= indexes


def test_get_data_dates_returns_distinct_sorted_dates(tmp_path):
    repository = make_repository(tmp_path)
    repository.upsert_many(
        [
            distribution(data_date="2026-08-07", level="1"),
            distribution(data_date="2026-07-31", level="1"),
            distribution(data_date="2026-08-07", level="2"),
        ]
    )

    assert repository.get_data_dates() == ["2026-07-31", "2026-08-07"]


def test_upsert_is_idempotent_and_preserves_different_dates(tmp_path):
    repository = make_repository(tmp_path)

    first = repository.upsert(distribution())
    second = repository.upsert(distribution(ratio=13.37))
    third = repository.upsert(distribution(data_date="2026-08-14"))

    assert (first.inserted_count, first.updated_count) == (1, 0)
    assert (second.inserted_count, second.updated_count) == (0, 1)
    assert (third.inserted_count, third.updated_count) == (1, 0)
    assert len(repository.get_by_stock_code("2330")) == 2
    assert (
        repository.get_by_stock_and_date("2330", "2026-08-07")[0].holding_ratio
        == 13.37
    )
    assert len(repository.get_latest_by_stock_code("2330")) == 1


def test_repository_enforces_stock_foreign_key(tmp_path):
    repository = make_repository(tmp_path)
    invalid = TDCCDistribution(
        data_date="2026-08-07",
        stock_code="9999",
        holding_level="15",
        shareholder_count=1,
        share_count=1,
        holding_ratio=1.0,
    )

    with pytest.raises(DatabaseError):
        repository.upsert(invalid)
