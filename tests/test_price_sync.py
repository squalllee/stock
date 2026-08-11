from datetime import date

from stock_master.models import PriceHistory, Stock
from stock_master.repositories.price_repository import PriceHistoryRepository
from stock_master.repositories.stock_repository import StockRepository
from stock_master.services.price_sync_service import PriceSyncService


class FakePriceProvider:
    def __init__(self, market, records=None, *, no_data=False):
        self.market = market
        self.records = list(records or [])
        self.no_data = no_data
        self.calls = []
        self.last_trade_date = None
        self.last_no_data = False
        self.last_skipped_total_count = 0
        self.configured_codes = None

    def set_stock_codes(self, codes):
        self.configured_codes = set(codes)

    def fetch(self, trade_date=None):
        self.calls.append(trade_date)
        self.last_no_data = self.no_data
        if self.no_data:
            self.last_trade_date = None
            return []
        self.last_trade_date = self.records[0].trade_date if self.records else None
        return list(self.records)


def price_record(stock_code, market, trade_date="2026-08-07"):
    return PriceHistory(
        trade_date=trade_date,
        stock_code=stock_code,
        market=market,
        trade_volume=1000,
        trade_value=200000,
        open_price=190.0,
        high_price=210.0,
        low_price=180.0,
        close_price=200.0,
        transaction_count=10,
    )


def make_service(tmp_path, twse_records, tpex_records, stocks=None):
    db_path = tmp_path / "data" / "stocks.db"
    stock_repository = StockRepository(db_path)
    stock_repository.upsert_many(
        stocks
        or [Stock("2330", "台積電", "TWSE"), Stock("3105", "穩懋", "TPEX")]
    )
    twse = FakePriceProvider("TWSE", twse_records)
    tpex = FakePriceProvider("TPEX", tpex_records)
    repository = PriceHistoryRepository(db_path)
    return (
        PriceSyncService(twse, tpex, stock_repository, repository),
        twse,
        tpex,
        repository,
    )


def test_price_sync_filters_to_stock_master_and_configures_tpex(tmp_path):
    service, twse, tpex, repository = make_service(
        tmp_path,
        [price_record("2330", "TWSE"), price_record("0050", "TWSE")],
        [price_record("3105", "TPEX"), price_record("00878", "TPEX")],
    )

    result = service.sync(date(2026, 8, 7))

    assert result.price_count == 2
    assert result.skipped_non_master_count == 2
    assert tpex.configured_codes == {"3105"}
    assert [item.stock_code for item in repository.get_range("2026-08-07", "2026-08-07")] == [
        "2330",
        "3105",
    ]
    assert twse.calls == [date(2026, 8, 7)]


def test_price_sync_skips_explicit_non_trading_day(tmp_path):
    service, _, tpex, repository = make_service(
        tmp_path,
        [price_record("2330", "TWSE")],
        [price_record("3105", "TPEX")],
    )
    service.sync(date(2026, 8, 7))
    service.twse_provider = FakePriceProvider("TWSE", no_data=True)

    result = service.sync(date(2026, 8, 9))

    assert result.skipped_non_trading is True
    assert tpex.calls == [date(2026, 8, 7)]
    assert len(repository.get_range("2026-08-07", "2026-08-07")) == 2

