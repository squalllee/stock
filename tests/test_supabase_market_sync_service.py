from __future__ import annotations

from dataclasses import dataclass

import pytest

import stock_master.services.supabase_market_sync_service as market_sync_module
from stock_master.exceptions import StockDataValidationError, StockProviderError
from stock_master.models import InsiderTransaction, PriceHistory, TDCCDistribution
from stock_master.providers import TDCCHistoricalQueryResult
from stock_master.services.supabase_market_sync_service import (
    SupabaseMarketSyncService,
    _SupabasePriceRepository,
    _SupabaseStockUniverse,
    _SupabaseTDCCCheckpointRepository,
    _SupabaseTDCCRepository,
)


@dataclass
class _Response:
    data: list[dict]


class _Query:
    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.values = None
        self.offset = 0
        self.order_desc = False
        self.limit_size = None
        self.filters = []

    def select(self, _fields):
        return self

    def range(self, start, _end):
        self.offset = start
        return self

    def order(self, _column, *, desc=False):
        self.order_desc = desc
        return self

    def limit(self, size):
        self.limit_size = size
        return self

    def gte(self, column, value):
        self.filters.append(("gte", column, value))
        return self

    def lte(self, column, value):
        self.filters.append(("lte", column, value))
        return self

    def upsert(self, values, *, on_conflict, returning, default_to_null):
        self.values = list(values)
        self.client.upsert_calls.append((self.table_name, self.values, on_conflict))
        return self

    def execute(self):
        if self.values is not None:
            return _Response([])
        rows = self.client.tables.get(self.table_name, [])
        for operator, column, value in self.filters:
            if operator == "gte":
                rows = [row for row in rows if row.get(column, "") >= value]
            else:
                rows = [row for row in rows if row.get(column, "") <= value]
        if self.order_desc:
            rows = sorted(rows, key=lambda row: row.get("data_date", ""), reverse=True)
        if self.limit_size is not None:
            rows = rows[: self.limit_size]
        return _Response(rows[self.offset : self.offset + 1000])


class _Client:
    def __init__(self):
        self.tables = {
            "stocks": [
                {"stock_code": "2330", "stock_name": "台積電", "market": "TWSE"},
                {"stock_code": "3105", "stock_name": "穩懋", "market": "TPEX"},
            ],
            "tdcc_distributions": [],
            "tdcc_sync_checkpoints": [],
        }
        self.upsert_calls = []

    def table(self, table_name):
        return _Query(self, table_name)


def test_supabase_stock_universe_reads_stocks_without_sqlite():
    client = _Client()
    values = _SupabaseStockUniverse(client).get_all()
    assert [(value.stock_code, value.market) for value in values] == [
        ("2330", "TWSE"),
        ("3105", "TPEX"),
    ]


def test_supabase_adapters_upsert_normalized_price_and_tdcc_rows():
    client = _Client()
    service = SupabaseMarketSyncService(client, batch_size=1, backoff_seconds=0)

    price_repository = _SupabasePriceRepository(service.writer)
    price_repository.upsert_many(
        [
            PriceHistory(
                trade_date="2026-08-21",
                stock_code="2330",
                market="TWSE",
                trade_volume=100,
                trade_value=65000,
                close_price=650,
            )
        ]
    )
    tdcc_repository = _SupabaseTDCCRepository(service.writer)
    tdcc_repository.upsert_many(
        [
            TDCCDistribution("2026-08-21", "2330", "01", 10, 1000, 1.2),
            TDCCDistribution("2026-08-21", "2330", "1-999", 10, 1000, 1.2),
        ]
    )

    assert client.upsert_calls[0][0] == "price_history"
    assert client.upsert_calls[0][2] == "trade_date,stock_code"
    assert client.upsert_calls[0][1][0]["market_average_price"] == 650
    assert client.upsert_calls[1][0] == "tdcc_distributions"
    assert client.upsert_calls[1][1][0]["holding_level"] == 1
    assert len(client.upsert_calls[1][1]) == 1


def test_supabase_insider_sync_filters_to_stock_universe_and_upserts(monkeypatch):
    client = _Client()
    fetch_calls = []

    class FakeProvider:
        def __init__(self, _client, *, market, **kwargs):
            self.market = market
            self.report_type = kwargs.get("report_type", "planned_transfer")
            self.last_report_date = "2026-08-28"

        def fetch(self, stock_codes):
            fetch_calls.append((self.market, self.report_type, set(stock_codes)))
            if self.report_type == "untransferred":
                return [
                    InsiderTransaction(
                        report_date="2026-08-28",
                        stock_code="3105",
                        market=self.market,
                        report_type="untransferred",
                        transaction_type="untransferred",
                        insider_name="李小華",
                        insider_role="經理人",
                        shares_changed=100,
                        source="tpex_openapi",
                        source_record_key="key-untransferred",
                    )
                ]
            return [
                InsiderTransaction(
                    report_date="2026-08-28",
                    stock_code="2330",
                    market=self.market,
                    report_type="planned_transfer",
                    transaction_type="transfer",
                    insider_name="王小明",
                    insider_role="董事",
                    shares_changed=200,
                    source=f"{self.market.casefold()}_openapi",
                    source_record_key=f"key-{self.market}",
                )
            ]

    class FakeTransferProvider(FakeProvider):
        def __init__(self, client, *, market):
            super().__init__(client, market=market, report_type="planned_transfer")

    class FakeUntransferredProvider(FakeProvider):
        def __init__(self, client, *, market):
            super().__init__(client, market=market, report_type="untransferred")

    monkeypatch.setattr(
        market_sync_module, "InsiderTransferProvider", FakeTransferProvider
    )
    monkeypatch.setattr(
        market_sync_module,
        "InsiderUntransferredProvider",
        FakeUntransferredProvider,
    )
    service = SupabaseMarketSyncService(client, batch_size=10, backoff_seconds=0)

    result = service.sync_insider_transactions()

    assert result["record_count"] == 4
    assert result["latest_data_date"] == "2026-08-28"
    assert result["report_type_counts"] == {
        "planned_transfer": 2,
        "untransferred": 2,
    }
    assert len(fetch_calls) == 4
    assert all(codes == {"2330", "3105"} for _, _, codes in fetch_calls)
    assert client.upsert_calls[-1][0] == "insider_transactions"
    assert client.upsert_calls[-1][2] == "source,source_record_key"


def test_supabase_tdcc_latest_skips_when_weekly_date_is_already_stored(
    monkeypatch,
):
    client = _Client()
    client.tables["tdcc_distributions"] = [
        {"data_date": "2026-08-21"},
    ]

    class FakeTDCCProvider:
        last_skipped_total_count = 0

        def __init__(self, _client, *, url):
            self.url = url

        def fetch(self, stock_codes):
            assert stock_codes == {"2330", "3105"}
            return [
                TDCCDistribution("2026-08-21", "2330", "1", 10, 100, 1.0)
            ]

    monkeypatch.setattr(
        market_sync_module, "TDCCDistributionProvider", FakeTDCCProvider
    )
    service = SupabaseMarketSyncService(client, backoff_seconds=0)

    result = service.sync_tdcc_latest()

    assert result == {
        "skipped": True,
        "reason": "TDCC 最新一期資料已同步",
        "data_date": "2026-08-21",
    }
    assert client.upsert_calls == []


def test_supabase_checkpoint_repository_reads_and_upserts_completed_queries():
    client = _Client()
    client.tables["tdcc_sync_checkpoints"] = [
        {
            "data_date": "2026-08-21",
            "stock_code": "2330",
            "status": "completed",
            "record_count": 15,
        },
        {
            "data_date": "2025-12-26",
            "stock_code": "2330",
            "status": "completed",
            "record_count": 15,
        },
    ]
    service = SupabaseMarketSyncService(client, backoff_seconds=0)
    repository = _SupabaseTDCCCheckpointRepository(service.writer)

    assert repository.get_completed("2026-01-01", "2026-12-31") == {
        ("2026-08-21", "2330")
    }
    repository.upsert_many(
        [TDCCHistoricalQueryResult("2026-08-14", "3105", 0)]
    )

    table_name, values, conflict = client.upsert_calls[-1]
    assert table_name == "tdcc_sync_checkpoints"
    assert conflict == "data_date,stock_code"
    assert values[0]["status"] == "no_data"
    assert values[0]["record_count"] == 0


def test_supabase_tdcc_year_writes_each_stock_batch_and_resumes(monkeypatch):
    client = _Client()
    client.tables["tdcc_sync_checkpoints"] = [
        {
            "data_date": "2026-08-21",
            "stock_code": "2330",
            "status": "completed",
            "record_count": 1,
        }
    ]
    fetch_calls = []

    class FakeHistoricalProvider:
        def __init__(self, _client_factory, **kwargs):
            self.workers = kwargs["workers"]
            self.last_query_results = ()
            self.last_request_count = 0
            self.last_skipped_total_count = 0

        def available_dates(self):
            return ("2026-08-21", "2026-08-14")

        def fetch(self, stock_codes, *, completed_queries, selected_dates):
            fetch_calls.append((set(stock_codes), set(completed_queries)))
            results = [
                TDCCHistoricalQueryResult(data_date, stock_code, 1)
                for stock_code in sorted(stock_codes)
                for data_date in selected_dates
                if (data_date, stock_code) not in completed_queries
            ]
            self.last_query_results = tuple(results)
            self.last_request_count = len(results)
            return [
                TDCCDistribution(
                    result.data_date,
                    result.stock_code,
                    "1",
                    10,
                    100,
                    1.0,
                )
                for result in results
            ]

    monkeypatch.setattr(
        market_sync_module,
        "TDCCHistoricalDistributionProvider",
        FakeHistoricalProvider,
    )
    service = SupabaseMarketSyncService(
        client,
        batch_size=500,
        backoff_seconds=0,
        tdcc_history_stock_batch_size=1,
    )

    result = service.sync_tdcc_year(2026)

    assert fetch_calls[0][0] == {"2330"}
    assert ("2026-08-21", "2330") in fetch_calls[0][1]
    assert result["total_query_count"] == 4
    assert result["skipped_checkpoint_count"] == 1
    assert result["completed_query_count"] == 3
    assert result["remaining_query_count"] == 0
    assert result["request_count"] == 3
    assert result["result"].tdcc_count == 3
    assert [call[0] for call in client.upsert_calls] == [
        "tdcc_distributions",
        "tdcc_sync_checkpoints",
        "tdcc_distributions",
        "tdcc_sync_checkpoints",
    ]


@pytest.mark.parametrize(
    "parallel_failure",
    [
        StockProviderError("status=504"),
        StockDataValidationError(
            "TDCC historical session initialization failed after 3 attempt(s): "
            "missing form session fields."
        ),
    ],
)
def test_supabase_tdcc_year_retries_failed_parallel_batch_with_one_worker(
    monkeypatch,
    parallel_failure,
):
    client = _Client()
    client.tables["stocks"] = client.tables["stocks"][:1]
    fetch_workers = []

    class FakeHistoricalProvider:
        def __init__(self, _client_factory, **kwargs):
            self.workers = kwargs["workers"]
            self.last_query_results = ()
            self.last_request_count = 0
            self.last_skipped_total_count = 0

        def available_dates(self):
            return ("2026-08-21",)

        def fetch(self, stock_codes, *, completed_queries, selected_dates):
            fetch_workers.append(self.workers)
            if self.workers > 1:
                raise parallel_failure
            result = TDCCHistoricalQueryResult("2026-08-21", "2330", 1)
            self.last_query_results = (result,)
            self.last_request_count = 1
            return [
                TDCCDistribution("2026-08-21", "2330", "1", 10, 100, 1.0)
            ]

    monkeypatch.setattr(
        market_sync_module,
        "TDCCHistoricalDistributionProvider",
        FakeHistoricalProvider,
    )
    service = SupabaseMarketSyncService(client, backoff_seconds=0)

    result = service.sync_tdcc_year(2026)

    assert fetch_workers == [2, 1]
    assert result["remaining_query_count"] == 0


def test_supabase_daily_price_sync_accepts_inclusive_date_range(monkeypatch):
    client = _Client()
    calls = {}

    class FakePriceSyncService:
        def __init__(self, *providers):
            calls["price_service"] = providers

    class FakePriceHistorySyncService:
        def __init__(self, price_service, *, request_delay_seconds):
            calls["history_service"] = (price_service, request_delay_seconds)

        def sync(self, start_date, end_date):
            calls["range"] = (start_date, end_date)
            return "range-result"

    monkeypatch.setattr(
        market_sync_module, "PriceSyncService", FakePriceSyncService
    )
    monkeypatch.setattr(
        market_sync_module,
        "PriceHistorySyncService",
        FakePriceHistorySyncService,
    )

    service = SupabaseMarketSyncService(client, backoff_seconds=0)

    assert service.sync_daily_prices("2026-08-20", "2026-08-21") == "range-result"
    assert calls["range"] == ("2026-08-20", "2026-08-21")
