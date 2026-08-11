"""Independent maintenance-ratio estimation model."""

from __future__ import annotations

import math
from datetime import date, datetime

from stock_master.config import DEFAULT_MARGIN_FINANCING_RATIO
from stock_master.exceptions import StockDataValidationError
from stock_master.models import (
    DEFAULT_MARGIN_MODEL_VERSION,
    MarginEstimate,
)


class MarginMaintenanceEstimator:
    """Estimate financing per share, maintenance ratio, and 130% price.

    This model intentionally uses the official closing price.  The market
    average price is only used by the separate margin-cost model as a proxy
    for newly added financing lots.
    """

    def __init__(
        self,
        margin_financing_ratio: float = DEFAULT_MARGIN_FINANCING_RATIO,
        *,
        model_version: str = DEFAULT_MARGIN_MODEL_VERSION,
    ) -> None:
        self._validate_ratio(margin_financing_ratio)
        if not model_version.strip():
            raise ValueError("model_version cannot be empty")
        self.margin_financing_ratio = float(margin_financing_ratio)
        self.model_version = model_version

    def estimate(
        self,
        trade_date: str | date,
        stock_code: str,
        estimated_margin_avg_cost: float,
        close_price: float,
        margin_financing_ratio: float | None = None,
        model_version: str | None = None,
    ) -> MarginEstimate:
        """Return one independent maintenance estimate."""

        normalized_date = _coerce_date(trade_date)
        if not stock_code.strip():
            raise StockDataValidationError("stock_code cannot be empty.")
        ratio = (
            self.margin_financing_ratio
            if margin_financing_ratio is None
            else float(margin_financing_ratio)
        )
        self._validate_ratio(ratio)
        cost = _validate_non_negative_number(
            estimated_margin_avg_cost, "estimated_margin_avg_cost"
        )
        close = _validate_non_negative_number(close_price, "close_price")
        version = model_version or self.model_version
        if not version.strip():
            raise StockDataValidationError("model_version cannot be empty.")

        financing_per_share = cost * ratio
        if financing_per_share == 0:
            # A zero official margin balance has no meaningful denominator.
            # Persisting zero is deterministic and avoids an artificial infinity.
            maintenance_ratio = 0.0
            price_130 = 0.0
        else:
            maintenance_ratio = close / financing_per_share * 100.0
            price_130 = financing_per_share * 1.30
        return MarginEstimate(
            trade_date=normalized_date,
            stock_code=stock_code.strip(),
            estimated_margin_avg_cost=cost,
            margin_financing_ratio=ratio,
            estimated_financing_per_share=financing_per_share,
            close_price=close,
            estimated_maintenance_ratio=maintenance_ratio,
            estimated_130_price=price_130,
            model_version=version,
        )

    @staticmethod
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


def _validate_non_negative_number(value: float, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise StockDataValidationError(f"{field} must be a finite non-negative number.")
    return float(value)


def _coerce_date(value: str | date) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError) as exc:
        raise StockDataValidationError(
            "trade_date must be an ISO date in YYYY-MM-DD format."
        ) from exc
