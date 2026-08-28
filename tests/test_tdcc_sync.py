import sqlite3

import pytest

from stock_master.exceptions import StockDataValidationError, StockProviderError
from stock_master.models import Stock, TDCCDistribution
from stock_master.repositories.stock_repository import StockRepository
from stock_master.repositories.tdcc_repository import TDCCDistributionRepository
from stock_master.services.tdcc_sync_service import TDCCSyncService


class FakeProvider:
    def __init__(self, records=None, error=None, skipped_total_count=0):
        self.records = list(records or [])
        self.error = error
        self.received_stock_codes = None
        self.last_skipped_total_count = skipped_total_count

    def fetch(self, stock_codes):
        self.received_stock_codes = stock_codes
        if self.error:
            raise self.error
        return list(self.records)


def make_service(tmp_path, records, *, stocks=None, provider=None):
    db_path = tmp_path / "data" / "stocks.db"
    stock_repository = StockRepository(db_path)
    stock_repository.upsert_many(stocks or [Stock("2330", "台積電", "TWSE")])
    tdcc_repository = TDCCDistributionRepository(db_path)
    return (
        TDCCSyncService(
            provider or FakeProvider(records),
            stock_repository,
            tdcc_repository,
        ),
        stock_repository,
        tdcc_repository,
    )


def record(level="15", date="2026-08-07", code="2330", ratio=12.34):
    return TDCCDistribution(date, code, level, 100, 1000, ratio)


def test_tdcc_sync_uses_stock_master_and_filters_non_master(tmp_path):
    provider = FakeProvider([record(), record(code="3105")])
    service, _, repository = make_service(tmp_path, [], provider=provider)

    result = service.sync()

    assert provider.received_stock_codes == {"2330"}
    assert result.tdcc_count == 1
    assert repository.get_by_stock_code("2330") == [record()]
    assert repository.get_by_stock_code("3105") == []


def test_tdcc_sync_deduplicates_identical_records(tmp_path):
    service, _, repository = make_service(tmp_path, [record(), record()])

    result = service.sync()

    assert result.tdcc_count == 1
    assert result.inserted_count == 1
    assert len(repository.get_by_stock_code("2330")) == 1


def test_tdcc_sync_conflicting_duplicate_fails_without_writes(tmp_path):
    service, _, repository = make_service(
        tmp_path, [record(ratio=12.34), record(ratio=13.34)]
    )

    with pytest.raises(StockDataValidationError, match="Conflicting"):
        service.sync()

    assert repository.get_by_stock_code("2330") == []


def test_tdcc_sync_empty_stock_master_aborts_before_provider(tmp_path):
    db_path = tmp_path / "data" / "stocks.db"
    stock_repository = StockRepository(db_path)
    stock_repository.create_tables()
    tdcc_repository = TDCCDistributionRepository(db_path)
    provider = FakeProvider([record()])
    service = TDCCSyncService(provider, stock_repository, tdcc_repository)

    with pytest.raises(StockDataValidationError, match="Run stock-master sync first"):
        service.sync()

    assert provider.received_stock_codes is None


def test_tdcc_sync_provider_failure_does_not_delete_existing_data(tmp_path):
    service, stock_repository, tdcc_repository = make_service(tmp_path, [record()])
    service.sync()
    provider = FakeProvider(error=StockProviderError("TDCC unavailable"))
    failed_service = TDCCSyncService(provider, stock_repository, tdcc_repository)

    with pytest.raises(StockProviderError):
        failed_service.sync()

    assert tdcc_repository.get_by_stock_code("2330") == [record()]
    with sqlite3.connect(tdcc_repository.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM tdcc_distributions"
        ).fetchone()[0] == 1


def test_tdcc_sync_empty_provider_result_does_not_delete_existing_data(tmp_path):
    service, stock_repository, tdcc_repository = make_service(tmp_path, [record()])
    service.sync()
    failed_service = TDCCSyncService(
        FakeProvider([]), stock_repository, tdcc_repository
    )

    with pytest.raises(StockDataValidationError, match="no distribution records"):
        failed_service.sync()

    assert tdcc_repository.get_by_stock_code("2330") == [record()]


def test_tdcc_sync_skips_total_record_from_fake_provider(tmp_path):
    service, _, repository = make_service(
        tmp_path,
        [record(), record(level="合計")],
    )

    result = service.sync()

    assert result.skipped_total_count == 1
    assert len(repository.get_by_stock_code("2330")) == 1


def test_tdcc_sync_persists_only_holding_levels_one_to_fifteen(tmp_path):
    service, _, repository = make_service(
        tmp_path,
        [
            record(level="14"),
            record(level="15"),
            record(level="16"),
            record(level="17"),
        ],
    )

    result = service.sync()

    assert result.tdcc_count == 2
    assert [
        item.holding_level for item in repository.get_by_stock_code("2330")
    ] == ["14", "15"]
