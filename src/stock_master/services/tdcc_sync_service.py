"""Application service for an atomic TDCC distribution synchronization."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from stock_master.exceptions import DatabaseError, StockDataValidationError
from stock_master.models import Stock, TDCCDistribution
from stock_master.providers.tdcc import TDCCDistributionProvider
from stock_master.repositories.stock_repository import StockRepository
from stock_master.repositories.tdcc_repository import TDCCDistributionRepository

logger = logging.getLogger(__name__)

_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TOTAL_LEVEL_MARKERS = ("合計", "總計", "total", "grandtotal")


def _is_total_holding_level(value: str) -> bool:
    normalized = "".join(value.casefold().split())
    return any(marker.casefold() in normalized for marker in _TOTAL_LEVEL_MARKERS)


@dataclass(frozen=True, slots=True)
class TDCCSyncResult:
    """Counts emitted after a successful TDCC synchronization."""

    stocks_count: int
    tdcc_count: int
    inserted_count: int
    updated_count: int
    skipped_total_count: int

    @property
    def stock_count(self) -> int:
        """Compatibility alias for callers using the singular label."""

        return self.stocks_count

    @property
    def records_count(self) -> int:
        """Compatibility alias for the number of persisted TDCC records."""

        return self.tdcc_count

    @property
    def total_count(self) -> int:
        """Compatibility alias matching the stock-sync result naming."""

        return self.tdcc_count


class TDCCSyncService:
    """Use the stock master as the sole universe and write one atomic batch."""

    def __init__(
        self,
        tdcc_provider: TDCCDistributionProvider,
        stock_repository: StockRepository,
        tdcc_repository: TDCCDistributionRepository,
    ) -> None:
        self.tdcc_provider = tdcc_provider
        self.stock_repository = stock_repository
        self.tdcc_repository = tdcc_repository

    def sync(self) -> TDCCSyncResult:
        """Fetch, validate, deduplicate, and persist TDCC records atomically."""

        try:
            stocks = self.stock_repository.get_all()
        except DatabaseError as exc:
            # A brand-new database has no stock master table yet. Treat it as
            # the same safe stop as an empty stock master and give the CLI an
            # actionable message; unrelated database failures still bubble up.
            if "no such table: stocks" not in str(exc).casefold():
                raise
            raise StockDataValidationError(
                "No stocks found in stock master. "
                "Run stock-master sync first."
            ) from exc
        if not stocks:
            raise StockDataValidationError(
                "No stocks found in stock master. "
                "Run stock-master sync first."
            )

        valid_stock_codes = self._stock_codes(stocks)
        # Creating a table is safe before the provider call. No rows are
        # deleted, and an upstream failure cannot reach upsert_many.
        self.tdcc_repository.create_tables()
        records = list(self.tdcc_provider.fetch(valid_stock_codes))
        if not records:
            raise StockDataValidationError(
                "TDCC returned no distribution records; refusing to sync."
            )
        normalized, skipped_totals = self._filter_and_validate(
            records, valid_stock_codes
        )
        deduplicated = self._deduplicate(normalized)
        stats = self.tdcc_repository.upsert_many(deduplicated)

        provider_skipped_totals = getattr(
            self.tdcc_provider, "last_skipped_total_count", 0
        )
        if not isinstance(provider_skipped_totals, int) or provider_skipped_totals < 0:
            provider_skipped_totals = 0

        result = TDCCSyncResult(
            stocks_count=len(stocks),
            tdcc_count=len(deduplicated),
            inserted_count=stats.inserted_count,
            updated_count=stats.updated_count,
            skipped_total_count=provider_skipped_totals + skipped_totals,
        )
        logger.info(
            "TDCC sync completed: stocks=%s records=%s inserted=%s updated=%s totals=%s",
            result.stocks_count,
            result.tdcc_count,
            result.inserted_count,
            result.updated_count,
            result.skipped_total_count,
        )
        return result

    @staticmethod
    def _stock_codes(stocks: Iterable[Stock]) -> set[str]:
        codes: set[str] = set()
        for stock in stocks:
            if not isinstance(stock, Stock):
                raise StockDataValidationError(
                    "Stock master returned a non-Stock value."
                )
            if not isinstance(stock.stock_code, str):
                raise StockDataValidationError(
                    "Stock master contains a non-text stock code."
                )
            code = stock.stock_code.strip()
            if not code:
                raise StockDataValidationError(
                    "Stock master contains an empty stock code."
                )
            codes.add(code)
        if not codes:
            raise StockDataValidationError(
                "No stocks found in stock master. Run stock-master sync first."
            )
        return codes

    @staticmethod
    def _filter_and_validate(
        records: Iterable[TDCCDistribution],
        valid_stock_codes: set[str],
    ) -> tuple[list[TDCCDistribution], int]:
        values: list[TDCCDistribution] = []
        skipped_totals = 0
        for record in records:
            if not isinstance(record, TDCCDistribution):
                raise StockDataValidationError(
                    "TDCC provider returned a non-TDCCDistribution value."
                )
            if not isinstance(record.stock_code, str) or not record.stock_code.strip():
                raise StockDataValidationError("TDCC returned an empty stock_code.")
            if (
                not isinstance(record.holding_level, str)
                or not record.holding_level.strip()
            ):
                raise StockDataValidationError(
                    "TDCC returned an empty holding_level."
                )
            # The provider already applies this filter. Keep it in the service
            # as a boundary check so a provider implementation cannot expand
            # the stock universe accidentally.
            if record.stock_code not in valid_stock_codes:
                continue
            if _is_total_holding_level(record.holding_level):
                skipped_totals += 1
                continue
            TDCCSyncService._validate_record(record)
            values.append(record)
        return values, skipped_totals

    @staticmethod
    def _validate_record(record: TDCCDistribution) -> None:
        if not isinstance(record.data_date, str) or not _ISO_DATE_PATTERN.fullmatch(
            record.data_date
        ):
            raise StockDataValidationError(
                f"TDCC returned invalid normalized date {record.data_date!r}."
            )
        try:
            date.fromisoformat(record.data_date)
        except ValueError as exc:
            raise StockDataValidationError(
                f"TDCC returned invalid normalized date {record.data_date!r}."
            ) from exc
        if not isinstance(record.stock_code, str) or not record.stock_code.strip():
            raise StockDataValidationError("TDCC returned an empty stock_code.")
        if (
            not isinstance(record.holding_level, str)
            or not record.holding_level.strip()
        ):
            raise StockDataValidationError("TDCC returned an empty holding_level.")
        if _is_total_holding_level(record.holding_level):
            raise StockDataValidationError(
                "TDCC provider returned a total record that should have been skipped."
            )
        if (
            isinstance(record.shareholder_count, bool)
            or not isinstance(record.shareholder_count, int)
            or record.shareholder_count < 0
        ):
            raise StockDataValidationError(
                "TDCC shareholder_count must be a non-negative integer."
            )
        if (
            isinstance(record.share_count, bool)
            or not isinstance(record.share_count, int)
            or record.share_count < 0
        ):
            raise StockDataValidationError(
                "TDCC share_count must be a non-negative integer."
            )
        if (
            isinstance(record.holding_ratio, bool)
            or not isinstance(record.holding_ratio, (int, float))
            or not math.isfinite(float(record.holding_ratio))
            or not 0 <= float(record.holding_ratio) <= 100
        ):
            raise StockDataValidationError(
                "TDCC holding_ratio must be a finite percentage from 0 to 100."
            )

    @staticmethod
    def _deduplicate(
        records: list[TDCCDistribution],
    ) -> list[TDCCDistribution]:
        by_key: dict[tuple[str, str, str], TDCCDistribution] = {}
        for record in records:
            key = (record.data_date, record.stock_code, record.holding_level)
            previous = by_key.get(key)
            if previous is None:
                by_key[key] = record
                continue
            if previous != record:
                raise StockDataValidationError(
                    "Conflicting TDCC records for key "
                    f"{key}: {previous!r} vs {record!r}."
                )
        return list(by_key.values())
