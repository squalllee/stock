import sqlite3
from types import SimpleNamespace

import pytest

import sync_tdcc_to_supabase as sync
from sync_tdcc_to_supabase import (
    SyncError,
    _normalize_history_records,
    load_stock_codes,
    normalize_data_date,
    normalize_tdcc_payload,
    upsert_rows,
)
from stock_master.models import TDCCDistribution


def _record(
    *,
    code="2330",
    level="1",
    data_date="20260821",
    holders="1,234",
    shares="12,345",
    ratio="12.34%",
):
    return {
        "\ufeff資料日期": data_date,
        "證券代號": code,
        "持股分級": level,
        "人數": holders,
        "股數": shares,
        "占集保庫存數比例%": ratio,
    }


def test_normalizes_official_feed_and_keeps_only_levels_1_to_15():
    payload = [
        _record(level="1"),
        _record(level="15", code="00400A", ratio="0.00"),
        _record(level="16"),
        _record(level="17"),
        _record(level="合計", ratio="100.00%"),
    ]

    rows, stats = normalize_tdcc_payload(payload, year=2026)

    assert [(row.stock_code, row.holding_level) for row in rows] == [
        ("2330", 1),
        ("00400A", 15),
    ]
    assert rows[0].data_date == "2026-08-21"
    assert rows[0].shareholder_count == 1234
    assert rows[0].share_count == 12345
    assert rows[0].holding_ratio == 12.34
    assert stats.raw_count == 5
    assert stats.skipped_count == 3
    assert stats.data_dates == ("2026-08-21",)


def test_load_stock_codes_reads_the_sqlite_stock_master(tmp_path):
    db_path = tmp_path / "stocks.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        "create table stocks (stock_code text, stock_name text, market text)"
    )
    connection.executemany(
        "insert into stocks values (?, ?, ?)",
        [("2330", "台積電", "TWSE"), ("6488", "環球晶", "TPEX")],
    )
    connection.commit()
    connection.close()

    assert load_stock_codes(db_path) == {"2330", "6488"}


def test_latest_sync_can_restrict_rows_to_stock_master():
    payload = [_record(code="2330"), _record(code="0050", level="2")]

    result = sync.synchronize(
        client=None,
        payload=payload,
        stock_codes={"2330"},
        dry_run=True,
    )

    assert result.feed.stock_count == 1
    assert result.feed.synced_candidate_count == 1
    assert result.feed.skipped_count == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("115/08/21", "2026-08-21"),
        ("1150821", "2026-08-21"),
        ("2026-08-21", "2026-08-21"),
        ("20260821", "2026-08-21"),
    ],
)
def test_normalize_data_date_supports_roc_and_iso(raw, expected):
    assert normalize_data_date(raw) == expected


def test_normalize_data_date_rejects_malformed_future_year():
    with pytest.raises(SyncError, match="parsed year 5115"):
        normalize_data_date("51150831")


def test_year_filter_rejects_feed_without_matching_rows():
    with pytest.raises(SyncError, match="year 2025"):
        normalize_tdcc_payload([_record()], year=2025)


def test_schema_error_is_raised_before_upload():
    invalid = _record()
    del invalid["占集保庫存數比例%"]

    with pytest.raises(SyncError, match="holding_ratio"):
        normalize_tdcc_payload([invalid])


class FakeSupabaseClient:
    def __init__(self):
        self.calls = []

    def table(self, table_name):
        return FakeSupabaseTable(self, table_name)


class FakeSupabaseTable:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name

    def upsert(self, payload, **kwargs):
        self.client.calls.append((self.table_name, payload, kwargs))
        return self

    def execute(self):
        return object()


def test_upsert_uses_billdb_composite_key_and_batches():
    rows, _ = normalize_tdcc_payload(
        [_record(level="1"), _record(level="2"), _record(level="3")]
    )
    client = FakeSupabaseClient()

    uploaded, batches = upsert_rows(client, rows, batch_size=2)

    assert uploaded == 3
    assert batches == 2
    assert [len(call[1]) for call in client.calls] == [2, 1]
    assert all(
        call[0] == "tdcc_distributions"
        and call[2]["on_conflict"] == "data_date,stock_code,holding_level"
        and call[2]["returning"] == "minimal"
        for call in client.calls
    )


def test_normalize_history_records_discards_levels_above_15():
    records = [
        TDCCDistribution("2026-01-02", "2330", "1", 1, 10, 1.0),
        TDCCDistribution("2026-01-02", "2330", "15", 2, 20, 2.0),
        TDCCDistribution("2026-01-02", "2330", "16", 3, 30, 3.0),
    ]

    rows, skipped = _normalize_history_records(records, year=2026)

    assert [(row.data_date, row.holding_level) for row in rows] == [
        ("2026-01-02", 1),
        ("2026-01-02", 15),
    ]
    assert skipped == 1


def test_annual_sync_uses_historical_dates_and_returns_dry_run_stats(monkeypatch):
    monkeypatch.setattr(sync, "fetch_json", lambda *args, **kwargs: [_record()])
    dates = ("2026-01-02", "2026-08-21")
    monkeypatch.setattr(sync, "_history_dates", lambda **kwargs: dates)

    fake_provider = SimpleNamespace(
        last_data_dates=dates,
        last_raw_record_count=3,
        fetch=lambda stock_codes: [
            TDCCDistribution("2026-01-02", "2330", "1", 1, 10, 1.0),
            TDCCDistribution("2026-01-02", "2330", "16", 2, 20, 2.0),
        ],
    )
    monkeypatch.setattr(sync, "_history_provider", lambda **kwargs: fake_provider)

    result = sync.synchronize_historical_year(
        client=None,
        year=2026,
        stock_codes=["2330"],
        workers=1,
        request_delay_seconds=0,
        chunk_size=1,
        dry_run=True,
    )

    assert result.mode == "historical"
    assert result.feed.data_dates == ("2026-01-02",)
    assert result.feed.synced_candidate_count == 1
    assert result.feed.skipped_count == 2
    assert result.batch_count == 1
