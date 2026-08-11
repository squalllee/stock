"""Calendar-range orchestration for daily price history backfills."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from stock_master.exceptions import StockDataValidationError
from .price_sync_service import PriceSyncService, _coerce_date

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PriceHistorySyncResult:
    """Aggregated counts for an inclusive calendar-date backfill."""

    start_date: str
    end_date: str
    attempted_days: int
    synced_dates: tuple[str, ...]
    skipped_non_trading_dates: tuple[str, ...]
    price_count: int
    inserted_count: int
    updated_count: int

    @property
    def synced_count(self) -> int:
        return len(self.synced_dates)

    @property
    def skipped_non_trading_days(self) -> int:
        return len(self.skipped_non_trading_dates)

    @property
    def skipped_non_trading_count(self) -> int:
        return self.skipped_non_trading_days

    @property
    def skipped_dates(self) -> tuple[str, ...]:
        return self.skipped_non_trading_dates

    @property
    def records_count(self) -> int:
        return self.price_count


class PriceHistorySyncService:
    """Run one ``PriceSyncService.sync`` transaction per calendar date."""

    def __init__(
        self,
        price_sync_service: PriceSyncService,
        *,
        request_delay_seconds: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if request_delay_seconds < 0:
            raise ValueError("request_delay_seconds cannot be negative")
        self.price_sync_service = price_sync_service
        self.request_delay_seconds = request_delay_seconds
        self._sleep = sleep

    def sync(
        self,
        start_date: date | str,
        end_date: date | str,
    ) -> PriceHistorySyncResult:
        """Backfill an inclusive range, preserving earlier dates on error."""

        start = _coerce_date(start_date, "start_date")
        end = _coerce_date(end_date, "end_date")
        if start is None or end is None:
            raise StockDataValidationError(
                "start_date and end_date are required for price history sync."
            )
        if start > end:
            raise StockDataValidationError("start_date must not be after end_date.")

        current = start
        attempted_days = 0
        synced_dates: list[str] = []
        skipped_dates: list[str] = []
        price_count = 0
        inserted_count = 0
        updated_count = 0
        while current <= end:
            attempted_days += 1
            result = self.price_sync_service.sync(current)
            if result.skipped_non_trading:
                skipped_dates.append(current.isoformat())
            else:
                if result.trade_date is None:
                    raise StockDataValidationError(
                        "Price sync returned no trade date for a non-skipped day."
                    )
                synced_dates.append(result.trade_date)
                price_count += result.price_count
                inserted_count += result.inserted_count
                updated_count += result.updated_count
            if current < end and self.request_delay_seconds:
                self._sleep(self.request_delay_seconds)
            current += timedelta(days=1)

        aggregate = PriceHistorySyncResult(
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            attempted_days=attempted_days,
            synced_dates=tuple(synced_dates),
            skipped_non_trading_dates=tuple(skipped_dates),
            price_count=price_count,
            inserted_count=inserted_count,
            updated_count=updated_count,
        )
        logger.info(
            "Price history sync completed: %s..%s synced_dates=%s "
            "skipped_non_trading=%s records=%s",
            aggregate.start_date,
            aggregate.end_date,
            aggregate.synced_count,
            aggregate.skipped_non_trading_days,
            aggregate.price_count,
        )
        return aggregate

    def sync_range(
        self,
        start_date: date | str,
        end_date: date | str,
    ) -> PriceHistorySyncResult:
        """Named alias for callers that prefer a range-oriented API."""

        return self.sync(start_date, end_date)

