from dataclasses import replace
from datetime import date

import pytest

from stock_master.exceptions import StockDataValidationError, StockProviderError
from stock_master.models import MarginHistory, Stock
from stock_master.repositories.margin_repository import MarginHistoryRepository
from stock_master.repositories.stock_repository import StockRepository
from stock_master.services.margin_history_sync_service import (
    MarginHistorySyncService,
)
from stock_master.services.margin_sync_service import MarginSyncService


class DateAwareProvider:
    def __init__(self, market, template, *, no_data_dates=None, error_dates=None):
        self.market = market
        self.template = template
        self.no_data_dates = set(no_data_dates or [])
        self.error_dates = set(error_dates or [])
        self.calls = []
        self.last_trade_date = None
        self.last_no_data = False
        self.last_skipped_total_count = 0

    def fetch(self, trade_date=None):
        self.calls.append(trade_date)
        self.last_no_data = False
        if trade_date in self.error_dates:
            raise StockProviderError(f"{self.market} unavailable")
        if trade_date in self.no_data_dates:
            self.last_trade_date = None
            self.last_no_data = True
            return []
        current = trade_date or date(2026, 8, 7)
        self.last_trade_date = current.isoformat()
        return [replace(self.template, trade_date=current.isoformat())]


def make_history_service(tmp_path, *, no_data_dates=None, error_dates=None):
    db_path = tmp_path / "data" / "stocks.db"
    stock_repository = StockRepository(db_path)
    stock_repository.upsert_many(
        [Stock("2330", "台積電", "TWSE"), Stock("3105", "穩懋", "TPEX")]
    )
    twse_provider = DateAwareProvider(
        "TWSE",
        margin_record("2330", "TWSE"),
        no_data_dates=no_data_dates,
        error_dates=error_dates,
    )
    tpex_provider = DateAwareProvider("TPEX", margin_record("3105", "TPEX"))
    margin_repository = MarginHistoryRepository(db_path)
    margin_service = MarginSyncService(
        twse_provider,
        tpex_provider,
        stock_repository,
        margin_repository,
    )
    sleeps = []
    history_service = MarginHistorySyncService(
        margin_service,
        request_delay_seconds=0.2,
        sleep=sleeps.append,
    )
    return history_service, margin_repository, twse_provider, tpex_provider, sleeps


def margin_record(stock_code, market):
    return MarginHistory(
        trade_date="2026-08-07",
        stock_code=stock_code,
        market=market,
        margin_buy=1,
        margin_sell=2,
        margin_cash_redemption=3,
        margin_previous_balance=4,
        margin_balance=5,
        short_buy=6,
        short_sell=7,
        short_stock_redemption=8,
        short_previous_balance=9,
        short_balance=10,
        offsetting_volume=11,
    )


def test_margin_history_sync_iterates_calendar_dates_and_skips_no_data(tmp_path):
    history_service, repository, twse, tpex, sleeps = make_history_service(
        tmp_path,
        no_data_dates={date(2026, 8, 8)},
    )

    result = history_service.sync(date(2026, 8, 7), date(2026, 8, 9))

    assert result.attempted_days == 3
    assert result.synced_dates == ("2026-08-07", "2026-08-09")
    assert result.skipped_non_trading_dates == ("2026-08-08",)
    assert result.margin_count == 4
    assert sleeps == [0.2, 0.2]
    assert [item.trade_date for item in repository.get_range("2026-08-07", "2026-08-09")] == [
        "2026-08-07",
        "2026-08-07",
        "2026-08-09",
        "2026-08-09",
    ]
    assert tpex.calls == [date(2026, 8, 7), date(2026, 8, 9)]
    assert twse.calls == [
        date(2026, 8, 7),
        date(2026, 8, 8),
        date(2026, 8, 9),
    ]


def test_margin_history_partial_failure_preserves_previous_dates(tmp_path):
    history_service, repository, *_ = make_history_service(
        tmp_path,
        error_dates={date(2026, 8, 8)},
    )

    with pytest.raises(StockProviderError, match="TWSE unavailable"):
        history_service.sync(date(2026, 8, 7), date(2026, 8, 9))

    assert [
        (item.trade_date, item.stock_code)
        for item in repository.get_range("2026-08-07", "2026-08-09")
    ] == [("2026-08-07", "2330"), ("2026-08-07", "3105")]


def test_margin_history_rejects_reversed_range(tmp_path):
    history_service, *_ = make_history_service(tmp_path)

    with pytest.raises(StockDataValidationError, match="must not be after"):
        history_service.sync(date(2026, 8, 9), date(2026, 8, 7))
