"""Weighted-moving-average margin cost estimation service."""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime

from stock_master.config import DEFAULT_MARGIN_FINANCING_RATIO
from stock_master.exceptions import DatabaseError, StockDataValidationError
from stock_master.models import (
    DEFAULT_MARGIN_MODEL_VERSION,
    MarginCostEstimate,
    MarginEstimate,
    MarginHistory,
    PriceHistory,
)
from stock_master.repositories.margin_estimate_repository import (
    MarginEstimateRepository,
)
from stock_master.repositories.margin_repository import MarginHistoryRepository
from stock_master.repositories.price_repository import PriceHistoryRepository

from .margin_maintenance_estimator import MarginMaintenanceEstimator

logger = logging.getLogger(__name__)


class MarginCostEstimator:
    """Estimate margin average cost from raw margin and daily price history.

    The source-of-truth position quantity is always ``margin_balance`` from
    ``margin_history``.  A new margin buy is valued at
    ``trade_value / trade_volume``; selling or cash redemption does not change
    the remaining average cost in this V1 weighted-moving-average model.
    """

    def __init__(
        self,
        margin_repository: MarginHistoryRepository,
        price_repository: PriceHistoryRepository,
        estimate_repository: MarginEstimateRepository | None = None,
        *,
        margin_financing_ratio: float = DEFAULT_MARGIN_FINANCING_RATIO,
        model_version: str = DEFAULT_MARGIN_MODEL_VERSION,
        maintenance_estimator: MarginMaintenanceEstimator | None = None,
    ) -> None:
        _validate_ratio(margin_financing_ratio)
        if not model_version.strip():
            raise ValueError("model_version cannot be empty")
        self.margin_repository = margin_repository
        self.price_repository = price_repository
        self.estimate_repository = estimate_repository or MarginEstimateRepository(
            margin_repository.db_path
        )
        self.margin_financing_ratio = float(margin_financing_ratio)
        self.model_version = model_version
        self.maintenance_estimator = maintenance_estimator or MarginMaintenanceEstimator(
            self.margin_financing_ratio,
            model_version=model_version,
        )
        self.last_skipped_close_records: list[tuple[str, str]] = []
        self.last_skipped_price_records: list[tuple[str, str]] = []
        self.last_skipped_cost_records: list[tuple[str, str]] = []

    def estimate(
        self,
        stock_code: str,
        start_date: str | date,
        end_date: str | date | None = None,
        *,
        persist: bool = True,
        skip_missing_close: bool = True,
        skip_missing_price: bool = True,
    ) -> list[MarginEstimate]:
        """Estimate one stock over an inclusive range.

        Passing only one date estimates that date.  ``persist=False`` makes
        the estimator a pure read/model operation for notebooks and tests.
        """

        start, end = _coerce_range(start_date, end_date)
        return self.estimate_range(
            stock_code,
            start,
            end,
            persist=persist,
            skip_missing_close=skip_missing_close,
            skip_missing_price=skip_missing_price,
        )

    def estimate_range(
        self,
        stock_code: str,
        start_date: str | date,
        end_date: str | date,
        *,
        persist: bool = True,
        skip_missing_close: bool = True,
        skip_missing_price: bool = True,
    ) -> list[MarginEstimate]:
        """Estimate one stock over an inclusive date range."""

        if not stock_code.strip():
            raise StockDataValidationError("stock_code cannot be empty.")
        start, end = _coerce_range(start_date, end_date)
        margins = self.margin_repository.get_range(
            start.isoformat(), end.isoformat(), stock_code.strip()
        )
        self.last_skipped_close_records = []
        self.last_skipped_price_records = []
        self.last_skipped_cost_records = []
        estimates = self._estimate_records(
            margins,
            start,
            end,
            skip_missing_close=skip_missing_close,
            skip_missing_price=skip_missing_price,
        )
        if persist and estimates:
            self.estimate_repository.upsert_many(estimates)
        return estimates

    def estimate_all(
        self,
        start_date: str | date,
        end_date: str | date,
        *,
        persist: bool = True,
        skip_missing_close: bool = True,
        skip_missing_price: bool = True,
    ) -> list[MarginEstimate]:
        """Estimate every stock with margin facts in an inclusive range."""

        start, end = _coerce_range(start_date, end_date)
        margins = self.margin_repository.get_range(start.isoformat(), end.isoformat())
        grouped: dict[str, list[MarginHistory]] = defaultdict(list)
        for record in margins:
            grouped[record.stock_code].append(record)
        self.last_skipped_close_records = []
        self.last_skipped_price_records = []
        self.last_skipped_cost_records = []
        estimates: list[MarginEstimate] = []
        for stock_code in sorted(grouped):
            estimates.extend(
                self._estimate_records(
                    grouped[stock_code],
                    start,
                    end,
                    skip_missing_close=skip_missing_close,
                    skip_missing_price=skip_missing_price,
                )
            )
        if persist and estimates:
            self.estimate_repository.upsert_many(estimates)
        return estimates

    def estimate_costs(
        self,
        stock_code: str,
        start_date: str | date,
        end_date: str | date | None = None,
    ) -> list[MarginCostEstimate]:
        """Return only the WMA cost layer without persisting final estimates."""

        start, end = _coerce_range(start_date, end_date)
        margins = self.margin_repository.get_range(
            start.isoformat(), end.isoformat(), stock_code.strip()
        )
        prices = self._load_prices(stock_code.strip(), start, end)
        costs = self._estimate_costs(margins, prices, skip_missing_price=False)
        return [
            MarginCostEstimate(
                trade_date=trade_date,
                stock_code=stock_code.strip(),
                estimated_margin_avg_cost=cost,
                model_version=self.model_version,
            )
            for trade_date, cost in costs
            if cost is not None
        ]

    def _estimate_records(
        self,
        margins: list[MarginHistory],
        start: date,
        end: date,
        *,
        skip_missing_close: bool,
        skip_missing_price: bool,
    ) -> list[MarginEstimate]:
        if not margins:
            return []
        stock_code = margins[0].stock_code
        price_by_date = self._load_prices(stock_code, start, end)
        costs = self._estimate_costs(
            margins,
            price_by_date,
            skip_missing_price=skip_missing_price,
        )
        estimates: list[MarginEstimate] = []
        for margin, (trade_date, cost) in zip(margins, costs, strict=True):
            price = price_by_date.get(trade_date)
            if price is None:
                message = (
                    f"Missing price_history for {stock_code} on {trade_date}; "
                    "cannot estimate maintenance ratio."
                )
                if not skip_missing_price:
                    raise StockDataValidationError(message)
                logger.warning("%s Skipping this estimate row.", message)
                self.last_skipped_price_records.append((stock_code, trade_date))
                continue
            if cost is None:
                message = (
                    f"Estimated margin cost unavailable for {stock_code} on "
                    f"{trade_date}; cannot estimate maintenance ratio."
                )
                if not skip_missing_price:
                    raise StockDataValidationError(message)
                logger.warning("%s Skipping this estimate row.", message)
                self.last_skipped_cost_records.append((stock_code, trade_date))
                continue
            if price.market != margin.market:
                raise StockDataValidationError(
                    f"Market mismatch for {stock_code} on {trade_date}: "
                    f"margin={margin.market}, price={price.market}."
                )
            if price.close_price is None:
                message = (
                    f"Missing close_price for {stock_code} on {trade_date}; "
                    "cannot estimate maintenance ratio."
                )
                if not skip_missing_close:
                    raise StockDataValidationError(message)
                logger.warning("%s Skipping this estimate row.", message)
                self.last_skipped_close_records.append((stock_code, trade_date))
                continue
            estimates.append(
                self.maintenance_estimator.estimate(
                    trade_date=trade_date,
                    stock_code=stock_code,
                    estimated_margin_avg_cost=cost,
                    close_price=price.close_price,
                    margin_financing_ratio=self.margin_financing_ratio,
                    model_version=self.model_version,
                )
            )
        return estimates

    def _load_prices(
        self,
        stock_code: str,
        start: date,
        end: date,
    ) -> dict[str, PriceHistory]:
        values = self.price_repository.get_range(
            start.isoformat(), end.isoformat(), stock_code
        )
        return {item.trade_date: item for item in values}

    def _estimate_costs(
        self,
        margins: list[MarginHistory],
        prices: dict[str, PriceHistory],
        *,
        skip_missing_price: bool,
    ) -> list[tuple[str, float | None]]:
        if not margins:
            return []
        costs: list[tuple[str, float | None]] = []
        previous_margin: MarginHistory | None = None
        previous_cost: float | None = None
        for index, margin in enumerate(margins):
            if previous_margin is not None:
                if previous_margin.margin_balance != margin.margin_previous_balance:
                    logger.warning(
                        "Margin balance continuity discrepancy for %s on %s: "
                        "previous official balance=%s, current previous balance=%s",
                        margin.stock_code,
                        margin.trade_date,
                        previous_margin.margin_balance,
                        margin.margin_previous_balance,
                    )
                expected_balance = (
                    margin.margin_previous_balance
                    + margin.margin_buy
                    - margin.margin_sell
                    - margin.margin_cash_redemption
                )
                if expected_balance != margin.margin_balance:
                    logger.warning(
                        "Margin balance reconciliation discrepancy for %s on %s: "
                        "expected=%s official=%s; official balance remains source of truth",
                        margin.stock_code,
                        margin.trade_date,
                        expected_balance,
                        margin.margin_balance,
                    )

            price = prices.get(margin.trade_date)
            if price is None:
                if margin.margin_balance <= 0:
                    current_cost = 0.0
                elif (
                    index > 0
                    and margin.margin_buy == 0
                    and margin.margin_previous_balance > 0
                    and previous_cost is not None
                ):
                    # No new lots need pricing on this date, so the previous
                    # WMA remains valid even when the stock had no price row.
                    current_cost = previous_cost
                elif skip_missing_price:
                    # Keep the cost unknown until a valid price can bootstrap
                    # it.  The final maintenance row will be skipped rather
                    # than inventing a price for this date.
                    current_cost = None
                else:
                    raise StockDataValidationError(
                        f"Missing price_history for {margin.stock_code} on "
                        f"{margin.trade_date}; cannot price new or bootstrap "
                        "margin lots."
                    )
                if current_cost is not None and (
                    not math.isfinite(current_cost) or current_cost < 0
                ):
                    raise StockDataValidationError(
                        f"Invalid estimated margin cost for {margin.stock_code} on "
                        f"{margin.trade_date}."
                    )
                costs.append(
                    (
                        margin.trade_date,
                        None if current_cost is None else float(current_cost),
                    )
                )
                previous_margin = margin
                previous_cost = (
                    None if current_cost is None else float(current_cost)
                )
                continue
            market_average = price.market_average_price

            if margin.margin_balance <= 0:
                current_cost = 0.0
            elif index == 0:
                if margin.margin_buy > 0:
                    if market_average is None and skip_missing_price:
                        current_cost = None
                    else:
                        current_cost = _require_market_average(
                            market_average, margin.stock_code, margin.trade_date
                        )
                else:
                    current_cost = _bootstrap_cost(
                        market_average,
                        price.close_price,
                        margin.stock_code,
                        margin.trade_date,
                        allow_missing=skip_missing_price,
                    )
            elif margin.margin_buy > 0:
                if previous_cost is None and margin.margin_previous_balance > 0:
                    current_cost = None
                elif market_average is None and skip_missing_price:
                    # New margin lots must use the market-average proxy; a
                    # close price is not an acceptable replacement here.
                    current_cost = None
                else:
                    buy_price = _require_market_average(
                        market_average, margin.stock_code, margin.trade_date
                    )
                    gross_quantity = (
                        margin.margin_previous_balance + margin.margin_buy
                    )
                    if gross_quantity <= 0:
                        current_cost = buy_price
                    else:
                        gross_cost = (
                            margin.margin_previous_balance * (previous_cost or 0.0)
                            + margin.margin_buy * buy_price
                        )
                        current_cost = gross_cost / gross_quantity
            elif margin.margin_previous_balance > 0:
                # A sell or cash redemption changes quantity only; it does not
                # change the estimated average cost of the remaining lots.
                current_cost = previous_cost
            else:
                # This is an inconsistent but recoverable official snapshot:
                # bootstrap from the only available market proxy.
                current_cost = _bootstrap_cost(
                    market_average,
                    price.close_price,
                    margin.stock_code,
                    margin.trade_date,
                    allow_missing=skip_missing_price,
                )

            if current_cost is not None and (
                not math.isfinite(current_cost) or current_cost < 0
            ):
                raise StockDataValidationError(
                    f"Invalid estimated margin cost for {margin.stock_code} on "
                    f"{margin.trade_date}."
                )
            costs.append(
                (
                    margin.trade_date,
                    None if current_cost is None else float(current_cost),
                )
            )
            previous_margin = margin
            previous_cost = None if current_cost is None else float(current_cost)
        return costs


def _require_market_average(
    market_average: float | None,
    stock_code: str,
    trade_date: str,
) -> float:
    if market_average is None:
        raise StockDataValidationError(
            f"Cannot estimate new margin cost for {stock_code} on {trade_date}: "
            "trade_volume is zero, so market_average_price is unavailable."
        )
    return float(market_average)


def _bootstrap_cost(
    market_average: float | None,
    close_price: float | None,
    stock_code: str,
    trade_date: str,
    *,
    allow_missing: bool,
) -> float | None:
    """Bootstrap from market average, with close as a documented fallback."""

    if market_average is not None:
        return float(market_average)
    if close_price is not None:
        logger.warning(
            "Market average unavailable for %s on %s; using close_price "
            "as bootstrap fallback",
            stock_code,
            trade_date,
        )
        return float(close_price)
    if allow_missing:
        return None
    raise StockDataValidationError(
        f"Cannot bootstrap margin cost for {stock_code} on {trade_date}: "
        "market_average_price and close_price are unavailable."
    )


def _coerce_range(
    start_date: str | date,
    end_date: str | date | None,
) -> tuple[date, date]:
    start = _coerce_date(start_date, "start_date")
    end = _coerce_date(end_date if end_date is not None else start, "end_date")
    if start is None or end is None:
        raise StockDataValidationError("start_date and end_date are required.")
    if start > end:
        raise StockDataValidationError("start_date must not be after end_date.")
    return start, end


def _coerce_date(value: str | date | datetime, field: str) -> date | None:
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
    raise StockDataValidationError(f"{field} must be a date or ISO date string.")


def _validate_ratio(value: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 < value <= 1
    ):
        raise ValueError(
            "margin_financing_ratio must be finite, greater than 0, and <= 1."
        )
