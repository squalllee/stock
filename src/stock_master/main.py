"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, timedelta
from pathlib import Path

from stock_master.config import (
    BILLDB_SUPABASE_URL,
    DEFAULT_DATABASE_PATH,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MARGIN_HISTORY_DAYS,
    DEFAULT_MARGIN_HISTORY_REQUEST_DELAY_SECONDS,
    DEFAULT_MARGIN_FINANCING_RATIO,
    DEFAULT_MARGIN_MODEL_VERSION,
    DEFAULT_MIN_EXPECTED_TPEX_STOCKS,
    DEFAULT_MIN_EXPECTED_TWSE_STOCKS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    DEFAULT_SUPABASE_TDCC_BATCH_SIZE,
    DEFAULT_TDCC_HISTORY_DAYS,
    DEFAULT_TDCC_HISTORY_REQUEST_DELAY_SECONDS,
    DEFAULT_TDCC_HISTORY_WORKERS,
    DEFAULT_PRICE_HISTORY_REQUEST_DELAY_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    TDCC_API_URL,
    TDCC_HISTORY_URL,
    TPEX_MARGIN_URL,
    TPEX_PRICE_URL,
    TPEX_API_URL,
    TWSE_MARGIN_URL,
    TWSE_PRICE_URL,
    TWSE_API_URL,
    load_project_dotenv,
)
from stock_master.exceptions import StockMasterError
from stock_master.providers import (
    JsonHttpClient,
    TPExMarginProvider,
    TDCCDistributionProvider,
    TDCCHistoricalDistributionProvider,
    TextHttpClient,
    TPExPriceProvider,
    TPExStockProvider,
    TWSEMarginProvider,
    TWSEPriceProvider,
    TWSEStockProvider,
)
from stock_master.repositories import (
    MarginHistoryRepository,
    MarginEstimateRepository,
    PriceHistoryRepository,
    StockRepository,
    TDCCDistributionRepository,
)
from stock_master.services import (
    MarginHistorySyncService,
    MarginSyncService,
    MarginCostEstimator,
    PriceHistorySyncService,
    PriceSyncService,
    StockSyncService,
    SupabaseTDCCSyncService,
    TDCCSyncService,
    create_supabase_client,
)

logger = logging.getLogger(__name__)


def _parse_iso_date(value: str) -> date:
    """argparse converter for an explicit Gregorian ISO date."""

    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock-master",
        description="Synchronize Taiwan stock master and TDCC data into SQLite.",
    )
    subparsers = parser.add_subparsers(dest="command")

    sync_parser = subparsers.add_parser("sync", help="synchronize the stock master")
    sync_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite path (default: {DEFAULT_DATABASE_PATH})",
    )
    sync_parser.add_argument("--twse-url", default=TWSE_API_URL)
    sync_parser.add_argument("--tpex-url", default=TPEX_API_URL)
    sync_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    sync_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"HTTP attempts per provider (default: {DEFAULT_MAX_ATTEMPTS})",
    )
    sync_parser.add_argument(
        "--backoff-seconds",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help=f"Initial retry backoff (default: {DEFAULT_RETRY_BACKOFF_SECONDS})",
    )
    sync_parser.add_argument(
        "--min-twse",
        type=int,
        default=DEFAULT_MIN_EXPECTED_TWSE_STOCKS,
        help=f"minimum accepted TWSE records (default: {DEFAULT_MIN_EXPECTED_TWSE_STOCKS})",
    )
    sync_parser.add_argument(
        "--min-tpex",
        type=int,
        default=DEFAULT_MIN_EXPECTED_TPEX_STOCKS,
        help=f"minimum accepted TPEx records (default: {DEFAULT_MIN_EXPECTED_TPEX_STOCKS})",
    )
    sync_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="logging level (default: INFO)",
    )

    tdcc_parser = subparsers.add_parser(
        "tdcc-sync", help="synchronize TDCC shareholding distributions"
    )
    tdcc_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite path (default: {DEFAULT_DATABASE_PATH})",
    )
    tdcc_parser.add_argument("--tdcc-url", default=TDCC_API_URL)
    tdcc_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    tdcc_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"HTTP attempts per provider (default: {DEFAULT_MAX_ATTEMPTS})",
    )
    tdcc_parser.add_argument(
        "--backoff-seconds",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help=f"Initial retry backoff (default: {DEFAULT_RETRY_BACKOFF_SECONDS})",
    )
    tdcc_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="logging level (default: INFO)",
    )

    tdcc_history_parser = subparsers.add_parser(
        "tdcc-month-sync",
        aliases=["tdcc-history-sync"],
        help="synchronize recent weekly TDCC historical distributions",
    )
    tdcc_history_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite path (default: {DEFAULT_DATABASE_PATH})",
    )
    tdcc_history_parser.add_argument("--tdcc-history-url", default=TDCC_HISTORY_URL)
    tdcc_history_parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_TDCC_HISTORY_DAYS,
        help=f"calendar-day window ending today (default: {DEFAULT_TDCC_HISTORY_DAYS})",
    )
    tdcc_history_parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_TDCC_HISTORY_WORKERS,
        help=(
            "parallel TDCC sessions; keep conservative to avoid throttling "
            f"(default: {DEFAULT_TDCC_HISTORY_WORKERS})"
        ),
    )
    tdcc_history_parser.add_argument(
        "--request-delay",
        type=float,
        default=DEFAULT_TDCC_HISTORY_REQUEST_DELAY_SECONDS,
        help=(
            "seconds between historical form requests per worker "
            f"(default: {DEFAULT_TDCC_HISTORY_REQUEST_DELAY_SECONDS})"
        ),
    )
    tdcc_history_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    tdcc_history_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"HTTP attempts per request (default: {DEFAULT_MAX_ATTEMPTS})",
    )
    tdcc_history_parser.add_argument(
        "--backoff-seconds",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help=f"Initial retry backoff (default: {DEFAULT_RETRY_BACKOFF_SECONDS})",
    )
    tdcc_history_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="logging level (default: INFO)",
    )

    tdcc_supabase_parser = subparsers.add_parser(
        "tdcc-supabase-sync",
        help="upsert one calendar year of local TDCC data into Supabase",
    )
    tdcc_supabase_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite path (default: {DEFAULT_DATABASE_PATH})",
    )
    tdcc_supabase_parser.add_argument(
        "--year",
        type=int,
        default=date.today().year,
        help="calendar year to sync (default: current year)",
    )
    tdcc_supabase_parser.add_argument(
        "--supabase-url",
        default=None,
        help=(
            "Supabase project URL; defaults to SUPABASE_URL or the BillDB URL"
        ),
    )
    tdcc_supabase_parser.add_argument(
        "--table",
        default="tdcc_distributions",
        help="Supabase table name (default: tdcc_distributions)",
    )
    tdcc_supabase_parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_SUPABASE_TDCC_BATCH_SIZE,
        help=(
            "rows per Supabase upsert request "
            f"(default: {DEFAULT_SUPABASE_TDCC_BATCH_SIZE})"
        ),
    )
    tdcc_supabase_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"Supabase attempts per batch (default: {DEFAULT_MAX_ATTEMPTS})",
    )
    tdcc_supabase_parser.add_argument(
        "--backoff-seconds",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help=f"Initial retry backoff (default: {DEFAULT_RETRY_BACKOFF_SECONDS})",
    )
    tdcc_supabase_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and count rows without connecting to Supabase",
    )
    tdcc_supabase_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="logging level (default: INFO)",
    )

    margin_parser = subparsers.add_parser(
        "margin-sync", help="synchronize one day of margin-trading history"
    )
    margin_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite path (default: {DEFAULT_DATABASE_PATH})",
    )
    margin_parser.add_argument(
        "--date",
        type=_parse_iso_date,
        default=None,
        help="trading date in YYYY-MM-DD; omit to use the latest date",
    )
    margin_parser.add_argument("--twse-margin-url", default=TWSE_MARGIN_URL)
    margin_parser.add_argument("--tpex-margin-url", default=TPEX_MARGIN_URL)
    margin_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    margin_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"HTTP attempts per provider (default: {DEFAULT_MAX_ATTEMPTS})",
    )
    margin_parser.add_argument(
        "--backoff-seconds",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help=f"Initial retry backoff (default: {DEFAULT_RETRY_BACKOFF_SECONDS})",
    )
    margin_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="logging level (default: INFO)",
    )

    margin_history_parser = subparsers.add_parser(
        "margin-history-sync",
        help="synchronize a calendar-date range of margin history",
    )
    margin_history_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite path (default: {DEFAULT_DATABASE_PATH})",
    )
    margin_history_parser.add_argument(
        "--start-date",
        type=_parse_iso_date,
        default=None,
        help=(
            "inclusive start date in YYYY-MM-DD; defaults to the beginning "
            f"of the latest {DEFAULT_MARGIN_HISTORY_DAYS}-day window"
        ),
    )
    margin_history_parser.add_argument(
        "--end-date",
        type=_parse_iso_date,
        default=None,
        help="inclusive end date in YYYY-MM-DD; defaults to today",
    )
    margin_history_parser.add_argument(
        "--twse-margin-url", default=TWSE_MARGIN_URL
    )
    margin_history_parser.add_argument(
        "--tpex-margin-url", default=TPEX_MARGIN_URL
    )
    margin_history_parser.add_argument(
        "--request-delay",
        type=float,
        default=DEFAULT_MARGIN_HISTORY_REQUEST_DELAY_SECONDS,
        help=(
            "seconds between date requests "
            f"(default: {DEFAULT_MARGIN_HISTORY_REQUEST_DELAY_SECONDS})"
        ),
    )
    margin_history_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    margin_history_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"HTTP attempts per provider (default: {DEFAULT_MAX_ATTEMPTS})",
    )
    margin_history_parser.add_argument(
        "--backoff-seconds",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help=f"Initial retry backoff (default: {DEFAULT_RETRY_BACKOFF_SECONDS})",
    )
    margin_history_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="logging level (default: INFO)",
    )

    price_parser = subparsers.add_parser(
        "price-sync", help="synchronize one day of daily closing prices"
    )
    price_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite path (default: {DEFAULT_DATABASE_PATH})",
    )
    price_parser.add_argument(
        "--date",
        type=_parse_iso_date,
        default=None,
        help="trading date in YYYY-MM-DD; omit to use the latest date",
    )
    price_parser.add_argument("--twse-price-url", default=TWSE_PRICE_URL)
    price_parser.add_argument("--tpex-price-url", default=TPEX_PRICE_URL)
    price_parser.add_argument(
        "--request-delay",
        type=float,
        default=DEFAULT_PRICE_HISTORY_REQUEST_DELAY_SECONDS,
        help=(
            "seconds between TPEx code/month requests "
            f"(default: {DEFAULT_PRICE_HISTORY_REQUEST_DELAY_SECONDS})"
        ),
    )
    price_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    price_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"HTTP attempts per provider (default: {DEFAULT_MAX_ATTEMPTS})",
    )
    price_parser.add_argument(
        "--backoff-seconds",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help=f"Initial retry backoff (default: {DEFAULT_RETRY_BACKOFF_SECONDS})",
    )
    price_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="logging level (default: INFO)",
    )

    price_history_parser = subparsers.add_parser(
        "price-history-sync",
        help="synchronize a calendar-date range of daily prices",
    )
    price_history_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite path (default: {DEFAULT_DATABASE_PATH})",
    )
    price_history_parser.add_argument(
        "--start-date",
        type=_parse_iso_date,
        default=None,
        help=(
            "inclusive start date in YYYY-MM-DD; defaults to the beginning "
            f"of the latest {DEFAULT_MARGIN_HISTORY_DAYS}-day window"
        ),
    )
    price_history_parser.add_argument(
        "--end-date",
        type=_parse_iso_date,
        default=None,
        help="inclusive end date in YYYY-MM-DD; defaults to today",
    )
    price_history_parser.add_argument("--twse-price-url", default=TWSE_PRICE_URL)
    price_history_parser.add_argument("--tpex-price-url", default=TPEX_PRICE_URL)
    price_history_parser.add_argument(
        "--request-delay",
        type=float,
        default=DEFAULT_PRICE_HISTORY_REQUEST_DELAY_SECONDS,
        help=(
            "seconds between date and TPEx code/month requests "
            f"(default: {DEFAULT_PRICE_HISTORY_REQUEST_DELAY_SECONDS})"
        ),
    )
    price_history_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"HTTP timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    price_history_parser.add_argument(
        "--max-attempts",
        type=int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"HTTP attempts per provider (default: {DEFAULT_MAX_ATTEMPTS})",
    )
    price_history_parser.add_argument(
        "--backoff-seconds",
        type=float,
        default=DEFAULT_RETRY_BACKOFF_SECONDS,
        help=f"Initial retry backoff (default: {DEFAULT_RETRY_BACKOFF_SECONDS})",
    )
    price_history_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="logging level (default: INFO)",
    )

    estimate_parser = subparsers.add_parser(
        "margin-estimate",
        help="estimate margin average cost and maintenance ratio",
    )
    estimate_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite path (default: {DEFAULT_DATABASE_PATH})",
    )
    target = estimate_parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--stock-code", help="estimate one stock code")
    target.add_argument("--all", action="store_true", help="estimate all stocks")
    estimate_parser.add_argument(
        "--start-date",
        type=_parse_iso_date,
        default=None,
        help="inclusive start date; defaults to the beginning of the latest 30-day window",
    )
    estimate_parser.add_argument(
        "--end-date",
        type=_parse_iso_date,
        default=None,
        help="inclusive end date; defaults to today",
    )
    estimate_parser.add_argument(
        "--margin-ratio",
        type=float,
        default=DEFAULT_MARGIN_FINANCING_RATIO,
        help=(
            "financing ratio used by the estimate model "
            f"(default: {DEFAULT_MARGIN_FINANCING_RATIO})"
        ),
    )
    estimate_parser.add_argument(
        "--model-version",
        default=DEFAULT_MARGIN_MODEL_VERSION,
        help=f"estimate model version (default: {DEFAULT_MARGIN_MODEL_VERSION})",
    )
    estimate_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="logging level (default: INFO)",
    )

    web_parser = subparsers.add_parser(
        "web",
        help="start the Taiwan stock Web query and sync platform",
    )
    web_parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=f"SQLite path (default: {DEFAULT_DATABASE_PATH})",
    )
    web_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind host (default: 127.0.0.1)",
    )
    web_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="bind port (default: 8000)",
    )
    web_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="logging level (default: INFO)",
    )

    desktop_parser = subparsers.add_parser(
        "desktop",
        help="open the Windows/local Tkinter synchronization console",
    )
    desktop_parser.add_argument(
        "--supabase-url",
        default=None,
        help="Supabase project URL (default: SUPABASE_URL or BillDB URL)",
    )
    return parser


def _run_sync(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(message)s",
    )
    client = JsonHttpClient(
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        backoff_seconds=args.backoff_seconds,
        user_agent=DEFAULT_USER_AGENT,
    )
    twse_provider = TWSEStockProvider(client, url=args.twse_url)
    tpex_provider = TPExStockProvider(client, url=args.tpex_url)
    repository = StockRepository(args.db)
    service = StockSyncService(
        twse_provider,
        tpex_provider,
        repository,
        min_expected_twse=args.min_twse,
        min_expected_tpex=args.min_tpex,
    )

    result = service.sync()
    print("Taiwan Stock Master Sync")
    print()
    print(f"TWSE stocks : {result.twse_count}")
    print(f"TPEx stocks : {result.tpex_count}")
    print(f"Total       : {result.total_count}")
    print()
    print(f"Inserted    : {result.inserted_count}")
    print(f"Updated     : {result.updated_count}")
    print()
    print(f"Database    : {args.db}")
    print("Sync completed successfully.")
    return 0


def _run_tdcc_sync(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(message)s",
    )
    client = JsonHttpClient(
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        backoff_seconds=args.backoff_seconds,
        user_agent=DEFAULT_USER_AGENT,
    )
    provider = TDCCDistributionProvider(client, url=args.tdcc_url)
    stock_repository = StockRepository(args.db)
    tdcc_repository = TDCCDistributionRepository(args.db)
    service = TDCCSyncService(provider, stock_repository, tdcc_repository)

    result = service.sync()
    print("TDCC Distribution Sync")
    print()
    print(f"Stocks in master : {result.stocks_count}")
    print(f"TDCC records     : {result.tdcc_count}")
    print()
    print(f"Inserted         : {result.inserted_count}")
    print(f"Updated          : {result.updated_count}")
    print(f"Skipped totals   : {result.skipped_total_count}")
    print()
    print(f"Database         : {args.db}")
    print("TDCC sync completed successfully.")
    return 0


def _run_tdcc_history_sync(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(message)s",
    )

    def make_client() -> TextHttpClient:
        return TextHttpClient(
            timeout=args.timeout,
            max_attempts=args.max_attempts,
            backoff_seconds=args.backoff_seconds,
            user_agent=DEFAULT_USER_AGENT,
        )

    provider = TDCCHistoricalDistributionProvider(
        make_client,
        url=args.tdcc_history_url,
        days=args.days,
        workers=args.workers,
        request_delay_seconds=args.request_delay,
    )
    stock_repository = StockRepository(args.db)
    tdcc_repository = TDCCDistributionRepository(args.db)
    service = TDCCSyncService(provider, stock_repository, tdcc_repository)

    result = service.sync()
    print("TDCC Recent History Sync")
    print()
    print(f"Stocks in master : {result.stocks_count}")
    print(f"Date range       : {provider.start_date} .. {provider.end_date}")
    print(f"Data dates       : {', '.join(provider.last_data_dates)}")
    print(f"TDCC records     : {result.tdcc_count}")
    print(f"Historical calls : {provider.last_request_count}")
    print()
    print(f"Inserted         : {result.inserted_count}")
    print(f"Updated          : {result.updated_count}")
    print(f"Skipped totals   : {result.skipped_total_count}")
    print(f"Skipped adjust   : {provider.last_skipped_adjustment_count}")
    print()
    print(f"Database         : {args.db}")
    print("TDCC recent history sync completed successfully.")
    return 0


def _run_tdcc_supabase_sync(args: argparse.Namespace) -> int:
    load_project_dotenv()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(message)s",
    )
    supabase_url = (
        args.supabase_url
        or os.environ.get("SUPABASE_URL")
        or BILLDB_SUPABASE_URL
    )
    supabase_key = (
        os.environ.get("SUPABASE_SECRET_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    )
    client = None
    if not args.dry_run:
        client = create_supabase_client(supabase_url, supabase_key or "")

    service = SupabaseTDCCSyncService(
        args.db,
        client,
        table_name=args.table,
        batch_size=args.batch_size,
        max_attempts=args.max_attempts,
        backoff_seconds=args.backoff_seconds,
    )
    result = service.sync(year=args.year, dry_run=args.dry_run)

    print("TDCC Supabase Sync")
    print()
    print(f"Year          : {result.year}")
    print(f"SQLite rows   : {result.source_count}")
    print(f"Synced rows   : {result.synced_count}")
    print(f"Skipped rows  : {result.skipped_count}")
    print(f"Batches       : {result.batch_count}")
    print(f"Supabase URL  : {supabase_url}")
    print(f"Table         : {args.table}")
    print(f"Dry run       : {'yes' if result.dry_run else 'no'}")
    print()
    print(
        "Validation completed successfully."
        if result.dry_run
        else "TDCC Supabase sync completed successfully."
    )
    return 0


def _make_margin_service(args: argparse.Namespace) -> MarginSyncService:
    client = JsonHttpClient(
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        backoff_seconds=args.backoff_seconds,
        user_agent=DEFAULT_USER_AGENT,
    )
    twse_provider = TWSEMarginProvider(client, url=args.twse_margin_url)
    tpex_provider = TPExMarginProvider(client, url=args.tpex_margin_url)
    stock_repository = StockRepository(args.db)
    margin_repository = MarginHistoryRepository(args.db)
    return MarginSyncService(
        twse_provider,
        tpex_provider,
        stock_repository,
        margin_repository,
    )


def _run_margin_sync(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(message)s",
    )
    service = _make_margin_service(args)
    result = service.sync(args.date)
    print("Margin Trading Sync")
    print()
    print(f"Trade date       : {result.trade_date or 'no data'}")
    print(f"Stocks in master : {result.stocks_count}")
    print(f"TWSE records     : {result.twse_count}")
    print(f"TPEx records     : {result.tpex_count}")
    print(f"Margin records   : {result.margin_count}")
    print()
    print(f"Inserted         : {result.inserted_count}")
    print(f"Updated          : {result.updated_count}")
    print(f"Skipped non-stock: {result.skipped_non_master_count}")
    print(f"Skipped totals   : {result.skipped_total_count}")
    print()
    print(f"Database         : {args.db}")
    if result.skipped_non_trading:
        print("No official data for this date; nothing was written.")
    else:
        print("Margin sync completed successfully.")
    return 0


def _run_margin_history_sync(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(message)s",
    )
    today = date.today()
    end_date = args.end_date or today
    if args.start_date is None:
        start_date = end_date - timedelta(days=DEFAULT_MARGIN_HISTORY_DAYS - 1)
    else:
        start_date = args.start_date
    service = _make_margin_service(args)
    history_service = MarginHistorySyncService(
        service,
        request_delay_seconds=args.request_delay,
    )
    result = history_service.sync(start_date, end_date)
    print("Margin Trading History Sync")
    print()
    print(f"Date range       : {result.start_date} .. {result.end_date}")
    print(f"Attempted days   : {result.attempted_days}")
    print(f"Synced dates     : {result.synced_count}")
    print(f"Skipped holidays : {result.skipped_non_trading_days}")
    print(f"Margin records   : {result.margin_count}")
    print()
    print(f"Inserted         : {result.inserted_count}")
    print(f"Updated          : {result.updated_count}")
    print(f"Database         : {args.db}")
    print("Margin history sync completed successfully.")
    return 0


def _make_price_service(args: argparse.Namespace) -> PriceSyncService:
    client = JsonHttpClient(
        timeout=args.timeout,
        max_attempts=args.max_attempts,
        backoff_seconds=args.backoff_seconds,
        user_agent=DEFAULT_USER_AGENT,
    )
    twse_provider = TWSEPriceProvider(client, url=args.twse_price_url)
    tpex_provider = TPExPriceProvider(
        client,
        url=args.tpex_price_url,
        request_delay_seconds=args.request_delay,
    )
    stock_repository = StockRepository(args.db)
    price_repository = PriceHistoryRepository(args.db)
    return PriceSyncService(
        twse_provider,
        tpex_provider,
        stock_repository,
        price_repository,
    )


def _run_price_sync(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(message)s",
    )
    service = _make_price_service(args)
    result = service.sync(args.date)
    print("Daily Price Sync")
    print()
    print(f"Trade date       : {result.trade_date or 'no data'}")
    print(f"Stocks in master : {result.stocks_count}")
    print(f"TWSE records     : {result.twse_count}")
    print(f"TPEx records     : {result.tpex_count}")
    print(f"Price records    : {result.price_count}")
    print()
    print(f"Inserted         : {result.inserted_count}")
    print(f"Updated          : {result.updated_count}")
    print(f"Skipped non-stock: {result.skipped_non_master_count}")
    print(f"Skipped totals   : {result.skipped_total_count}")
    print()
    print(f"Database         : {args.db}")
    if result.skipped_non_trading:
        print("No official data for this date; nothing was written.")
    else:
        print("Price sync completed successfully.")
    return 0


def _run_price_history_sync(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(message)s",
    )
    today = date.today()
    end_date = args.end_date or today
    start_date = args.start_date or end_date - timedelta(
        days=DEFAULT_MARGIN_HISTORY_DAYS - 1
    )
    service = _make_price_service(args)
    history_service = PriceHistorySyncService(
        service,
        request_delay_seconds=args.request_delay,
    )
    result = history_service.sync(start_date, end_date)
    print("Daily Price History Sync")
    print()
    print(f"Date range       : {result.start_date} .. {result.end_date}")
    print(f"Attempted days   : {result.attempted_days}")
    print(f"Synced dates     : {result.synced_count}")
    print(f"Skipped holidays : {result.skipped_non_trading_days}")
    print(f"Price records    : {result.price_count}")
    print()
    print(f"Inserted         : {result.inserted_count}")
    print(f"Updated          : {result.updated_count}")
    print(f"Database         : {args.db}")
    print("Price history sync completed successfully.")
    return 0


def _run_margin_estimate(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(message)s",
    )
    today = date.today()
    end_date = args.end_date or today
    start_date = args.start_date or end_date - timedelta(days=29)
    margin_repository = MarginHistoryRepository(args.db)
    price_repository = PriceHistoryRepository(args.db)
    estimate_repository = MarginEstimateRepository(args.db)
    estimator = MarginCostEstimator(
        margin_repository,
        price_repository,
        estimate_repository,
        margin_financing_ratio=args.margin_ratio,
        model_version=args.model_version,
    )
    if args.all:
        estimates = estimator.estimate_all(start_date, end_date)
        target = "all stocks"
    else:
        estimates = estimator.estimate_range(
            args.stock_code,
            start_date,
            end_date,
        )
        target = args.stock_code
    print("Margin Cost and Maintenance Estimate")
    print()
    print(f"Target           : {target}")
    print(f"Date range       : {start_date} .. {end_date}")
    print(f"Model version    : {args.model_version}")
    print(f"Margin ratio     : {args.margin_ratio}")
    print(f"Estimate records : {len(estimates)}")
    print(f"Skipped no close : {len(estimator.last_skipped_close_records)}")
    print(f"Skipped no price : {len(estimator.last_skipped_price_records)}")
    print(f"Skipped no cost  : {len(estimator.last_skipped_cost_records)}")
    print(f"Database         : {args.db}")
    print("Margin estimate completed successfully.")
    return 0


def _run_web(args: argparse.Namespace) -> int:
    """Start the Web server with query pages and an explicit sync action."""

    import uvicorn

    from stock_master.web import create_app

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(message)s",
    )
    uvicorn.run(
        create_app(args.db),
        host=args.host,
        port=args.port,
        log_level=args.log_level.lower(),
    )
    return 0


def _run_desktop(args: argparse.Namespace) -> int:
    """Open the local Tkinter synchronization console."""

    from stock_master.desktop import run_desktop_app

    return run_desktop_app(args.supabase_url)


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and return a process exit code."""

    parser = build_parser()
    original_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(original_argv)
    if args.command is None:
        args = parser.parse_args(["sync", *original_argv])
    if args.command in {
        "sync",
        "tdcc-sync",
        "tdcc-month-sync",
        "tdcc-history-sync",
        "tdcc-supabase-sync",
        "margin-sync",
        "margin-history-sync",
        "price-sync",
        "price-history-sync",
        "margin-estimate",
        "web",
        "desktop",
    }:
        try:
            if args.command == "sync":
                return _run_sync(args)
            if args.command == "tdcc-sync":
                return _run_tdcc_sync(args)
            if args.command in {"tdcc-month-sync", "tdcc-history-sync"}:
                return _run_tdcc_history_sync(args)
            if args.command == "tdcc-supabase-sync":
                return _run_tdcc_supabase_sync(args)
            if args.command == "margin-sync":
                return _run_margin_sync(args)
            if args.command == "margin-history-sync":
                return _run_margin_history_sync(args)
            if args.command == "price-sync":
                return _run_price_sync(args)
            if args.command == "price-history-sync":
                return _run_price_history_sync(args)
            if args.command == "web":
                return _run_web(args)
            if args.command == "desktop":
                return _run_desktop(args)
            return _run_margin_estimate(args)
        except StockMasterError as exc:
            logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
            logger.error("%s", exc)
            return 1
        except (OSError, RuntimeError, ValueError) as exc:
            logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
            logger.error("%s", exc)
            return 1

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
