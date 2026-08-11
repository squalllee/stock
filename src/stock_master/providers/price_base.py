"""Shared interfaces and validation helpers for daily price providers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from stock_master.exceptions import StockDataValidationError
from stock_master.models import PriceHistory


class PriceProvider(Protocol):
    """Common interface for one exchange's daily trading report."""

    market: str
    last_trade_date: str | None
    last_no_data: bool

    def fetch(self, trade_date: date | None = None) -> list[PriceHistory]:
        """Fetch one requested date, or the provider's latest available date."""


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
    # TPEx annotates some trading dates with a trailing footnote marker,
    # e.g. ``115/07/16*``.  The marker is presentation metadata, not part of
    # the date, so remove only known trailing annotation characters.
    raw = raw.rstrip("*＊#†‡").strip()
    raw = (
        raw.replace("民國", "")
        .replace("／", "/")
        .replace("－", "-")
        .replace("年", "/")
        .replace("月", "/")
        .replace("日", "")
        .replace(".", "/")
    )
    parts = [part.strip() for part in raw.replace("-", "/").split("/")]
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        year, month, day = (int(part) for part in parts)
        if year < 1000:
            year += 1911
        try:
            return date(year, month, day).isoformat()
        except ValueError as exc:
            raise StockDataValidationError(
                f"{market} price returned invalid trade date {value!r}."
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
        f"{market} price returned invalid trade date {value!r}; "
        "expected ROC or ISO date."
    )


def build_date_parameter(trade_date: date, *, roc: bool = False) -> str:
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
    """Parse an official integer quantity and reject missing values."""

    raw = str(value).replace("\ufeff", "").strip()
    raw = raw.replace(",", "").replace("，", "").replace("\u00a0", "")
    if not raw or raw in {"--", "－", "-"}:
        raise StockDataValidationError(
            f"{market} price record {record_index} has missing {field}; "
            "expected an integer."
        )
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise StockDataValidationError(
            f"{market} price record {record_index} has invalid {field} {value!r}."
        ) from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise StockDataValidationError(
            f"{market} price record {record_index} has non-integer {field} {value!r}."
        )
    result = int(number)
    if result < 0:
        raise StockDataValidationError(
            f"{market} price record {record_index} has negative {field} {value!r}."
        )
    return result


def parse_optional_non_negative_int(
    value: object,
    *,
    market: str,
    field: str,
    record_index: int,
) -> int | None:
    """Return None for optional blanks and reject other invalid values."""

    raw = str(value).replace("\ufeff", "").strip()
    if not raw or raw in {"--", "－", "-"}:
        return None
    return parse_non_negative_int(
        value,
        market=market,
        field=field,
        record_index=record_index,
    )


def parse_optional_price(
    value: object,
    *,
    market: str,
    field: str,
    record_index: int,
) -> float | None:
    """Parse a nullable non-negative price from an official response."""

    raw = str(value).replace("\ufeff", "").strip()
    raw = raw.replace(",", "").replace("，", "").replace("\u00a0", "")
    if not raw or raw in {"--", "－", "-"}:
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError) as exc:
        raise StockDataValidationError(
            f"{market} price record {record_index} has invalid {field} {value!r}."
        ) from exc
    if not isfinite(number) or number < 0:
        raise StockDataValidationError(
            f"{market} price record {record_index} has invalid non-negative "
            f"{field} {value!r}."
        )
    return number


def find_field_index(
    fields: Sequence[object],
    aliases: Sequence[str],
    *,
    occurrence: int = 0,
    market: str,
) -> int:
    """Find a field by normalized official label."""

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
            f"{market} price schema changed: missing column occurrence "
            f"{occurrence} for one of {', '.join(aliases)}."
        )
    return matches[occurrence]


def require_mapping_payload(payload: object, market: str) -> Mapping[str, Any]:
    """Require an object response from the official endpoint."""

    if not isinstance(payload, Mapping):
        raise StockDataValidationError(
            f"{market} price response schema changed: expected an object payload."
        )
    return payload


def require_tables(payload: Mapping[str, Any], market: str) -> list[Mapping[str, Any]]:
    """Validate a response's nested tables collection."""

    tables = payload.get("tables")
    if not isinstance(tables, Sequence) or isinstance(
        tables, (str, bytes, bytearray)
    ):
        raise StockDataValidationError(
            f"{market} price response schema changed: expected tables."
        )
    result: list[Mapping[str, Any]] = []
    for index, table in enumerate(tables):
        if not isinstance(table, Mapping):
            raise StockDataValidationError(
                f"{market} price response schema changed: table {index} is invalid."
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
            f"{market} price response schema changed: missing table fields."
        )
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
        raise StockDataValidationError(
            f"{market} price response schema changed: missing table data."
        )
    rows: list[list[object]] = []
    for index, row in enumerate(data):
        if not isinstance(row, Sequence) or isinstance(row, (str, bytes, bytearray)):
            raise StockDataValidationError(
                f"{market} price response schema changed: row {index} is invalid."
            )
        rows.append(list(row))
    return list(fields), rows


def validate_requested_date(
    actual_date: str,
    requested_date: date | None,
    market: str,
) -> None:
    """Prevent a provider from silently returning a different requested date."""

    if requested_date is not None and actual_date != requested_date.isoformat():
        raise StockDataValidationError(
            f"{market} price returned unexpected trade date: expected "
            f"{requested_date.isoformat()}, got {actual_date}."
        )


def is_no_data_status(value: object) -> bool:
    """Recognize explicit no-data markers from TWSE and TPEx."""

    normalized = str(value).strip().casefold()
    return any(
        marker in normalized
        for marker in ("沒有符合條件的資料", "查無資料", "no data", "nodata")
    )


def validate_price_record(record: PriceHistory, market: str) -> None:
    """Validate a normalized daily price record at the domain boundary."""

    if not isinstance(record, PriceHistory):
        raise StockDataValidationError(
            f"{market} price provider returned a non-PriceHistory value."
        )
    if record.market != market:
        raise StockDataValidationError(
            f"{market} price returned record with market {record.market!r}."
        )
    try:
        date.fromisoformat(record.trade_date)
    except (TypeError, ValueError) as exc:
        raise StockDataValidationError(
            f"{market} price returned invalid normalized date "
            f"{record.trade_date!r}."
        ) from exc
    if not isinstance(record.stock_code, str) or not record.stock_code.strip():
        raise StockDataValidationError(f"{market} price returned empty stock code.")
    quantities = (record.trade_volume, record.trade_value)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in quantities
    ):
        raise StockDataValidationError(
            f"{market} price returned invalid non-negative volume/value."
        )
    optional_values = (
        record.open_price,
        record.high_price,
        record.low_price,
        record.close_price,
    )
    if any(
        value is not None
        and (isinstance(value, bool) or not isinstance(value, (int, float)))
        for value in optional_values
    ):
        raise StockDataValidationError(
            f"{market} price returned invalid OHLC values."
        )
    if any(value is not None and (not isfinite(float(value)) or value < 0) for value in optional_values):
        raise StockDataValidationError(
            f"{market} price returned non-finite or negative OHLC values."
        )
    if record.transaction_count is not None and (
        isinstance(record.transaction_count, bool)
        or not isinstance(record.transaction_count, int)
        or record.transaction_count < 0
    ):
        raise StockDataValidationError(
            f"{market} price returned invalid transaction_count."
        )
