"""Application service for one-day margin-trading synchronization."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import TYPE_CHECKING, Callable

from stock_master.exceptions import DatabaseError, StockDataValidationError
from stock_master.models import MarginHistory, Stock
from stock_master.providers.margin_base import MarginProvider, validate_margin_record
from stock_master.repositories.margin_repository import (
    MarginHistoryRepository,
)
from stock_master.repositories.stock_repository import StockRepository

if TYPE_CHECKING:
    from .margin_history_sync_service import MarginHistorySyncResult

logger = logging.getLogger(__name__)

_MARKETS = ("TWSE", "TPEX")
_TOTAL_MARKERS = frozenset({"合計", "總計", "total", "grandtotal"})


@dataclass(frozen=True, slots=True)
class MarginSyncResult:
    """Counts emitted after one successful or skipped date synchronization."""

    trade_date: str | None
    stocks_count: int
    twse_count: int
    tpex_count: int
    margin_count: int
    inserted_count: int
    updated_count: int
    skipped_non_master_count: int = 0
    skipped_total_count: int = 0
    skipped_non_trading: bool = False

    @property
    def stock_count(self) -> int:
        """Compatibility alias for callers using the singular label."""

        return self.stocks_count

    @property
    def records_count(self) -> int:
        """Compatibility alias for the persisted record count."""

        return self.margin_count

    @property
    def total_count(self) -> int:
        """Compatibility alias matching the stock-sync result naming."""

        return self.margin_count

    @property
    def skipped_non_trading_day(self) -> bool:
        """Compatibility alias for callers using the singular label."""

        return self.skipped_non_trading

    @property
    def skipped_non_stock_count(self) -> int:
        """Compatibility alias for CLI/reporting callers."""

        return self.skipped_non_master_count


class MarginSyncService:
    """Fetch both markets, filter by ``stocks``, then commit one date batch."""

    def __init__(
        self,
        twse_provider: MarginProvider,
        tpex_provider: MarginProvider,
        stock_repository: StockRepository,
        margin_repository: MarginHistoryRepository,
    ) -> None:
        self.twse_provider = twse_provider
        self.tpex_provider = tpex_provider
        self.stock_repository = stock_repository
        self.margin_repository = margin_repository

    def sync(self, trade_date: date | str | None = None) -> MarginSyncResult:
        """Synchronize one requested date, or the latest available date.

        The TWSE response is checked first because it has an explicit no-data
        response for weekends and exchange holidays.  When that marker is
        returned, the entire date is skipped and TPEx is not queried.
        """

        requested_date = _coerce_date(trade_date, "trade_date")
        stocks = self._load_stocks()
        codes_by_market = self._stock_codes_by_market(stocks)

        # Creating the table is safe before provider calls: it does not delete
        # rows, and no UPSERT occurs until both market payloads are valid.
        self.margin_repository.create_tables()

        actual_date: str | None = None
        twse_values: list[MarginHistory] = []
        twse_skipped_non_master = 0
        twse_skipped_totals = 0
        if codes_by_market["TWSE"]:
            twse_records = list(self.twse_provider.fetch(requested_date))
            if _provider_no_data(self.twse_provider):
                logger.info(
                    "Margin date %s has no official TWSE data; skipping date",
                    requested_date or "latest",
                )
                return _skipped_result(
                    len(stocks), _provider_skipped_totals(self.twse_provider)
                )
            if not twse_records:
                raise StockDataValidationError(
                    "TWSE margin returned no records without a valid no-data marker; "
                    "refusing to sync."
                )

            actual_date = _provider_date(
                self.twse_provider,
                twse_records,
                requested_date,
                "TWSE",
            )
            twse_values, twse_skipped_non_master, twse_skipped_totals = (
                self._filter_records(
                    twse_records,
                    market="TWSE",
                    valid_codes=codes_by_market["TWSE"],
                    actual_date=actual_date,
                )
            )
        else:
            logger.info("No TWSE stocks in stock master; skipping TWSE request")

        if codes_by_market["TPEX"]:
            tpex_records = list(self.tpex_provider.fetch(requested_date))
            if _provider_no_data(self.tpex_provider):
                if actual_date is None:
                    return _skipped_result(
                        len(stocks), _provider_skipped_totals(self.tpex_provider)
                    )
                raise StockDataValidationError(
                    f"TPEx margin returned no data for trading date {actual_date}; "
                    "the date is incomplete and was not written."
                )
            if not tpex_records:
                raise StockDataValidationError(
                "TPEx margin returned no records without a valid no-data marker; "
                "refusing to sync."
            )
            tpex_date = _provider_date(
                self.tpex_provider,
                tpex_records,
                requested_date,
                "TPEX",
            )
            if actual_date is not None and tpex_date != actual_date:
                raise StockDataValidationError(
                    "TWSE and TPEx margin dates do not match: "
                    f"TWSE={actual_date}, TPEx={tpex_date}."
                )
            actual_date = tpex_date
            tpex_values, tpex_skipped_non_master, tpex_skipped_totals = (
                self._filter_records(
                    tpex_records,
                    market="TPEX",
                    valid_codes=codes_by_market["TPEX"],
                    actual_date=actual_date,
                )
            )
        else:
            logger.info("No TPEX stocks in stock master; skipping TPEx request")
            tpex_values = []
            tpex_skipped_non_master = 0
            tpex_skipped_totals = 0

        if actual_date is None:
            raise StockDataValidationError(
                "Margin sync could not determine a trading date from either market."
            )

        deduplicated = self._deduplicate([*twse_values, *tpex_values])
        if not deduplicated:
            raise StockDataValidationError(
                "Margin providers returned no records belonging to stocks; "
                "refusing to sync."
            )
        stats = self.margin_repository.upsert_many(deduplicated)
        result = MarginSyncResult(
            trade_date=actual_date,
            stocks_count=len(stocks),
            twse_count=len(twse_values),
            tpex_count=len(tpex_values),
            margin_count=len(deduplicated),
            inserted_count=stats.inserted_count,
            updated_count=stats.updated_count,
            skipped_non_master_count=(
                twse_skipped_non_master + tpex_skipped_non_master
            ),
            skipped_total_count=(
                _provider_skipped_totals(self.twse_provider)
                + _provider_skipped_totals(self.tpex_provider)
                + twse_skipped_totals
                + tpex_skipped_totals
            ),
        )
        logger.info(
            "Margin sync completed: date=%s stocks=%s records=%s "
            "inserted=%s updated=%s skipped_non_master=%s",
            result.trade_date,
            result.stocks_count,
            result.margin_count,
            result.inserted_count,
            result.updated_count,
            result.skipped_non_master_count,
        )
        return result

    def sync_range(
        self,
        start_date: date | str,
        end_date: date | str,
        *,
        request_delay_seconds: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> "MarginHistorySyncResult":
        """Convenience wrapper for the separate calendar-range service."""

        from .margin_history_sync_service import MarginHistorySyncService

        return MarginHistorySyncService(
            self,
            request_delay_seconds=request_delay_seconds,
            sleep=sleep,
        ).sync(start_date, end_date)

    def _load_stocks(self) -> list[Stock]:
        try:
            stocks = self.stock_repository.get_all()
        except DatabaseError as exc:
            if "no such table: stocks" not in str(exc).casefold():
                raise
            raise StockDataValidationError(
                "No stocks found in stock master. Run stock-master sync first."
            ) from exc
        if not stocks:
            raise StockDataValidationError(
                "No stocks found in stock master. Run stock-master sync first."
            )
        return stocks

    @staticmethod
    def _stock_codes_by_market(stocks: Iterable[Stock]) -> dict[str, set[str]]:
        codes_by_market = {market: set() for market in _MARKETS}
        for stock in stocks:
            if not isinstance(stock, Stock):
                raise StockDataValidationError(
                    "Stock master returned a non-Stock value."
                )
            if not isinstance(stock.stock_code, str) or not stock.stock_code.strip():
                raise StockDataValidationError(
                    "Stock master contains an empty stock code."
                )
            if stock.market not in codes_by_market:
                raise StockDataValidationError(
                    f"Stock master contains invalid market {stock.market!r}."
                )
            codes_by_market[stock.market].add(stock.stock_code.strip())
        if not any(codes_by_market.values()):
            raise StockDataValidationError(
                "No stocks found in stock master. Run stock-master sync first."
            )
        return codes_by_market

    @staticmethod
    def _filter_records(
        records: Iterable[MarginHistory],
        *,
        market: str,
        valid_codes: set[str],
        actual_date: str,
    ) -> tuple[list[MarginHistory], int, int]:
        values: list[MarginHistory] = []
        skipped_non_master = 0
        skipped_totals = 0
        for record in records:
            if not isinstance(record, MarginHistory):
                raise StockDataValidationError(
                    f"{market} margin provider returned a non-MarginHistory value."
                )
            validate_margin_record(record, market)
            if record.trade_date != actual_date:
                raise StockDataValidationError(
                    f"{market} margin returned mixed trade dates: expected "
                    f"{actual_date}, got {record.trade_date}."
                )
            code = record.stock_code.strip()
            if _is_total_code(code):
                skipped_totals += 1
                continue
            if code not in valid_codes:
                skipped_non_master += 1
                continue
            if code != record.stock_code:
                record = replace(record, stock_code=code)
            values.append(record)
        return values, skipped_non_master, skipped_totals

    @staticmethod
    def _deduplicate(records: Iterable[MarginHistory]) -> list[MarginHistory]:
        by_key: dict[tuple[str, str], MarginHistory] = {}
        for record in records:
            key = (record.trade_date, record.stock_code)
            previous = by_key.get(key)
            if previous is None:
                by_key[key] = record
                continue
            if previous != record:
                raise StockDataValidationError(
                    "Conflicting margin records for key "
                    f"{key}: {previous!r} vs {record!r}."
                )
        return list(by_key.values())


def _coerce_date(value: date | str | None, field: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise StockDataValidationError(
                f"{field} must be an ISO date in YYYY-MM-DD format."
            ) from exc
    raise StockDataValidationError(
        f"{field} must be a date, ISO date string, or None."
    )


def _provider_date(
    provider: MarginProvider,
    records: list[MarginHistory],
    requested_date: date | None,
    market: str,
) -> str:
    provider_date = getattr(provider, "last_trade_date", None)
    if provider_date is not None:
        if not isinstance(provider_date, str):
            raise StockDataValidationError(
                f"{market} margin provider returned a non-text trade date."
            )
        actual_date = provider_date
    else:
        dates = {record.trade_date for record in records}
        if len(dates) != 1:
            raise StockDataValidationError(
                f"{market} margin provider returned mixed or missing trade dates."
            )
        actual_date = dates.pop()
    try:
        normalized = date.fromisoformat(actual_date).isoformat()
    except (TypeError, ValueError) as exc:
        raise StockDataValidationError(
            f"{market} margin provider returned invalid trade date {actual_date!r}."
        ) from exc
    if requested_date is not None and normalized != requested_date.isoformat():
        raise StockDataValidationError(
            f"{market} margin returned unexpected trade date: expected "
            f"{requested_date.isoformat()}, got {normalized}."
        )
    return normalized


def _provider_no_data(provider: MarginProvider) -> bool:
    value = getattr(provider, "last_no_data", False)
    return isinstance(value, bool) and value


def _skipped_result(stocks_count: int, skipped_total_count: int) -> MarginSyncResult:
    return MarginSyncResult(
        trade_date=None,
        stocks_count=stocks_count,
        twse_count=0,
        tpex_count=0,
        margin_count=0,
        inserted_count=0,
        updated_count=0,
        skipped_total_count=skipped_total_count,
        skipped_non_trading=True,
    )


def _provider_skipped_totals(provider: MarginProvider) -> int:
    value = getattr(provider, "last_skipped_total_count", 0)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _is_total_code(value: str) -> bool:
    return "".join(value.casefold().split()) in {
        marker.casefold() for marker in _TOTAL_MARKERS
    }
