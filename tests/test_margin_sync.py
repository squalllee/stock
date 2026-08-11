from datetime import date

import pytest

from stock_master.exceptions import StockDataValidationError, StockProviderError
from stock_master.models import MarginHistory, Stock
from stock_master.repositories.margin_repository import MarginHistoryRepository
from stock_master.repositories.stock_repository import StockRepository
from stock_master.services.margin_sync_service import MarginSyncService


class FakeMarginProvider:
    def __init__(
        self,
        market,
        records=None,
        *,
        no_data=False,
        error=None,
        skipped_total_count=0,
    ):
        self.market = market
        self.records = list(records or [])
        self.no_data = no_data
        self.error = error
        self.calls = []
        self.last_trade_date = None
        self.last_no_data = False
        self.last_skipped_total_count = skipped_total_count

    def fetch(self, trade_date=None):
        self.calls.append(trade_date)
        self.last_no_data = self.no_data
        if self.error:
            raise self.error
        if self.no_data:
            self.last_trade_date = None
            return []
        self.last_trade_date = self.records[0].trade_date if self.records else None
        return list(self.records)


def margin_record(
    stock_code, market, trade_date="2026-08-07", *, margin_balance=100
):
    return MarginHistory(
        trade_date=trade_date,
        stock_code=stock_code,
        market=market,
        margin_buy=1,
        margin_sell=2,
        margin_cash_redemption=3,
        margin_previous_balance=4,
        margin_balance=margin_balance,
        short_buy=5,
        short_sell=6,
        short_stock_redemption=7,
        short_previous_balance=8,
        short_balance=9,
        offsetting_volume=10,
    )


def make_service(tmp_path, twse_records, tpex_records, *, stocks=None):
    db_path = tmp_path / "data" / "stocks.db"
    stock_repository = StockRepository(db_path)
    stock_repository.upsert_many(
        stocks
        or [Stock("2330", "台積電", "TWSE"), Stock("3105", "穩懋", "TPEX")]
    )
    twse_provider = FakeMarginProvider("TWSE", twse_records)
    tpex_provider = FakeMarginProvider("TPEX", tpex_records)
    margin_repository = MarginHistoryRepository(db_path)
    service = MarginSyncService(
        twse_provider,
        tpex_provider,
        stock_repository,
        margin_repository,
    )
    return service, twse_provider, tpex_provider, margin_repository


def test_margin_sync_success_uses_stock_master_and_filters_etf(tmp_path):
    service, twse, tpex, repository = make_service(
        tmp_path,
        [
            margin_record("2330", "TWSE"),
            margin_record("0050", "TWSE"),
        ],
        [
            margin_record("3105", "TPEX"),
            margin_record("00878", "TPEX"),
            margin_record("00679B", "TPEX"),
        ],
    )

    result = service.sync(date(2026, 8, 7))

    assert twse.calls == [date(2026, 8, 7)]
    assert tpex.calls == [date(2026, 8, 7)]
    assert result.trade_date == "2026-08-07"
    assert result.twse_count == 1
    assert result.tpex_count == 1
    assert result.margin_count == 2
    assert result.skipped_non_master_count == 3
    assert [item.stock_code for item in repository.get_range("2026-08-07", "2026-08-07")] == [
        "2330",
        "3105",
    ]


def test_margin_sync_skips_market_without_master_codes(tmp_path):
    service, twse, tpex, repository = make_service(
        tmp_path,
        [margin_record("2330", "TWSE")],
        [margin_record("3105", "TPEX")],
        stocks=[Stock("2330", "台積電", "TWSE")],
    )

    result = service.sync(date(2026, 8, 7))

    assert result.margin_count == 1
    assert twse.calls == [date(2026, 8, 7)]
    assert tpex.calls == []
    assert [item.stock_code for item in repository.get_by_stock_code("2330")] == [
        "2330"
    ]

    tpex_only_service, twse_only, tpex_only, tpex_repository = make_service(
        tmp_path / "tpex-only",
        [margin_record("2330", "TWSE")],
        [margin_record("3105", "TPEX")],
        stocks=[Stock("3105", "穩懋", "TPEX")],
    )
    tpex_only_result = tpex_only_service.sync(date(2026, 8, 7))
    assert tpex_only_result.margin_count == 1
    assert twse_only.calls == []
    assert tpex_only.calls == [date(2026, 8, 7)]
    assert [item.stock_code for item in tpex_repository.get_by_stock_code("3105")] == [
        "3105"
    ]


def test_margin_sync_idempotent_and_updates_existing_date(tmp_path):
    record = margin_record("2330", "TWSE")
    service, _, _, repository = make_service(tmp_path, [record], [])
    # Add a TPEx master and a corresponding row so both official sources are complete.
    service.tpex_provider.records = [margin_record("3105", "TPEX")]

    first = service.sync(date(2026, 8, 7))
    service.twse_provider.records = [margin_record("2330", "TWSE", margin_balance=999)]
    second = service.sync(date(2026, 8, 7))

    assert (first.inserted_count, first.updated_count) == (2, 0)
    assert (second.inserted_count, second.updated_count) == (0, 2)
    assert repository.get_by_stock_and_date("2330", "2026-08-07").margin_balance == 999


def test_margin_sync_non_trading_day_skips_tpex_and_preserves_history(tmp_path):
    service, _, tpex, repository = make_service(
        tmp_path,
        [margin_record("2330", "TWSE")],
        [margin_record("3105", "TPEX")],
    )
    service.sync(date(2026, 8, 7))
    service.twse_provider = FakeMarginProvider("TWSE", no_data=True)

    result = service.sync(date(2026, 8, 9))

    assert result.skipped_non_trading is True
    assert result.trade_date is None
    assert tpex.calls == [date(2026, 8, 7)]
    assert len(repository.get_range("2026-08-07", "2026-08-07")) == 2


def test_margin_sync_provider_failure_does_not_delete_history(tmp_path):
    service, _, _, repository = make_service(
        tmp_path,
        [margin_record("2330", "TWSE")],
        [margin_record("3105", "TPEX")],
    )
    service.sync(date(2026, 8, 7))
    service.twse_provider = FakeMarginProvider(
        "TWSE", error=StockProviderError("TWSE unavailable")
    )

    with pytest.raises(StockProviderError):
        service.sync(date(2026, 8, 8))

    assert len(repository.get_range("2026-08-07", "2026-08-07")) == 2


def test_margin_sync_date_mismatch_is_rejected_before_write(tmp_path):
    service, _, _, repository = make_service(
        tmp_path,
        [margin_record("2330", "TWSE", "2026-08-08")],
        [margin_record("3105", "TPEX", "2026-08-08")],
    )

    with pytest.raises(StockDataValidationError, match="unexpected trade date"):
        service.sync(date(2026, 8, 7))

    assert repository.get_range("2026-08-07", "2026-08-08") == []


def test_margin_sync_tpex_no_data_is_not_treated_as_success(tmp_path):
    service, _, _, repository = make_service(
        tmp_path,
        [margin_record("2330", "TWSE")],
        [margin_record("3105", "TPEX")],
    )
    service.tpex_provider = FakeMarginProvider("TPEX", no_data=True)

    with pytest.raises(StockDataValidationError, match="TPEx margin returned no data"):
        service.sync(date(2026, 8, 7))

    assert repository.get_range("2026-08-07", "2026-08-07") == []


def test_margin_sync_requires_stock_master(tmp_path):
    db_path = tmp_path / "data" / "stocks.db"
    stock_repository = StockRepository(db_path)
    stock_repository.create_tables()
    service = MarginSyncService(
        FakeMarginProvider("TWSE", [margin_record("2330", "TWSE")]),
        FakeMarginProvider("TPEX", [margin_record("3105", "TPEX")]),
        stock_repository,
        MarginHistoryRepository(db_path),
    )

    with pytest.raises(StockDataValidationError, match="Run stock-master sync first"):
        service.sync(date(2026, 8, 7))
