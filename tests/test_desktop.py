from __future__ import annotations

import logging
import os
import queue
from dataclasses import dataclass
from datetime import date

import stock_master.desktop as desktop_module
from stock_master.desktop import (
    SyncSkipped,
    _default_price_date,
    build_parser,
    summarize_result,
)
from stock_master.config import load_project_dotenv
from stock_master.main import build_parser as build_main_parser


def test_desktop_parser_defaults_to_billdb_environment():
    args = build_parser().parse_args([])
    assert args.supabase_url is None


def test_desktop_log_handler_forwards_background_status():
    events = queue.Queue()
    handler = desktop_module._DesktopLogHandler(events)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    record = logging.LogRecord(
        "stock_master.test",
        logging.INFO,
        __file__,
        1,
        "同步股票 %s/%s",
        (12, 50),
        None,
    )

    handler.emit(record)

    assert events.get_nowait() == ("log", "INFO 同步股票 12/50")


def test_desktop_terminal_log_handler_prints_warning(capsys):
    handler = desktop_module._DesktopTerminalLogHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    record = logging.LogRecord(
        "stock_master.test",
        logging.WARNING,
        __file__,
        1,
        "HTTP attempt %s/%s failed for %s: status=%s",
        (1, 3, "https://example.test", 504),
        None,
    )

    handler.emit(record)

    captured = capsys.readouterr()
    assert captured.err == (
        "WARNING HTTP attempt 1/3 failed for https://example.test: status=504\n"
    )


def test_main_parser_exposes_desktop_command():
    args = build_main_parser().parse_args(
        ["desktop", "--supabase-url", "https://example.supabase.co"]
    )
    assert args.command == "desktop"
    assert args.supabase_url == "https://example.supabase.co"


def test_dotenv_loads_supabase_key_without_overriding_process_environment(
    tmp_path, monkeypatch
):
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        'SUPABASE_SECRET_KEY="from-file"\n'
        "SUPABASE_URL=https://from-file.supabase.co # comment\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SUPABASE_SECRET_KEY", raising=False)
    monkeypatch.setenv("SUPABASE_URL", "https://from-process.supabase.co")

    assert load_project_dotenv() == dotenv
    assert os.environ["SUPABASE_SECRET_KEY"] == "from-file"
    assert os.environ["SUPABASE_URL"] == "https://from-process.supabase.co"


def test_desktop_result_summaries_are_user_facing():
    @dataclass(frozen=True)
    class StockResult:
        total_count: int
        twse_count: int
        tpex_count: int

    summary = summarize_result(
        "stock-master",
        StockResult(total_count=10, twse_count=7, tpex_count=3),
    )
    assert summary == "完成：股票主檔 10 筆（TWSE 7、TPEX 3）"
    assert "沒有新的股權分散資料" in summarize_result(
        "tdcc-latest", SyncSkipped("TDCC 官方目前沒有新的股權分散資料")
    )


def test_desktop_tdcc_latest_turns_existing_weekly_data_into_skip(monkeypatch):
    class FakeService:
        def sync_tdcc_latest(self):
            return {
                "skipped": True,
                "reason": "TDCC 最新一期資料已同步",
            }

    monkeypatch.setattr(desktop_module, "_market_service", lambda _: FakeService())

    result = desktop_module.sync_tdcc_latest(object())

    assert result == SyncSkipped("TDCC 最新一期資料已同步")


def test_default_price_date_moves_weekend_back_to_friday():
    assert _default_price_date(date(2026, 8, 23)) == date(2026, 8, 21)


def test_desktop_daily_price_wrapper_accepts_date_range(monkeypatch):
    calls = []

    class FakeService:
        def sync_daily_prices(self, start_date, end_date):
            calls.append((start_date, end_date))
            return "done"

    monkeypatch.setattr(desktop_module, "_market_service", lambda _: FakeService())

    assert desktop_module.sync_daily_prices(
        object(), "2026-08-20", "2026-08-21"
    ) == "done"
    assert calls == [("2026-08-20", "2026-08-21")]


def test_desktop_range_result_summary_includes_dates_and_skips():
    @dataclass(frozen=True)
    class RangeResult:
        start_date: str
        end_date: str
        synced_dates: tuple[str, ...]
        skipped_non_trading_dates: tuple[str, ...]
        price_count: int

    summary = summarize_result(
        "daily-prices",
        RangeResult(
            "2026-08-20",
            "2026-08-23",
            ("2026-08-20", "2026-08-21"),
            ("2026-08-22", "2026-08-23"),
            3200,
        ),
    )

    assert summary == (
        "完成：每日成交行情 3,200 筆，2026-08-20 ～ 2026-08-23，"
        "同步 2 個交易日，略過 2 天"
    )


def test_desktop_tdcc_year_summary_shows_resume_progress():
    summary = summarize_result(
        "tdcc-year",
        {
            "year": 2026,
            "data_dates": ["2026-08-21", "2026-08-14"],
            "completed_query_count": 25,
            "skipped_checkpoint_count": 75,
            "result": {"tdcc_count": 360},
        },
    )

    assert summary == (
        "完成：TDCC 2026 年新增同步 360 筆，共 2 個資料日期；"
        "本次查詢 25 組，略過 75 組"
    )
