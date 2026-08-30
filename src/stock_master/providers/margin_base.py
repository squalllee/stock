"""Shared interfaces and normalization helpers for margin providers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from stock_master.exceptions import StockDataValidationError
from stock_master.models import MarginHistory


class MarginProvider(Protocol):
    """Common interface for one exchange's daily margin report."""

    market: str
    last_trade_date: str | None
    last_no_data: bool

    def fetch(self, trade_date: date | None = None) -> list[MarginHistory]:
        """Fetch one requested date, or the provider's latest date."""


def build_query_url(url: str, parameters: Mapping[str, str]) -> str:
    """Add query parameters without losing any existing URL parameters."""

    parsed = urlsplit(url)
    query = list(parse_qsl(parsed.query, keep_blank_values=True))
    query.extend((key, value) for key, value in parameters.items())
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def normalize_trade_date(value: object, market: str) -> str:
    """Normalize ISO, Gregorian compact, and ROC dates to ISO format."""

    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    raw = str(value).replace("\ufeff", "").strip()
    raw = (
        raw.replace("民國", "")
        .replace("／", "/")
        .replace("－", "-")
        .replace("年", "/")
        .replace("月", "/")
        .replace("日", "")
    )
    raw = raw.replace(".", "/")
    parts = [part.strip() for part in raw.replace("-", "/").split("/")]
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        year, month, day = (int(part) for part in parts)
        if year < 1000:
            year += 1911
        try:
            return date(year, month, day).isoformat()
        except ValueError as exc:
            raise StockDataValidationError(
                f"{market} returned invalid trade date {value!r}."
            ) from exc

    if raw.isdigit() and len(raw) in {7, 8}:
        if len(raw) == 7:
            return normalize_trade_date(
                f"{raw[:3]}/{raw[3:5]}/{raw[5:]}", market
            )
        return normalize_trade_date(
            f"{raw[:4]}/{raw[4:6]}/{raw[6:]}", market
        )

    raise StockDataValidationError(
        f"{market} returned invalid trade date {value!r}; expected ROC or ISO date."
    )


def build_date_parameter(trade_date: date, *, roc: bool) -> str:
    """Format a query date for an official exchange endpoint."""

    year = trade_date.year - 1911 if roc else trade_date.year
    return (
        f"{year:03d}/{trade_date.month:02d}/{trade_date.day:02d}"
        if roc
        else f"{year:04d}{trade_date.month:02d}{trade_date.day:02d}"
    )


def parse_non_negative_int(
    value: object,
    *,
    market: str,
    field: str,
    record_index: int,
) -> int:
    """Parse an official integer quantity without converting missing values."""

    raw = str(value).replace("\ufeff", "").strip()
    raw = raw.replace(",", "").replace("，", "").replace("\u00a0", "")
    if not raw or raw in {"--", "－", "-"}:
        raise StockDataValidationError(
            f"{market} record {record_index} has missing {field}; expected an integer."
        )
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise StockDataValidationError(
            f"{market} record {record_index} has invalid {field} {value!r}."
        ) from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise StockDataValidationError(
            f"{market} record {record_index} has non-integer {field} {value!r}."
        )
    result = int(number)
    if result < 0:
        raise StockDataValidationError(
            f"{market} record {record_index} has negative {field} {value!r}."
        )
    return result


def parse_optional_non_negative_int(
    value: object,
    *,
    market: str,
    field: str,
    record_index: int,
) -> int | None:
    """Return None for an optional blank value; reject other invalid values."""

    raw = str(value).replace("\ufeff", "").strip()
    if not raw or raw in {"--", "－", "-"}:
        return None
    return parse_non_negative_int(
        value,
        market=market,
        field=field,
        record_index=record_index,
    )


def parse_optional_non_negative_float(
    value: object,
    *,
    market: str,
    field: str,
    record_index: int,
) -> float | None:
    """Parse an optional decimal percentage from an official report."""

    raw = str(value).replace("\ufeff", "").strip()
    if not raw or raw in {"--", "－", "-"}:
        return None
    raw = raw.replace(",", "").replace("，", "").replace("%", "")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise StockDataValidationError(
            f"{market} record {record_index} has invalid {field} {value!r}."
        ) from exc
    if not number.is_finite() or number < 0:
        raise StockDataValidationError(
            f"{market} record {record_index} has invalid {field} {value!r}."
        )
    result = float(number)
    if not isfinite(result):
        raise StockDataValidationError(
            f"{market} record {record_index} has invalid {field} {value!r}."
        )
    return result


def calculate_margin_utilization(
    margin_balance: int,
    margin_limit: int | None,
) -> float | None:
    """Derive TWSE utilization when the official report omits the percentage."""

    if margin_limit is None or margin_limit <= 0:
        return None
    return round(margin_balance / margin_limit * 100, 4)


def find_field_index(
    fields: Sequence[object],
    aliases: Sequence[str],
    *,
    occurrence: int = 0,
    market: str,
) -> int:
    """Find a column by official label and occurrence, or fail loudly."""

    normalized_aliases = {
        "".join(str(alias).replace("\u00a0", " ").split()).casefold()
        for alias in aliases
    }
    matches = [
        index
        for index, field in enumerate(fields)
        if "".join(str(field).replace("\u00a0", " ").split()).casefold()
        in normalized_aliases
    ]
    if occurrence < 0 or occurrence >= len(matches):
        raise StockDataValidationError(
            f"{market} margin schema changed: missing column occurrence "
            f"{occurrence} for one of {', '.join(aliases)}."
        )
    return matches[occurrence]


def require_mapping_payload(payload: object, market: str) -> Mapping[str, Any]:
    """Require the wrapper object used by the exchange history endpoints."""

    if not isinstance(payload, Mapping):
        raise StockDataValidationError(
            f"{market} margin response schema changed: expected an object payload."
        )
    return payload


def require_tables(payload: Mapping[str, Any], market: str) -> list[Mapping[str, Any]]:
    """Validate the nested tables collection in an exchange response."""

    tables = payload.get("tables")
    if not isinstance(tables, Sequence) or isinstance(
        tables, (str, bytes, bytearray)
    ):
        raise StockDataValidationError(
            f"{market} margin response schema changed: expected tables."
        )
    result: list[Mapping[str, Any]] = []
    for index, table in enumerate(tables):
        if not isinstance(table, Mapping):
            raise StockDataValidationError(
                f"{market} margin response schema changed: table {index} is invalid."
            )
        result.append(table)
    return result


def require_table_data(
    table: Mapping[str, Any],
    *,
    market: str,
) -> tuple[list[object], list[list[object]]]:
    """Validate one table's fields and rows before normalization."""

    fields = table.get("fields")
    data = table.get("data")
    if not isinstance(fields, Sequence) or isinstance(
        fields, (str, bytes, bytearray)
    ):
        raise StockDataValidationError(
            f"{market} margin response schema changed: missing table fields."
        )
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
        raise StockDataValidationError(
            f"{market} margin response schema changed: missing table data."
        )
    rows: list[list[object]] = []
    for index, row in enumerate(data):
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)):
            raise StockDataValidationError(
                f"{market} margin response schema changed: row {index} is invalid."
            )
        rows.append(list(row))
    return list(fields), rows


def validate_requested_date(
    actual_date: str,
    requested_date: date | None,
    market: str,
) -> None:
    """Prevent a provider from silently returning a different trading date."""

    if requested_date is not None and actual_date != requested_date.isoformat():
        raise StockDataValidationError(
            f"{market} margin returned unexpected trade date: "
            f"expected {requested_date.isoformat()}, got {actual_date}."
        )


def is_no_data_status(value: object) -> bool:
    """Recognize official no-data markers without accepting arbitrary empties."""

    normalized = str(value).strip().casefold()
    return any(
        marker in normalized
        for marker in ("沒有符合條件的資料", "查無資料", "no data", "nodata")
    )


def validate_margin_record(record: MarginHistory, market: str) -> None:
    """Validate a normalized provider record at the domain boundary."""

    if record.market != market:
        raise StockDataValidationError(
            f"{market} margin returned record with market {record.market!r}."
        )
    try:
        date.fromisoformat(record.trade_date)
    except (TypeError, ValueError) as exc:
        raise StockDataValidationError(
            f"{market} margin returned invalid normalized date {record.trade_date!r}."
        ) from exc
    if not record.stock_code.strip():
        raise StockDataValidationError(f"{market} margin returned empty stock code.")
    quantities = (
        record.margin_buy,
        record.margin_sell,
        record.margin_cash_redemption,
        record.margin_previous_balance,
        record.margin_balance,
        record.short_buy,
        record.short_sell,
        record.short_stock_redemption,
        record.short_previous_balance,
        record.short_balance,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in quantities):
        raise StockDataValidationError(
            f"{market} margin returned invalid non-negative quantity values."
        )
    if record.offsetting_volume is not None and (
        isinstance(record.offsetting_volume, bool)
        or not isinstance(record.offsetting_volume, int)
        or record.offsetting_volume < 0
    ):
        raise StockDataValidationError(
            f"{market} margin returned invalid offsetting_volume."
        )
    if record.margin_limit is not None and (
        isinstance(record.margin_limit, bool)
        or not isinstance(record.margin_limit, int)
        or record.margin_limit < 0
    ):
        raise StockDataValidationError(
            f"{market} margin returned invalid margin_limit."
        )
    if record.margin_utilization is not None and (
        isinstance(record.margin_utilization, bool)
        or not isinstance(record.margin_utilization, (int, float))
        or not isfinite(float(record.margin_utilization))
        or record.margin_utilization < 0
        or record.margin_utilization > 100
    ):
        raise StockDataValidationError(
            f"{market} margin returned invalid margin_utilization."
        )
