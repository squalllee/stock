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
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    TPEX_API_URL,
    TWSE_API_URL,
)
from stock_master.exceptions import StockMasterError
from stock_master.providers import JsonHttpClient, TPExStockProvider, TWSEStockProvider
from stock_master.repositories import StockRepository
from stock_master.services import StockSyncService

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock-master",
        description="Synchronize TWSE and TPEx common stocks into SQLite.",
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


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and return a process exit code."""

    parser = build_parser()
    original_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(original_argv)
    if args.command is None:
        args = parser.parse_args(["sync", *original_argv])
    if args.command == "sync":
        try:
            return _run_sync(args)
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

