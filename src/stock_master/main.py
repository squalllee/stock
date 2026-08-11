"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from stock_master.config import (
    DEFAULT_DATABASE_PATH,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MIN_EXPECTED_TPEX_STOCKS,
    DEFAULT_MIN_EXPECTED_TWSE_STOCKS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    DEFAULT_TDCC_HISTORY_DAYS,
    DEFAULT_TDCC_HISTORY_REQUEST_DELAY_SECONDS,
    DEFAULT_TDCC_HISTORY_WORKERS,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    TDCC_API_URL,
    TDCC_HISTORY_URL,
    TPEX_API_URL,
    TWSE_API_URL,
)
from stock_master.exceptions import StockMasterError
from stock_master.providers import (
    JsonHttpClient,
    TDCCDistributionProvider,
    TDCCHistoricalDistributionProvider,
    TextHttpClient,
    TPExStockProvider,
    TWSEStockProvider,
)
from stock_master.repositories import StockRepository, TDCCDistributionRepository
from stock_master.services import StockSyncService, TDCCSyncService

logger = logging.getLogger(__name__)


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
    }:
        try:
            if args.command == "sync":
                return _run_sync(args)
            if args.command == "tdcc-sync":
                return _run_tdcc_sync(args)
            return _run_tdcc_history_sync(args)
        except StockMasterError as exc:
            logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
            logger.error("%s", exc)
            return 1
        except (OSError, ValueError) as exc:
            logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
            logger.error("%s", exc)
            return 1

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
