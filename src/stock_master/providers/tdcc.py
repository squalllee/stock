"""TDCC official shareholding-distribution provider."""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from stock_master.config import TDCC_API_URL
from stock_master.exceptions import StockDataValidationError
from stock_master.models import TDCCDistribution

from .http import JsonHttpClient
from .record_utils import ensure_non_empty, payload_records

logger = logging.getLogger(__name__)

# These aliases cover the official Chinese API names and the English names
# used by fixtures and by older versions of the OpenAPI schema.
DATA_DATE_FIELDS = (
    "資料日期",
    "DataDate",
    "data_date",
    "Date",
    "date",
)
STOCK_CODE_FIELDS = (
    "證券代號",
    "股票代號",
    "公司代號",
    "SecurityCode",
    "SecuritiesCode",
    "StockCode",
    "stock_code",
    "Code",
    "code",
)
HOLDING_LEVEL_FIELDS = (
    "持股分級",
    "持股/單位數分級",
    "持股分級名稱",
    "HoldingLevel",
    "holding_level",
    "level",
)
SHAREHOLDER_COUNT_FIELDS = (
    "人數",
    "股東人數",
    "NumberOfHolders",
    "NumberOfShareholders",
    "HolderCount",
    "ShareholderCount",
    "shareholder_count",
    "holders",
)
SHARE_COUNT_FIELDS = (
    "股數",
    "股數/單位數",
    "持有股數",
    "NumberOfShares",
    "ShareCount",
    "share_count",
    "shares",
)
HOLDING_RATIO_FIELDS = (
    "占集保庫存數比例%",
    "占集保庫存數比例(%)",
    "占集保庫存數比例 (%)",
    "占集保庫存數比例（%）",
    "占集保庫存數比例",
    "HoldingRatio",
    "HoldingPercentage",
    "Percentage",
    "holding_ratio",
    "ratio",
)

_MISSING = object()
_DATE_SEPARATOR_PATTERN = re.compile(
    r"^(?P<year>\d{3,4})[./-](?P<month>\d{1,2})[./-](?P<day>\d{1,2})$"
)
_TOTAL_MARKERS = ("合計", "總計", "total", "grandtotal")


def _first_raw_value(
    record: Mapping[str, Any], fields: Sequence[str]
) -> object:
    """Return the first present, non-blank value from a field alias list."""

    for field in fields:
        # TDCC currently returns the first column as ``\ufeff資料日期``. The
        # response body's UTF-8 BOM decoder cannot remove a BOM embedded
        # inside a JSON property name, so accept it at the field boundary too.
        if field in record:
            value = record[field]
        elif f"\ufeff{field}" in record:
            value = record[f"\ufeff{field}"]
        else:
            continue
        if value is None:
            continue
        if isinstance(value, str) and not value.replace("\ufeff", "").strip():
            continue
        return value
    return _MISSING


def _required_value(
    record: Mapping[str, Any],
    fields: Sequence[str],
    semantic_name: str,
    record_index: int,
) -> object:
    value = _first_raw_value(record, fields)
    if value is _MISSING:
        raise StockDataValidationError(
            "TDCC response schema changed: "
            f"record {record_index} missing {semantic_name} field; "
            f"expected one of {', '.join(fields)}."
        )
    return value


def _clean_text(value: object) -> str:
    return str(value).replace("\ufeff", "").strip()


def _build_date(year: int, month: int, day: int, raw: object) -> str:
    # TDCC's seven-digit dates use the Republic of China calendar.
    if year < 1000:
        year += 1911
    try:
        return date(year, month, day).isoformat()
    except ValueError as exc:
        raise StockDataValidationError(
            f"TDCC returned invalid data_date {raw!r}."
        ) from exc


def normalize_data_date(value: object) -> str:
    """Normalize TDCC dates to ``YYYY-MM-DD``.

    Supported inputs include ISO dates, slash/dash separated dates, ROC
    dates such as ``1150810`` and ``115/08/10``, and compact Gregorian dates.
    """

    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    raw = _clean_text(value)
    if raw.endswith(".0") and raw[:-2].isdigit():
        raw = raw[:-2]
    raw = (
        raw.replace("民國", "")
        .replace("／", "/")
        .replace("－", "-")
        .replace("年", "/")
        .replace("月", "/")
        .replace("日", "")
    )

    match = _DATE_SEPARATOR_PATTERN.fullmatch(raw)
    if match:
        return _build_date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            value,
        )

    if raw.isdigit() and len(raw) in {7, 8}:
        if len(raw) == 7:
            return _build_date(
                int(raw[:3]), int(raw[3:5]), int(raw[5:]), value
            )
        return _build_date(
            int(raw[:4]), int(raw[4:6]), int(raw[6:]), value
        )

    raise StockDataValidationError(
        f"TDCC returned invalid data_date {value!r}; expected ROC or ISO date."
    )


def _parse_non_negative_integer(
    value: object, semantic_name: str, record_index: int
) -> int:
    raw = _clean_text(value).replace(",", "").replace("，", "")
    raw = raw.replace(" ", "").replace("\u00a0", "")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise StockDataValidationError(
            f"TDCC record {record_index} has invalid {semantic_name} {value!r}."
        ) from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise StockDataValidationError(
            f"TDCC record {record_index} has non-integer {semantic_name} {value!r}."
        )
    result = int(number)
    if result < 0:
        raise StockDataValidationError(
            f"TDCC record {record_index} has negative {semantic_name} {value!r}."
        )
    return result


def _parse_ratio(value: object, record_index: int) -> float:
    raw = _clean_text(value).replace(",", "").replace("，", "")
    raw = raw.replace("％", "%").replace(" ", "").replace("\u00a0", "")
    if raw.endswith("%"):
        raw = raw[:-1]
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise StockDataValidationError(
            f"TDCC record {record_index} has invalid holding_ratio {value!r}."
        ) from exc
    if not number.is_finite():
        raise StockDataValidationError(
            f"TDCC record {record_index} has non-finite holding_ratio {value!r}."
        )
    result = float(number)
    if not math.isfinite(result) or not 0 <= result <= 100:
        raise StockDataValidationError(
            f"TDCC record {record_index} has out-of-range holding_ratio {value!r}."
        )
    return result


def _is_total_label(value: object) -> bool:
    normalized = "".join(_clean_text(value).casefold().split())
    return any(
        marker.casefold() == normalized or marker.casefold() in normalized
        for marker in _TOTAL_MARKERS
    )


def is_total_record(record: Mapping[str, Any]) -> bool:
    """Return whether a raw TDCC record is the aggregate row."""

    level = _first_raw_value(record, HOLDING_LEVEL_FIELDS)
    return level is not _MISSING and _is_total_label(level)


def normalize_tdcc_record(
    record: Mapping[str, Any], *, record_index: int = 0
) -> TDCCDistribution | None:
    """Normalize one raw TDCC record; return ``None`` for aggregate rows."""

    if not isinstance(record, Mapping):
        raise StockDataValidationError(
            f"TDCC response schema changed: record {record_index} is not an object."
        )

    data_date = normalize_data_date(
        _required_value(record, DATA_DATE_FIELDS, "data_date", record_index)
    )
    stock_code = _clean_text(
        _required_value(record, STOCK_CODE_FIELDS, "stock_code", record_index)
    )
    if not stock_code:
        raise StockDataValidationError(
            f"TDCC record {record_index} has an empty stock_code."
        )
    holding_level = _clean_text(
        _required_value(
            record, HOLDING_LEVEL_FIELDS, "holding_level", record_index
        )
    )
    if not holding_level:
        raise StockDataValidationError(
            f"TDCC record {record_index} has an empty holding_level."
        )
    if _is_total_label(holding_level):
        return None

    shareholder_count = _parse_non_negative_integer(
        _required_value(
            record,
            SHAREHOLDER_COUNT_FIELDS,
            "shareholder_count",
            record_index,
        ),
        "shareholder_count",
        record_index,
    )
    share_count = _parse_non_negative_integer(
        _required_value(record, SHARE_COUNT_FIELDS, "share_count", record_index),
        "share_count",
        record_index,
    )
    holding_ratio = _parse_ratio(
        _required_value(
            record, HOLDING_RATIO_FIELDS, "holding_ratio", record_index
        ),
        record_index,
    )
    return TDCCDistribution(
        data_date=data_date,
        stock_code=stock_code,
        holding_level=holding_level,
        shareholder_count=shareholder_count,
        share_count=share_count,
        holding_ratio=holding_ratio,
    )


def validate_tdcc_schema(records: Sequence[Mapping[str, Any]]) -> None:
    """Validate the required shape before a bulk response is persisted."""

    if not records:
        raise StockDataValidationError(
            "TDCC response returned an empty distribution list; refusing to sync."
        )
    for index, record in enumerate(records):
        # normalize_tdcc_record performs semantic and numeric validation too;
        # this function is kept public for callers that only want a schema pass.
        normalize_tdcc_record(record, record_index=index)


class TDCCDistributionProvider:
    """Fetch and normalize the bulk TDCC shareholding-distribution feed."""

    def __init__(
        self,
        http_client: JsonHttpClient,
        *,
        url: str = TDCC_API_URL,
    ) -> None:
        self.http_client = http_client
        self.url = url
        self.last_skipped_total_count = 0
        self.last_raw_record_count = 0

    def fetch(self, stock_codes: set[str]) -> list[TDCCDistribution]:
        """Fetch the bulk feed once and keep only records in ``stock_codes``."""

        valid_stock_codes = {
            _clean_text(stock_code)
            for stock_code in stock_codes
            if _clean_text(stock_code)
        }
        self.last_skipped_total_count = 0
        self.last_raw_record_count = 0

        if not valid_stock_codes:
            logger.info("TDCC fetch skipped because stock universe is empty")
            return []

        logger.info("Starting TDCC fetch for %s master stocks", len(valid_stock_codes))
        payload = self.http_client.get_json(self.url)
        records = payload_records(payload, "TDCC")
        ensure_non_empty(records, "TDCC distribution")
        self.last_raw_record_count = len(records)

        distributions: list[TDCCDistribution] = []
        for index, record in enumerate(records):
            normalized = normalize_tdcc_record(record, record_index=index)
            if normalized is None:
                self.last_skipped_total_count += 1
                continue
            if normalized.stock_code not in valid_stock_codes:
                continue
            distributions.append(normalized)

        logger.info(
            "TDCC returned %s records; kept %s master-stock records; skipped %s totals",
            len(records),
            len(distributions),
            self.last_skipped_total_count,
        )
        return distributions
