"""Raw provider record normalization and schema checks."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from stock_master.exceptions import StockDataValidationError
from stock_master.models import Stock

CODE_FIELDS = (
    "公司代號",
    "證券代號",
    "股票代號",
    "Code",
    "StockCode",
    "SecuritiesCompanyCode",
    "code",
)

NAME_FIELDS = (
    "公司簡稱",
    "證券名稱",
    "股票名稱",
    "Name",
    "CompanyAbbreviation",
    "CompanyNameAbbreviation",
    "CompanyName",
    "stock_name",
    "公司名稱",
    "name",
)

STOCK_CODE_PATTERN = re.compile(r"^\d{4}$")


def clean_text(value: Any) -> str:
    """Convert an API scalar to a trimmed string."""

    if value is None:
        return ""
    return str(value).replace("\ufeff", "").strip()


def is_valid_stock_code(code: str) -> bool:
    """Return whether a normalized code has the four-digit stock shape."""

    return bool(STOCK_CODE_PATTERN.fullmatch(clean_text(code)))


def first_value(record: Mapping[str, Any], fields: Sequence[str]) -> str:
    """Return the first non-empty value for any supported field alias."""

    for field in fields:
        if field in record:
            value = clean_text(record[field])
            if value:
                return value
    return ""


def validate_raw_schema(
    records: Sequence[Mapping[str, Any]], market: str
) -> None:
    """Fail loudly if the official response no longer has code/name fields."""

    if not any(first_value(record, CODE_FIELDS) for record in records):
        raise StockDataValidationError(
            f'{market} response schema changed. Expected field "{CODE_FIELDS[0]}" '
            "not found."
        )
    if not any(first_value(record, NAME_FIELDS) for record in records):
        raise StockDataValidationError(
            f'{market} response schema changed. Expected field "{NAME_FIELDS[0]}" '
            "not found."
        )


def normalize_stock(record: Mapping[str, Any], market: str) -> Stock:
    """Project a provider record into the common Stock model."""

    code = first_value(record, CODE_FIELDS)
    name = first_value(record, NAME_FIELDS)

    if not code:
        raise StockDataValidationError(
            f'{market} record is missing required field "{CODE_FIELDS[0]}".'
        )
    if not name:
        raise StockDataValidationError(
            f'{market} record is missing required field "{NAME_FIELDS[0]}".'
        )
    if not is_valid_stock_code(code):
        raise StockDataValidationError(
            f"{market} returned invalid common-stock code {code!r}."
        )
    if market not in {"TWSE", "TPEX"}:
        raise StockDataValidationError(f"Unsupported market {market!r}.")

    return Stock(stock_code=code, stock_name=name, market=market)
