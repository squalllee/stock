"""Synchronize official stock data directly into Supabase BillDB.

This module deliberately does not import or open SQLite repositories.  The
Supabase ``stocks`` table is the universe used by the price and TDCC jobs, so
the desktop application can run on a machine that has no local data database.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from stock_master.config import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_MIN_EXPECTED_TPEX_STOCKS,
    DEFAULT_MIN_EXPECTED_TWSE_STOCKS,
    DEFAULT_PRICE_HISTORY_REQUEST_DELAY_SECONDS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    DEFAULT_SUPABASE_TDCC_BATCH_SIZE,
    DEFAULT_TDCC_HISTORY_DEGRADED_DELAY_SECONDS,
    DEFAULT_TDCC_HISTORY_RECOVERY_BATCHES,
    DEFAULT_TDCC_HISTORY_REQUEST_DELAY_SECONDS,
    DEFAULT_TDCC_HISTORY_STOCK_BATCH_SIZE,
    DEFAULT_TDCC_HISTORY_WORKERS,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    TDCC_API_URL,
    TDCC_HISTORY_URL,
    TPEX_API_URL,
    TPEX_PRICE_URL,
    TWSE_API_URL,
    TWSE_PRICE_URL,
)
from stock_master.exceptions import (
    StockDataValidationError,
    StockProviderError,
    SupabaseSyncError,
)
from stock_master.models import (
    InsiderTransaction,
    PriceHistory,
    Stock,
    TDCCDistribution,
)
from stock_master.providers import (
    JsonHttpClient,
    TDCCHistoricalDistributionProvider,
    TDCCHistoricalQueryResult,
    TDCCDistributionProvider,
    InsiderTransferProvider,
    InsiderUntransferredProvider,
    TextHttpClient,
    TPExPriceProvider,
    TPExStockProvider,
    TWSEPriceProvider,
    TWSEStockProvider,
)
from stock_master.services.normalizer import is_valid_stock_code
from stock_master.services.price_history_sync_service import PriceHistorySyncService
from stock_master.services.price_sync_service import PriceSyncService
from stock_master.services.stock_sync_service import StockSyncService
from stock_master.services.tdcc_sync_service import TDCCSyncResult, TDCCSyncService

logger = logging.getLogger(__name__)

_PAGE_SIZE = 1000
_MARKETS = frozenset({"TWSE", "TPEX"})
_RETRYABLE_TDCC_HISTORY_FAILURES = (
    "session initialization failed",
    "missing form session fields",
    "no available data dates",
)


def _is_retryable_tdcc_history_failure(exc: Exception) -> bool:
    if isinstance(exc, StockProviderError):
        return True
    message = str(exc).casefold()
    return any(marker in message for marker in _RETRYABLE_TDCC_HISTORY_FAILURES)


@dataclass(frozen=True, slots=True)
class SupabaseUpsertStats:
    """Counts returned by one or more Supabase upsert requests."""

    inserted_count: int
    updated_count: int
    synced_count: int
    batch_count: int


class _SupabaseBatchWriter:
    """Small retrying batch writer shared by all direct-sync adapters."""

    def __init__(
        self,
        client: Any,
        *,
        batch_size: int = DEFAULT_SUPABASE_TDCC_BATCH_SIZE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000.")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one.")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative.")
        self.client = client
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep

    def upsert(
        self,
        table_name: str,
        values: Iterable[dict[str, object]],
        *,
        on_conflict: str,
    ) -> SupabaseUpsertStats:
        rows = list(values)
        if not rows:
            return SupabaseUpsertStats(0, 0, 0, 0)

        batch_count = 0
        for start in range(0, len(rows), self.batch_size):
            batch_count += 1
            batch = rows[start : start + self.batch_size]
            self._upsert_batch(table_name, batch, on_conflict, batch_count)

        return SupabaseUpsertStats(
            inserted_count=0,
            # Supabase's Data API does not report insert/update counts for a
            # minimal upsert response.  Keep the service contract useful by
            # reporting all accepted rows as synchronized rows.
            updated_count=len(rows),
            synced_count=len(rows),
            batch_count=batch_count,
        )

    def _upsert_batch(
        self,
        table_name: str,
        values: list[dict[str, object]],
        on_conflict: str,
        batch_number: int,
    ) -> None:
        for attempt in range(1, self.max_attempts + 1):
            try:
                (
                    self.client.table(table_name)
                    .upsert(
                        values,
                        on_conflict=on_conflict,
                        returning="minimal",
                        default_to_null=False,
                    )
                    .execute()
                )
                logger.info(
                    "Supabase progress: table=%s batch=%s rows=%s",
                    table_name,
                    batch_number,
                    len(values),
                )
                return
            except Exception as exc:  # noqa: BLE001 - client errors are user-facing
                if attempt >= self.max_attempts:
                    raise SupabaseSyncError(
                        f"Supabase {table_name} upsert failed for batch "
                        f"{batch_number} after {attempt} attempt(s): {exc}"
                    ) from exc
                delay = self.backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "Supabase table %s batch %s failed on attempt %s/%s; "
                    "retrying in %.1f seconds: %s",
                    table_name,
                    batch_number,
                    attempt,
                    self.max_attempts,
                    delay,
                    exc,
                )
                if delay:
                    self._sleep(delay)


class _SupabaseStockUniverse:
    """Read stock rows from Supabase in Data API-sized pages."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def get_all(self) -> list[Stock]:
        values: list[Stock] = []
        offset = 0
        while True:
            try:
                response = (
                    self.client.table("stocks")
                    .select("stock_code,stock_name,market")
                    .range(offset, offset + _PAGE_SIZE - 1)
                    .execute()
                )
            except Exception as exc:  # noqa: BLE001 - normalize client errors
                raise SupabaseSyncError(
                    f"Could not read Supabase stocks table: {exc}"
                ) from exc

            rows = getattr(response, "data", None)
            if rows is None:
                raise SupabaseSyncError(
                    "Supabase stocks query returned no data payload."
                )
            if not rows:
                break
            for index, row in enumerate(rows):
                values.append(self._normalize_row(row, offset + index))
            if len(rows) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE

        if not values:
            raise StockDataValidationError(
                "Supabase stocks table is empty. Run stock master sync first."
            )
        return values

    @staticmethod
    def _normalize_row(row: object, index: int) -> Stock:
        if not isinstance(row, Mapping):
            raise StockDataValidationError(
                f"Supabase stocks row {index} is not an object."
            )
        code = str(row.get("stock_code", "")).strip()
        name = str(row.get("stock_name", "")).strip()
        market = str(row.get("market", "")).strip().upper()
        if not is_valid_stock_code(code) or not name or market not in _MARKETS:
            raise StockDataValidationError(
                f"Supabase stocks row {index} contains invalid stock data."
            )
        return Stock(stock_code=code, stock_name=name, market=market)


class _SupabaseStockRepository:
    """Repository adapter used by the existing stock validation service."""

    def __init__(self, writer: _SupabaseBatchWriter) -> None:
        self.writer = writer

    def create_tables(self) -> None:
        """Supabase tables are created by the checked-in SQL schema."""

    def upsert_many(self, stocks: Iterable[Stock]) -> SupabaseUpsertStats:
        synced_at = _utc_now()
        values = [
            {
                "stock_code": stock.stock_code,
                "stock_name": stock.stock_name,
                "market": stock.market,
                "updated_at": synced_at,
            }
            for stock in stocks
        ]
        return self.writer.upsert("stocks", values, on_conflict="stock_code")


class _SupabasePriceRepository:
    """Repository adapter that writes normalized price models to Supabase."""

    def __init__(self, writer: _SupabaseBatchWriter) -> None:
        self.writer = writer

    def create_tables(self) -> None:
        """Supabase tables are created by the checked-in SQL schema."""

    def upsert_many(self, prices: Iterable[PriceHistory]) -> SupabaseUpsertStats:
        synced_at = _utc_now()
        values = [
            {
                "trade_date": price.trade_date,
                "stock_code": price.stock_code,
                "market": price.market,
                "trade_volume": price.trade_volume,
                "trade_value": price.trade_value,
                "open_price": price.open_price,
                "high_price": price.high_price,
                "low_price": price.low_price,
                "close_price": price.close_price,
                "transaction_count": price.transaction_count,
                "market_average_price": price.market_average_price,
                "updated_at": synced_at,
            }
            for price in prices
        ]
        return self.writer.upsert(
            "price_history",
            values,
            on_conflict="trade_date,stock_code",
        )


class _SupabaseTDCCRepository:
    """Repository adapter that writes normalized TDCC models to Supabase."""

    def __init__(self, writer: _SupabaseBatchWriter) -> None:
        self.writer = writer

    def create_tables(self) -> None:
        """Supabase tables are created by the checked-in SQL schema."""

    def get_latest_data_date(self) -> str | None:
        """Return the newest TDCC date already stored in Supabase."""

        try:
            response = (
                self.writer.client.table("tdcc_distributions")
                .select("data_date")
                .order("data_date", desc=True)
                .limit(1)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 - normalize client errors
            raise SupabaseSyncError(
                f"Could not read latest TDCC data date from Supabase: {exc}"
            ) from exc

        rows = getattr(response, "data", None)
        if rows is None:
            raise SupabaseSyncError(
                "Supabase TDCC query returned no data payload."
            )
        if not rows:
            return None
        row = rows[0]
        if not isinstance(row, Mapping):
            raise SupabaseSyncError(
                "Supabase TDCC latest-date query returned an invalid row."
            )
        value = str(row.get("data_date", "")).strip()
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as exc:
            raise SupabaseSyncError(
                f"Supabase TDCC returned an invalid data date {value!r}."
            ) from exc

    def upsert_many(
        self, distributions: Iterable[TDCCDistribution]
    ) -> SupabaseUpsertStats:
        synced_at = _utc_now()
        all_distributions = list(distributions)
        numeric_distributions = [
            distribution
            for distribution in all_distributions
            if str(distribution.holding_level).strip().isdigit()
        ]
        values = [
            {
                "data_date": distribution.data_date,
                "stock_code": distribution.stock_code,
                "holding_level": int(distribution.holding_level),
                "shareholder_count": distribution.shareholder_count,
                "share_count": distribution.share_count,
                "holding_ratio": distribution.holding_ratio,
                "updated_at": synced_at,
            }
            for distribution in numeric_distributions
        ]
        skipped_count = len(all_distributions) - len(numeric_distributions)
        if skipped_count:
            logger.warning(
                "Skipped %s non-numeric TDCC holding levels for Supabase",
                skipped_count,
            )
        return self.writer.upsert(
            "tdcc_distributions",
            values,
            on_conflict="data_date,stock_code,holding_level",
        )


class _SupabaseInsiderRepository:
    """Repository adapter for normalized insider disclosure rows."""

    TABLE_NAME = "insider_transactions"

    def __init__(self, writer: _SupabaseBatchWriter) -> None:
        self.writer = writer

    def upsert_many(
        self, transactions: Iterable[InsiderTransaction]
    ) -> SupabaseUpsertStats:
        synced_at = _utc_now()
        values = [
            {
                "report_date": transaction.report_date,
                "stock_code": transaction.stock_code,
                "market": transaction.market,
                "report_type": transaction.report_type,
                "transaction_type": transaction.transaction_type,
                "insider_name": transaction.insider_name,
                "insider_role": transaction.insider_role,
                "shares_changed": transaction.shares_changed,
                "source": transaction.source,
                "source_record_key": transaction.source_record_key,
                "transfer_method": transaction.transfer_method,
                "transferee": transaction.transferee,
                "current_shares": transaction.current_shares,
                "planned_shares": transaction.planned_shares,
                "after_shares": transaction.after_shares,
                "effective_period": transaction.effective_period,
                "reason": transaction.reason,
                "raw_data": dict(transaction.raw_data),
                "updated_at": synced_at,
            }
            for transaction in transactions
        ]
        return self.writer.upsert(
            self.TABLE_NAME,
            values,
            on_conflict="source,source_record_key",
        )


class _SupabaseTDCCCheckpointRepository:
    """Persist completed historical stock/date queries for safe resume."""

    TABLE_NAME = "tdcc_sync_checkpoints"

    def __init__(self, writer: _SupabaseBatchWriter) -> None:
        self.writer = writer

    def get_completed(
        self,
        start_date: str,
        end_date: str,
    ) -> set[tuple[str, str]]:
        completed: set[tuple[str, str]] = set()
        offset = 0
        while True:
            try:
                response = (
                    self.writer.client.table(self.TABLE_NAME)
                    .select("data_date,stock_code,status,record_count")
                    .gte("data_date", start_date)
                    .lte("data_date", end_date)
                    .range(offset, offset + _PAGE_SIZE - 1)
                    .execute()
                )
            except Exception as exc:  # noqa: BLE001 - normalize client errors
                raise SupabaseSyncError(
                    "Could not read Supabase TDCC checkpoints. Apply "
                    "supabase/schema/tdcc_sync_checkpoints.sql first: "
                    f"{exc}"
                ) from exc

            rows = getattr(response, "data", None)
            if rows is None:
                raise SupabaseSyncError(
                    "Supabase TDCC checkpoint query returned no data payload."
                )
            if not rows:
                break
            for index, row in enumerate(rows):
                if not isinstance(row, Mapping):
                    raise SupabaseSyncError(
                        "Supabase TDCC checkpoint query returned an invalid row "
                        f"at offset {offset + index}."
                    )
                data_date = str(row.get("data_date", "")).strip()
                stock_code = str(row.get("stock_code", "")).strip()
                status = str(row.get("status", "")).strip()
                try:
                    date.fromisoformat(data_date)
                except ValueError as exc:
                    raise SupabaseSyncError(
                        "Supabase TDCC checkpoint contains invalid data date "
                        f"{data_date!r}."
                    ) from exc
                if not stock_code or status not in {"completed", "no_data"}:
                    raise SupabaseSyncError(
                        "Supabase TDCC checkpoint contains invalid stock/status data."
                    )
                completed.add((data_date, stock_code))
            if len(rows) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE
        return completed

    def upsert_many(
        self,
        query_results: Iterable[TDCCHistoricalQueryResult],
    ) -> SupabaseUpsertStats:
        synced_at = _utc_now()
        values = [
            {
                "data_date": result.data_date,
                "stock_code": result.stock_code,
                "status": result.status,
                "record_count": result.record_count,
                "updated_at": synced_at,
            }
            for result in query_results
        ]
        return self.writer.upsert(
            self.TABLE_NAME,
            values,
            on_conflict="data_date,stock_code",
        )


class _PrefetchedTDCCProvider:
    """Expose one validated bulk fetch to the existing TDCC sync service."""

    def __init__(
        self,
        records: Iterable[TDCCDistribution],
        *,
        skipped_total_count: int,
    ) -> None:
        self.records = tuple(records)
        self.last_skipped_total_count = skipped_total_count

    def fetch(self, _stock_codes: set[str]) -> list[TDCCDistribution]:
        return list(self.records)


class SupabaseMarketSyncService:
    """Run stock, price, and TDCC workflows without touching SQLite."""

    def __init__(
        self,
        supabase_client: Any,
        *,
        batch_size: int = DEFAULT_SUPABASE_TDCC_BATCH_SIZE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        tdcc_history_stock_batch_size: int = DEFAULT_TDCC_HISTORY_STOCK_BATCH_SIZE,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if tdcc_history_stock_batch_size < 1:
            raise ValueError("tdcc_history_stock_batch_size must be at least one.")
        self.writer = _SupabaseBatchWriter(
            supabase_client,
            batch_size=batch_size,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            sleep=sleep,
        )
        self.universe = _SupabaseStockUniverse(supabase_client)
        self.tdcc_history_stock_batch_size = tdcc_history_stock_batch_size

    def sync_stock_master(self) -> Any:
        """Fetch and upsert the official TWSE/TPEx common-stock master."""

        client = _json_client()
        service = StockSyncService(
            TWSEStockProvider(client, url=TWSE_API_URL),
            TPExStockProvider(client, url=TPEX_API_URL),
            _SupabaseStockRepository(self.writer),
            min_expected_twse=DEFAULT_MIN_EXPECTED_TWSE_STOCKS,
            min_expected_tpex=DEFAULT_MIN_EXPECTED_TPEX_STOCKS,
        )
        return service.sync()

    def sync_daily_prices(
        self,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
    ) -> Any:
        """Fetch and upsert latest prices or an inclusive calendar range.

        With no dates, the providers use their latest-available endpoints. If
        one date is supplied, it is treated as a one-day range.
        """

        self.universe.get_all()
        client = _json_client()
        service = PriceSyncService(
            TWSEPriceProvider(client, url=TWSE_PRICE_URL),
            TPExPriceProvider(
                client,
                url=TPEX_PRICE_URL,
                request_delay_seconds=DEFAULT_PRICE_HISTORY_REQUEST_DELAY_SECONDS,
            ),
            self.universe,
            _SupabasePriceRepository(self.writer),
        )
        # The first query gives the user an actionable empty-table error
        # before making official market requests. The service reads the
        # universe again to keep its normal validation path.
        if start_date is None and end_date is None:
            return service.sync()

        if start_date is None:
            start_date = end_date
        if end_date is None:
            end_date = start_date
        return PriceHistorySyncService(
            service,
            request_delay_seconds=DEFAULT_PRICE_HISTORY_REQUEST_DELAY_SECONDS,
        ).sync(start_date, end_date)

    def sync_insider_transactions(self) -> dict[str, object]:
        """Fetch the current TWSE/TPEx insider disclosure feeds.

        The feeds are published for the whole market.  We still read the
        Supabase stock master first and filter by those codes before writing,
        so delisted/unsupported instruments never enter the mobile-web data
        set.  Both planned transfers and subsequent non-transfer notices are
        retained with an explicit ``report_type``.
        """

        stocks = self.universe.get_all()
        stock_codes = {stock.stock_code for stock in stocks}
        client = _json_client()
        providers = (
            InsiderTransferProvider(client, market="TWSE"),
            InsiderUntransferredProvider(client, market="TWSE"),
            InsiderTransferProvider(client, market="TPEX"),
            InsiderUntransferredProvider(client, market="TPEX"),
        )
        transactions: list[InsiderTransaction] = []
        latest_dates: list[str] = []
        source_counts: dict[str, int] = {}
        for provider in providers:
            rows = provider.fetch(stock_codes)
            transactions.extend(rows)
            if provider.last_report_date:
                latest_dates.append(provider.last_report_date)
            source_counts[provider.market] = source_counts.get(provider.market, 0) + len(rows)

        if not transactions:
            logger.info("Official insider feeds contain no rows for Supabase stocks")
            return {
                "skipped": True,
                "reason": "官方目前沒有符合股票主檔的內部人申報資料",
                "latest_data_date": max(latest_dates) if latest_dates else None,
                "record_count": 0,
                "market_counts": source_counts,
            }

        stats = _SupabaseInsiderRepository(self.writer).upsert_many(transactions)
        report_type_counts: dict[str, int] = {}
        for transaction in transactions:
            report_type_counts[transaction.report_type] = (
                report_type_counts.get(transaction.report_type, 0) + 1
            )
        latest_date = max(transaction.report_date for transaction in transactions)
        return {
            "record_count": len(transactions),
            "latest_data_date": max(latest_dates + [latest_date]),
            "market_counts": source_counts,
            "report_type_counts": report_type_counts,
            "inserted_count": stats.inserted_count,
            "updated_count": stats.updated_count,
            "synced_count": stats.synced_count,
            "batch_count": stats.batch_count,
        }

    def sync_tdcc_latest(self) -> Any:
        """Fetch and upsert the latest TDCC levels 1-15 once per data date."""

        client = _json_client()
        stocks = self.universe.get_all()
        stock_codes = {stock.stock_code for stock in stocks}
        provider = TDCCDistributionProvider(client, url=TDCC_API_URL)
        records = list(provider.fetch(stock_codes))
        if not records:
            raise StockDataValidationError(
                "TDCC returned no distribution records; refusing to sync."
            )

        source_data_date = max(record.data_date for record in records)
        repository = _SupabaseTDCCRepository(self.writer)
        stored_data_date = repository.get_latest_data_date()
        if stored_data_date is not None and source_data_date <= stored_data_date:
            logger.info(
                "TDCC latest data date %s is already stored; skipping sync",
                source_data_date,
            )
            return {
                "skipped": True,
                "reason": "TDCC 最新一期資料已同步",
                "data_date": source_data_date,
            }

        service = TDCCSyncService(
            _PrefetchedTDCCProvider(
                records,
                skipped_total_count=provider.last_skipped_total_count,
            ),
            self.universe,
            repository,
        )
        return service.sync()

    def sync_tdcc_year(self, year: int) -> dict[str, object]:
        """Incrementally fetch and checkpoint all official TDCC weeks in a year."""

        today = date.today()
        if year < 2000 or year > today.year:
            raise ValueError(f"TDCC 年度必須介於 2000 到 {today.year} 之間")
        end_date = min(date(year, 12, 31), today)
        start_date = date(year, 1, 1)
        days = max(1, (end_date - start_date).days)
        stocks = self.universe.get_all()
        valid_stock_codes = {stock.stock_code for stock in stocks}
        distribution_repository = _SupabaseTDCCRepository(self.writer)
        checkpoint_repository = _SupabaseTDCCCheckpointRepository(self.writer)

        catalog_provider = self._tdcc_history_provider(
            days=days,
            end_date=end_date,
            workers=1,
            request_delay_seconds=DEFAULT_TDCC_HISTORY_REQUEST_DELAY_SECONDS,
        )
        data_dates = catalog_provider.available_dates()
        completed = checkpoint_repository.get_completed(
            start_date.isoformat(), end_date.isoformat()
        )
        scoped_completed = {
            pair
            for pair in completed
            if pair[0] in data_dates and pair[1] in valid_stock_codes
        }
        total_query_count = len(data_dates) * len(valid_stock_codes)
        initial_checkpoint_count = len(scoped_completed)
        pending_stock_codes = [
            stock_code
            for stock_code in sorted(valid_stock_codes)
            if any(
                (data_date, stock_code) not in scoped_completed
                for data_date in data_dates
            )
        ]

        current_workers = DEFAULT_TDCC_HISTORY_WORKERS
        request_delay = DEFAULT_TDCC_HISTORY_REQUEST_DELAY_SECONDS
        stable_batches = 0
        request_count = 0
        completed_query_count = 0
        tdcc_count = 0
        updated_count = 0
        supabase_batch_count = 0
        skipped_total_count = 0
        stock_batch_count = 0

        for batch_start in range(
            0,
            len(pending_stock_codes),
            self.tdcc_history_stock_batch_size,
        ):
            stock_batch_count += 1
            batch_codes = pending_stock_codes[
                batch_start : batch_start + self.tdcc_history_stock_batch_size
            ]
            provider = self._tdcc_history_provider(
                days=days,
                end_date=end_date,
                workers=current_workers,
                request_delay_seconds=request_delay,
            )
            try:
                records = provider.fetch(
                    set(batch_codes),
                    completed_queries=scoped_completed,
                    selected_dates=data_dates,
                )
            except (StockProviderError, StockDataValidationError) as exc:
                if (
                    current_workers <= 1
                    or not _is_retryable_tdcc_history_failure(exc)
                ):
                    raise
                logger.warning(
                    "TDCC historical batch %s failed with %s workers; "
                    "retrying with one worker and %.1f second delay: %s",
                    stock_batch_count,
                    current_workers,
                    DEFAULT_TDCC_HISTORY_DEGRADED_DELAY_SECONDS,
                    exc,
                )
                current_workers = 1
                request_delay = DEFAULT_TDCC_HISTORY_DEGRADED_DELAY_SECONDS
                stable_batches = 0
                provider = self._tdcc_history_provider(
                    days=days,
                    end_date=end_date,
                    workers=current_workers,
                    request_delay_seconds=request_delay,
                )
                records = provider.fetch(
                    set(batch_codes),
                    completed_queries=scoped_completed,
                    selected_dates=data_dates,
                )

            prepared_records, batch_skipped_totals = TDCCSyncService.prepare_records(
                records,
                valid_stock_codes,
            )
            persisted_counts: dict[tuple[str, str], int] = {}
            for record in prepared_records:
                pair = (record.data_date, record.stock_code)
                persisted_counts[pair] = persisted_counts.get(pair, 0) + 1
            checkpoint_results: list[TDCCHistoricalQueryResult] = []
            for query_result in provider.last_query_results:
                pair = (query_result.data_date, query_result.stock_code)
                persisted_count = persisted_counts.get(pair, 0)
                if query_result.record_count and persisted_count == 0:
                    raise StockDataValidationError(
                        "TDCC historical query returned records but none were "
                        f"eligible for persistence: {pair}."
                    )
                checkpoint_results.append(
                    TDCCHistoricalQueryResult(
                        data_date=query_result.data_date,
                        stock_code=query_result.stock_code,
                        record_count=persisted_count,
                    )
                )
            distribution_stats = distribution_repository.upsert_many(
                prepared_records
            )
            # Checkpoints are written only after all distribution rows in this
            # stock batch are durable. If this write fails, a rerun safely
            # upserts the same distribution keys before checkpointing again.
            checkpoint_stats = checkpoint_repository.upsert_many(
                checkpoint_results
            )
            scoped_completed.update(
                (result.data_date, result.stock_code)
                for result in checkpoint_results
            )

            request_count += provider.last_request_count
            completed_query_count += len(provider.last_query_results)
            tdcc_count += len(prepared_records)
            updated_count += distribution_stats.updated_count
            supabase_batch_count += (
                distribution_stats.batch_count + checkpoint_stats.batch_count
            )
            skipped_total_count += (
                provider.last_skipped_total_count + batch_skipped_totals
            )
            logger.info(
                "TDCC annual sync progress: stock batch=%s stocks=%s queries=%s "
                "records=%s checkpointed=%s/%s workers=%s",
                stock_batch_count,
                len(batch_codes),
                len(provider.last_query_results),
                len(prepared_records),
                len(scoped_completed),
                total_query_count,
                current_workers,
            )

            stable_batches += 1
            if (
                current_workers == 1
                and stable_batches >= DEFAULT_TDCC_HISTORY_RECOVERY_BATCHES
            ):
                current_workers = DEFAULT_TDCC_HISTORY_WORKERS
                request_delay = DEFAULT_TDCC_HISTORY_REQUEST_DELAY_SECONDS
                stable_batches = 0
                logger.info(
                    "TDCC historical source stabilized; restoring %s workers",
                    current_workers,
                )

        result = TDCCSyncResult(
            stocks_count=len(stocks),
            tdcc_count=tdcc_count,
            inserted_count=0,
            updated_count=updated_count,
            skipped_total_count=skipped_total_count,
        )
        return {
            "year": year,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "data_dates": list(data_dates),
            "request_count": request_count,
            "total_query_count": total_query_count,
            "skipped_checkpoint_count": initial_checkpoint_count,
            "completed_query_count": completed_query_count,
            "remaining_query_count": total_query_count - len(scoped_completed),
            "stock_batch_count": stock_batch_count,
            "supabase_batch_count": supabase_batch_count,
            "result": result,
        }

    @staticmethod
    def _tdcc_history_provider(
        *,
        days: int,
        end_date: date,
        workers: int,
        request_delay_seconds: float,
    ) -> TDCCHistoricalDistributionProvider:
        return TDCCHistoricalDistributionProvider(
            _text_client,
            url=TDCC_HISTORY_URL,
            days=days,
            end_date=end_date,
            workers=workers,
            request_delay_seconds=request_delay_seconds,
            newest_first=True,
        )


def _json_client() -> JsonHttpClient:
    return JsonHttpClient(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
        user_agent=DEFAULT_USER_AGENT,
    )


def _text_client() -> TextHttpClient:
    return TextHttpClient(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
        user_agent=DEFAULT_USER_AGENT,
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
