import sqlite3

import pytest

from stock_master.exceptions import StockDataValidationError, SupabaseSyncError
from stock_master.main import build_parser
from stock_master.services.supabase_tdcc_sync_service import (
    SupabaseTDCCSyncService,
)


class FakeSupabaseRequest:
    def __init__(self, client):
        self.client = client

    def upsert(self, values, **options):
        self.client.upserts.append((list(values), dict(options)))
        return self

    def execute(self):
        if self.client.failures:
            self.client.failures -= 1
            raise RuntimeError("temporary Supabase failure")
        return object()


class FakeSupabaseClient:
    def __init__(self, failures=0):
        self.failures = failures
        self.tables = []
        self.upserts = []

    def table(self, table_name):
        self.tables.append(table_name)
        return FakeSupabaseRequest(self)


def make_database(tmp_path, rows):
    db_path = tmp_path / "stocks.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE tdcc_distributions ("
            "data_date TEXT NOT NULL, "
            "stock_code TEXT NOT NULL, "
            "holding_level TEXT NOT NULL, "
            "shareholder_count INTEGER NOT NULL, "
            "share_count INTEGER NOT NULL, "
            "holding_ratio REAL NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO tdcc_distributions VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
    return db_path


def test_supabase_tdcc_sync_upserts_only_requested_year_and_levels_1_to_15(
    tmp_path,
):
    db_path = make_database(
        tmp_path,
        [
            ("2025-12-26", "2330", "1", 10, 100, 1.0),
            ("2026-01-02", "2330", "01", 11, 101, 1.1),
            ("2026-01-02", "2330", "15", 12, 102, 1.2),
            ("2026-01-02", "2330", "16", 13, 103, 1.3),
            ("2026-01-02", "2330", "合計", 14, 104, 100.0),
        ],
    )
    client = FakeSupabaseClient()
    service = SupabaseTDCCSyncService(db_path, client, batch_size=1)

    result = service.sync(year=2026)

    assert result.source_count == 4
    assert result.synced_count == 2
    assert result.skipped_count == 2
    assert result.batch_count == 2
    assert client.tables == ["tdcc_distributions", "tdcc_distributions"]
    payloads = [batch[0] for batch, _options in client.upserts]
    assert [payload["holding_level"] for payload in payloads] == [1, 15]
    assert all(payload["data_date"] == "2026-01-02" for payload in payloads)
    assert all("created_at" not in payload for payload in payloads)
    assert all("updated_at" in payload for payload in payloads)
    for _batch, options in client.upserts:
        assert options == {
            "on_conflict": "data_date,stock_code,holding_level",
            "returning": "minimal",
            "default_to_null": False,
        }


def test_supabase_tdcc_sync_dry_run_needs_no_client(tmp_path):
    db_path = make_database(
        tmp_path,
        [("2026-08-07", "2330", "15", 10, 100, 1.0)],
    )

    result = SupabaseTDCCSyncService(db_path, None).sync(
        year=2026,
        dry_run=True,
    )

    assert result.synced_count == 1
    assert result.batch_count == 1
    assert result.dry_run is True


def test_supabase_tdcc_sync_retries_a_failed_batch(tmp_path):
    db_path = make_database(
        tmp_path,
        [("2026-08-07", "2330", "15", 10, 100, 1.0)],
    )
    client = FakeSupabaseClient(failures=1)
    delays = []
    service = SupabaseTDCCSyncService(
        db_path,
        client,
        max_attempts=2,
        backoff_seconds=0.25,
        sleep=delays.append,
    )

    result = service.sync(year=2026)

    assert result.synced_count == 1
    assert len(client.upserts) == 2
    assert delays == [0.25]


def test_supabase_tdcc_sync_reports_terminal_batch_failure(tmp_path):
    db_path = make_database(
        tmp_path,
        [("2026-08-07", "2330", "15", 10, 100, 1.0)],
    )
    service = SupabaseTDCCSyncService(
        db_path,
        FakeSupabaseClient(failures=2),
        max_attempts=2,
        backoff_seconds=0,
    )

    with pytest.raises(SupabaseSyncError, match="batch 1 after 2 attempt"):
        service.sync(year=2026)


def test_supabase_tdcc_sync_rejects_empty_year(tmp_path):
    db_path = make_database(
        tmp_path,
        [("2025-08-08", "2330", "15", 10, 100, 1.0)],
    )

    with pytest.raises(StockDataValidationError, match="no TDCC data for 2026"):
        SupabaseTDCCSyncService(db_path, None).sync(year=2026, dry_run=True)


def test_cli_exposes_supabase_tdcc_sync_options():
    args = build_parser().parse_args(
        [
            "tdcc-supabase-sync",
            "--year",
            "2026",
            "--batch-size",
            "250",
            "--dry-run",
        ]
    )

    assert args.command == "tdcc-supabase-sync"
    assert args.year == 2026
    assert args.batch_size == 250
    assert args.dry_run is True
