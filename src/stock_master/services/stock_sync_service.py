"""Orchestration service for an atomic two-market synchronization."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from stock_master.config import (
    DEFAULT_MIN_EXPECTED_TPEX_STOCKS,
    DEFAULT_MIN_EXPECTED_TWSE_STOCKS,
)
from stock_master.exceptions import StockDataValidationError
from stock_master.models import Stock
from stock_master.providers.base import StockProvider
from stock_master.repositories.stock_repository import (
    StockRepository,
)
from .normalizer import is_valid_stock_code

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SyncResult:
    """Counts emitted after a successful synchronization."""

    twse_count: int
    tpex_count: int
    total_count: int
    inserted_count: int
    updated_count: int


class StockSyncService:
    """Fetch both markets, validate them, then commit one atomic batch."""

    def __init__(
        self,
        twse_provider: StockProvider,
        tpex_provider: StockProvider,
        repository: StockRepository,
        *,
        min_expected_twse: int = DEFAULT_MIN_EXPECTED_TWSE_STOCKS,
        min_expected_tpex: int = DEFAULT_MIN_EXPECTED_TPEX_STOCKS,
    ) -> None:
        if min_expected_twse < 0 or min_expected_tpex < 0:
            raise ValueError("minimum expected counts cannot be negative")

        self.twse_provider = twse_provider
        self.tpex_provider = tpex_provider
        self.repository = repository
        self.min_expected_twse = min_expected_twse
        self.min_expected_tpex = min_expected_tpex

    def sync(self) -> SyncResult:
        """Synchronize both providers without partial writes."""

        # Creating tables is safe before a fetch: it never drops or deletes
        # existing rows, and a provider failure will not reach upsert_many.
        self.repository.create_tables()

        twse_stocks = self._validate_provider_result(
            self.twse_provider.fetch(), "TWSE", self.min_expected_twse
        )
        tpex_stocks = self._validate_provider_result(
            self.tpex_provider.fetch(), "TPEX", self.min_expected_tpex
        )

        merged = self._deduplicate([*twse_stocks, *tpex_stocks])
        stats = self.repository.upsert_many(merged)

        result = SyncResult(
            twse_count=len(twse_stocks),
            tpex_count=len(tpex_stocks),
            total_count=len(merged),
            inserted_count=stats.inserted_count,
            updated_count=stats.updated_count,
        )
        logger.info("Total valid stocks: %s", result.total_count)
        logger.info("Database sync completed")
        return result

    @staticmethod
    def _validate_provider_result(
        stocks: Iterable[Stock],
        market: str,
        minimum: int,
    ) -> list[Stock]:
        values = list(stocks)
        if not values:
            raise StockDataValidationError(
                f"{market} returned no stocks; refusing to sync."
            )
        if len(values) < minimum:
            raise StockDataValidationError(
                f"{market} returned {len(values)} stocks, below the configured "
                f"minimum of {minimum}; refusing to sync."
            )

        normalized_values: list[Stock] = []
        for stock in values:
            if not isinstance(stock, Stock):
                raise StockDataValidationError(
                    f"{market} provider returned a non-Stock value."
                )
            if not isinstance(stock.stock_code, str) or not isinstance(
                stock.stock_name, str
            ):
                raise StockDataValidationError(
                    f"{market} provider returned a stock with non-text fields."
                )
            code = stock.stock_code.strip()
            name = stock.stock_name.strip()
            if not code or not name:
                raise StockDataValidationError(
                    f"{market} returned a stock with an empty code or name."
                )
            if not is_valid_stock_code(code):
                raise StockDataValidationError(
                    f"{market} returned invalid stock code {stock.stock_code!r}."
                )
            if stock.market != market:
                raise StockDataValidationError(
                    f"{market} provider returned stock {stock.stock_code} "
                    f"with market {stock.market!r}."
                )
            normalized_values.append(
                Stock(stock_code=code, stock_name=name, market=market)
            )
        return normalized_values

    @staticmethod
    def _deduplicate(stocks: list[Stock]) -> list[Stock]:
        by_code: dict[str, Stock] = {}
        for stock in stocks:
            previous = by_code.get(stock.stock_code)
            if previous is None:
                by_code[stock.stock_code] = stock
                continue
            if previous != stock:
                raise StockDataValidationError(
                    f"Conflicting stock records for code {stock.stock_code}: "
                    f"{previous!r} vs {stock!r}."
                )
        return list(by_code.values())
