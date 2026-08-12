"""Application query service for the read-only stock Web platform."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from stock_master.exceptions import DatabaseError
from stock_master.models import (
    DEFAULT_MARGIN_MODEL_VERSION,
    MarginEstimate,
    MarginHistory,
    PriceHistory,
    Stock,
    TDCCDistribution,
)
from stock_master.repositories import (
    MarginEstimateRepository,
    MarginHistoryRepository,
    PriceHistoryRepository,
    StockRepository,
    TDCCDistributionRepository,
)

from ..errors import WebError

logger = logging.getLogger(__name__)

_STOCK_CODE_PATTERN = re.compile(r"^\d{4}$")
MAX_HISTORY_LIMIT = 1000
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100


class StockQueryService:
    """Coordinate repository reads without importing any upstream provider."""

    def __init__(
        self,
        stock_repository: StockRepository,
        price_repository: PriceHistoryRepository,
        margin_repository: MarginHistoryRepository,
        margin_estimate_repository: MarginEstimateRepository,
        tdcc_repository: TDCCDistributionRepository,
    ) -> None:
        self.stock_repository = stock_repository
        self.price_repository = price_repository
        self.margin_repository = margin_repository
        self.margin_estimate_repository = margin_estimate_repository
        self.tdcc_repository = tdcc_repository

    def search_stocks(
        self,
        query: str = "",
        *,
        market: str | None = None,
        limit: int = DEFAULT_SEARCH_LIMIT,
        offset: int = 0,
    ) -> list[Stock]:
        """Search by code/name with a bounded result set."""

        if limit < 1 or limit > MAX_SEARCH_LIMIT:
            raise WebError(
                "INVALID_PAGINATION",
                f"limit must be between 1 and {MAX_SEARCH_LIMIT}.",
                400,
            )
        if offset < 0:
            raise WebError("INVALID_PAGINATION", "offset must be non-negative.", 400)
        if market is not None:
            market = market.upper()
            if market not in {"TWSE", "TPEX"}:
                raise WebError(
                    "INVALID_MARKET",
                    "market must be TWSE or TPEX.",
                    400,
                )
        try:
            return self.stock_repository.search(
                query,
                market=market,
                limit=limit,
                offset=offset,
            )
        except DatabaseError as exc:
            raise WebError("DATABASE_UNAVAILABLE", str(exc), 500) from exc

    def get_stock(self, stock_code: str) -> Stock:
        """Validate and retrieve one stock master row."""

        code = self._validate_stock_code(stock_code)
        try:
            stock = self.stock_repository.get_by_code(code)
        except DatabaseError as exc:
            raise WebError("DATABASE_UNAVAILABLE", str(exc), 500) from exc
        if stock is None:
            raise WebError(
                "STOCK_NOT_FOUND",
                f"Stock {code} was not found.",
                404,
            )
        return stock

    def get_overview(self, stock_code: str) -> dict[str, Any]:
        """Return stock metadata plus independently optional latest sections."""

        stock = self.get_stock(stock_code)
        return {
            "stock": _stock_dict(stock),
            "price": _safe_latest(
                lambda: _price_dict(
                    self.price_repository.get_latest_by_stock_code(stock.stock_code)
                )
            ),
            "margin": _safe_latest(
                lambda: _margin_dict(
                    self.margin_repository.get_latest_by_stock_code(stock.stock_code)
                )
            ),
            "margin_estimate": _safe_latest(
                lambda: _estimate_dict(
                    _latest_estimate(
                        self.margin_estimate_repository,
                        stock.stock_code,
                    )
                )
            ),
            "tdcc": _safe_latest(
                lambda: _tdcc_latest_summary(
                    self.tdcc_repository.get_latest_by_stock_code(stock.stock_code)
                )
            ),
        }

    def get_price_history(
        self,
        stock_code: str,
        *,
        start_date: str | None,
        end_date: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        stock = self.get_stock(stock_code)
        start, end = self._resolve_range(start_date, end_date)
        limit, offset = self._validate_history_pagination(limit, offset)
        try:
            if start_date is None and end_date is None:
                values, has_more = _recent_page(
                    lambda fetch_limit: self.price_repository.get_recent_by_stock_code(
                        stock.stock_code,
                        limit=fetch_limit,
                    ),
                    limit,
                    offset,
                )
            else:
                values = self.price_repository.get_range(
                    start,
                    end,
                    stock.stock_code,
                    limit=limit + 1,
                    offset=offset,
                )
                has_more = len(values) > limit
                values = values[:limit]
        except DatabaseError as exc:
            raise WebError("DATABASE_UNAVAILABLE", str(exc), 500) from exc
        return _page(
            stock.stock_code,
            start,
            end,
            values,
            _price_dict,
            limit,
            offset,
            has_more,
        )

    def get_margin_history(
        self,
        stock_code: str,
        *,
        start_date: str | None,
        end_date: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        stock = self.get_stock(stock_code)
        start, end = self._resolve_range(start_date, end_date)
        limit, offset = self._validate_history_pagination(limit, offset)
        try:
            if start_date is None and end_date is None:
                values, has_more = _recent_page(
                    lambda fetch_limit: self.margin_repository.get_recent_by_stock_code(
                        stock.stock_code,
                        limit=fetch_limit,
                    ),
                    limit,
                    offset,
                )
            else:
                values = self.margin_repository.get_range(
                    start,
                    end,
                    stock.stock_code,
                    limit=limit + 1,
                    offset=offset,
                )
                has_more = len(values) > limit
                values = values[:limit]
        except DatabaseError as exc:
            raise WebError("DATABASE_UNAVAILABLE", str(exc), 500) from exc
        return _page(
            stock.stock_code,
            start,
            end,
            values,
            _margin_dict,
            limit,
            offset,
            has_more,
        )

    def get_margin_estimates(
        self,
        stock_code: str,
        *,
        start_date: str | None,
        end_date: str | None,
        limit: int,
        offset: int,
        model_version: str = DEFAULT_MARGIN_MODEL_VERSION,
    ) -> dict[str, Any]:
        stock = self.get_stock(stock_code)
        start, end = self._resolve_range(start_date, end_date)
        limit, offset = self._validate_history_pagination(limit, offset)
        try:
            if start_date is None and end_date is None:
                values, has_more = _recent_page(
                    lambda fetch_limit: self.margin_estimate_repository.get_recent_by_stock_code(
                        stock.stock_code,
                        limit=fetch_limit,
                        model_version=model_version,
                    ),
                    limit,
                    offset,
                )
            else:
                values = self.margin_estimate_repository.get_range(
                    start,
                    end,
                    stock.stock_code,
                    model_version,
                    limit=limit + 1,
                    offset=offset,
                )
                has_more = len(values) > limit
                values = values[:limit]
        except DatabaseError as exc:
            raise WebError("DATABASE_UNAVAILABLE", str(exc), 500) from exc
        result = _page(
            stock.stock_code,
            start,
            end,
            values,
            _estimate_dict,
            limit,
            offset,
            has_more,
        )
        result["model_version"] = model_version
        return result

    def get_latest_margin_estimate(
        self,
        stock_code: str,
        *,
        model_version: str = DEFAULT_MARGIN_MODEL_VERSION,
    ) -> dict[str, Any] | None:
        stock = self.get_stock(stock_code)
        try:
            values = self.margin_estimate_repository.get_by_stock_code(
                stock.stock_code, model_version
            )
        except DatabaseError as exc:
            raise WebError("DATABASE_UNAVAILABLE", str(exc), 500) from exc
        if not values:
            return None
        return _estimate_dict(values[-1])

    def get_tdcc_history(
        self,
        stock_code: str,
        *,
        start_date: str | None,
        end_date: str | None,
        limit: int,
        offset: int,
    ) -> dict[str, Any]:
        stock = self.get_stock(stock_code)
        start, end = self._resolve_range(start_date, end_date)
        limit, offset = self._validate_history_pagination(limit, offset)
        try:
            if start_date is None and end_date is None:
                values, has_more = _recent_page(
                    lambda fetch_limit: self.tdcc_repository.get_recent_by_stock_code(
                        stock.stock_code,
                        limit=fetch_limit,
                    ),
                    limit,
                    offset,
                )
            else:
                values = self.tdcc_repository.get_range(
                    start,
                    end,
                    stock.stock_code,
                    limit=limit + 1,
                    offset=offset,
                )
                has_more = len(values) > limit
                values = values[:limit]
        except DatabaseError as exc:
            raise WebError("DATABASE_UNAVAILABLE", str(exc), 500) from exc
        return _page(
            stock.stock_code,
            start,
            end,
            values,
            _tdcc_dict,
            limit,
            offset,
            has_more,
        )

    def get_latest_tdcc(self, stock_code: str) -> dict[str, Any]:
        """Return the complete latest TDCC distribution for one stock."""

        stock = self.get_stock(stock_code)
        try:
            values = self.tdcc_repository.get_latest_by_stock_code(stock.stock_code)
        except DatabaseError as exc:
            raise WebError("DATABASE_UNAVAILABLE", str(exc), 500) from exc
        if not values:
            raise WebError(
                "NO_TDCC_DATA",
                f"No TDCC data is available for {stock.stock_code}.",
                404,
            )
        return {
            "stock_code": stock.stock_code,
            "data_date": values[0].data_date,
            "items": [_tdcc_dict(value) for value in values],
        }

    def get_dashboard_summary(self) -> dict[str, Any]:
        """Return homepage metrics; absent data tables are shown as null."""

        try:
            market_counts = self.stock_repository.get_market_counts()
            total = sum(market_counts.values())
        except DatabaseError as exc:
            raise WebError("DATABASE_UNAVAILABLE", str(exc), 500) from exc
        return {
            "stock_count": total,
            "twse_count": market_counts.get("TWSE", 0),
            "tpex_count": market_counts.get("TPEX", 0),
            "latest_price_date": _safe_value(
                self.price_repository.get_latest_trade_date
            ),
            "latest_margin_date": _safe_value(
                self.margin_repository.get_latest_trade_date
            ),
            "latest_tdcc_date": _safe_value(
                self.tdcc_repository.get_latest_data_date
            ),
        }

    def health(self) -> dict[str, str]:
        try:
            self.stock_repository.get_market_counts()
        except DatabaseError as exc:
            raise WebError("DATABASE_UNAVAILABLE", str(exc), 500) from exc
        return {"status": "ok", "database": "ok"}

    @staticmethod
    def _validate_stock_code(stock_code: str) -> str:
        if not _STOCK_CODE_PATTERN.fullmatch(stock_code):
            raise WebError(
                "INVALID_STOCK_CODE",
                "stock_code must be exactly four digits.",
                400,
            )
        return stock_code

    @staticmethod
    def _validate_history_pagination(limit: int, offset: int) -> tuple[int, int]:
        if limit < 1 or limit > MAX_HISTORY_LIMIT:
            raise WebError(
                "INVALID_PAGINATION",
                f"limit must be between 1 and {MAX_HISTORY_LIMIT}.",
                400,
            )
        if offset < 0:
            raise WebError("INVALID_PAGINATION", "offset must be non-negative.", 400)
        return limit, offset

    @staticmethod
    def _resolve_range(
        start_date: str | None,
        end_date: str | None,
    ) -> tuple[str, str]:
        today = date.today()
        if start_date is None and end_date is None:
            # Ninety trading days is approximately half a calendar year; the
            # UI asks for 90 rows while this metadata stays useful to callers.
            return (today - timedelta(days=180)).isoformat(), today.isoformat()
        start = _parse_date(start_date, "from") if start_date else date.min
        end = _parse_date(end_date, "to") if end_date else today
        if start > end:
            raise WebError(
                "INVALID_DATE_RANGE",
                "from must not be after to.",
                400,
            )
        return start.isoformat(), end.isoformat()


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise WebError(
            "INVALID_DATE",
            f"{field} must be an ISO date in YYYY-MM-DD format.",
            400,
        ) from exc


def _page(
    stock_code: str,
    start: str,
    end: str,
    values: list[Any],
    serializer: Callable[[Any], dict[str, Any] | None],
    limit: int,
    offset: int,
    has_more: bool,
) -> dict[str, Any]:
    return {
        "stock_code": stock_code,
        "from": start,
        "to": end,
        "items": [item for value in values if (item := serializer(value)) is not None],
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
    }


def _recent_page(
    fetch: Callable[[int], list[Any]],
    limit: int,
    offset: int,
) -> tuple[list[Any], bool]:
    """Page a recent result set from newest to oldest, returning charts asc."""

    fetch_limit = min(limit + offset + 1, MAX_HISTORY_LIMIT + 1)
    newest_first = list(reversed(fetch(fetch_limit)))
    has_more = len(newest_first) > offset + limit
    page = newest_first[offset : offset + limit]
    page.reverse()
    return page, has_more


def _safe_latest(callback: Callable[[], Any]) -> Any:
    try:
        return callback()
    except DatabaseError as exc:
        logger.warning("Optional Web data section unavailable: %s", exc)
        return None


def _safe_value(callback: Callable[[], Any]) -> Any:
    try:
        return callback()
    except DatabaseError as exc:
        logger.warning("Optional Web data date unavailable: %s", exc)
        return None


def _stock_dict(value: Stock) -> dict[str, Any]:
    return {
        "stock_code": value.stock_code,
        "stock_name": value.stock_name,
        "market": value.market,
    }


def _price_dict(value: PriceHistory | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "trade_date": value.trade_date,
        "open": value.open_price,
        "high": value.high_price,
        "low": value.low_price,
        "close": value.close_price,
        "trade_volume": value.trade_volume,
        "trade_value": value.trade_value,
        "transaction_count": value.transaction_count,
        "market_average_price": value.market_average_price,
    }


def _margin_dict(value: MarginHistory | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "trade_date": value.trade_date,
        "market": value.market,
        "margin_buy": value.margin_buy,
        "margin_sell": value.margin_sell,
        "margin_cash_redemption": value.margin_cash_redemption,
        "margin_previous_balance": value.margin_previous_balance,
        "margin_balance": value.margin_balance,
        "short_buy": value.short_buy,
        "short_sell": value.short_sell,
        "short_stock_redemption": value.short_stock_redemption,
        "short_previous_balance": value.short_previous_balance,
        "short_balance": value.short_balance,
        "offsetting_volume": value.offsetting_volume,
    }


def _estimate_dict(value: MarginEstimate | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "trade_date": value.trade_date,
        "estimated_margin_avg_cost": value.estimated_margin_avg_cost,
        "margin_financing_ratio": value.margin_financing_ratio,
        "estimated_financing_per_share": value.estimated_financing_per_share,
        "close_price": value.close_price,
        "estimated_maintenance_ratio": value.estimated_maintenance_ratio,
        "estimated_130_price": value.estimated_130_price,
        "model_version": value.model_version,
        "estimated": True,
    }


def _latest_estimate(
    repository: MarginEstimateRepository,
    stock_code: str,
    model_version: str = DEFAULT_MARGIN_MODEL_VERSION,
) -> MarginEstimate | None:
    values = repository.get_by_stock_code(stock_code, model_version)
    return values[-1] if values else None


def _tdcc_dict(value: TDCCDistribution) -> dict[str, Any]:
    return {
        "data_date": value.data_date,
        "holding_level": value.holding_level,
        "shareholder_count": value.shareholder_count,
        "share_count": value.share_count,
        "holding_ratio": value.holding_ratio,
    }


def _tdcc_latest_summary(
    values: list[TDCCDistribution],
) -> dict[str, Any] | None:
    if not values:
        return None
    return {
        "latest_date": values[0].data_date,
        "levels": len(values),
    }
