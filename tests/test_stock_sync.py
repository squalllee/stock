import sqlite3

import pytest

from stock_master.exceptions import StockDataValidationError, StockProviderError
from stock_master.models import Stock
from stock_master.repositories.stock_repository import StockRepository
from stock_master.services.stock_sync_service import StockSyncService


class FakeProvider:
    def __init__(self, stocks=None, error=None):
        self.stocks = stocks or []
        self.error = error

    def fetch(self):
        if self.error:
            raise self.error
        return list(self.stocks)


def make_service(tmp_path, twse, tpex):
    return StockSyncService(
        FakeProvider(twse),
        FakeProvider(tpex),
        StockRepository(tmp_path / "data" / "stocks.db"),
        min_expected_twse=0,
        min_expected_tpex=0,
    )


def test_sync_writes_both_markets_and_deduplicates(tmp_path):
    service = make_service(
        tmp_path,
        [Stock("2330", "台積電", "TWSE"), Stock("2330", "台積電", "TWSE")],
        [Stock("3105", "穩懋", "TPEX")],
    )

    result = service.sync()

    assert result.twse_count == 2
    assert result.tpex_count == 1
    assert result.total_count == 2
    repository = service.repository
    assert repository.get_by_code("2330") == Stock("2330", "台積電", "TWSE")
    assert repository.get_by_code("3105") == Stock("3105", "穩懋", "TPEX")

    with sqlite3.connect(tmp_path / "data" / "stocks.db") as connection:
        duplicate_rows = connection.execute(
            "SELECT stock_code, COUNT(*) FROM stocks "
            "GROUP BY stock_code HAVING COUNT(*) > 1"
        ).fetchall()
        excluded_rows = connection.execute(
            "SELECT COUNT(*) FROM stocks "
            "WHERE stock_code IN ('0050', '0056', '00878', '00919')"
        ).fetchone()[0]
    assert duplicate_rows == []
    assert excluded_rows == 0


def test_second_provider_failure_does_not_change_existing_rows(tmp_path):
    db_path = tmp_path / "data" / "stocks.db"
    repository = StockRepository(db_path)
    repository.upsert(Stock("2330", "台積電", "TWSE"))

    service = StockSyncService(
        FakeProvider([Stock("2317", "鴻海", "TWSE")]),
        FakeProvider(error=StockProviderError("TPEx unavailable")),
        repository,
        min_expected_twse=0,
        min_expected_tpex=0,
    )

    with pytest.raises(StockProviderError):
        service.sync()

    assert repository.get_by_code("2330") == Stock("2330", "台積電", "TWSE")
    assert repository.get_by_code("2317") is None


def test_empty_provider_result_is_rejected(tmp_path):
    service = make_service(tmp_path, [], [Stock("3105", "穩懋", "TPEX")])
    with pytest.raises(StockDataValidationError):
        service.sync()

