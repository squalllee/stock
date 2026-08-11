"""TPEx historical daily closing-price provider."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from datetime import date
from typing import Any, Callable

from stock_master.config import TPEX_PRICE_URL
from stock_master.exceptions import StockDataValidationError
from stock_master.models import PriceHistory

from .http import JsonHttpClient
from .price_base import (
    PriceProvider,
    build_query_url,
    find_field_index,
    is_no_data_status,
    normalize_trade_date,
    parse_non_negative_int,
    parse_optional_non_negative_int,
    parse_optional_price,
    require_mapping_payload,
    require_table_data,
    require_tables,
    validate_price_record,
)

logger = logging.getLogger(__name__)


def _row_value(
    row: list[object], index: int, *, market: str, field: str, row_index: int
) -> object:
    if index >= len(row):
        raise StockDataValidationError(
            f"{market} price response schema changed: row {row_index} "
            f"is missing {field}."
        )
    return row[index]


class TPExPriceProvider(PriceProvider):
    """Fetch TPEx price data through the official code/month endpoint.

    TPEx's historical page exposes one stock code and one month per request.
    The provider therefore receives the stock-master universe and caches each
    ``(stock_code, year, month)`` response for the lifetime of the instance.
    """

    market = "TPEX"

    def __init__(
        self,
        http_client: JsonHttpClient,
        *,
        url: str = TPEX_PRICE_URL,
        stock_codes: Iterable[str] | None = None,
        request_delay_seconds: float = 0.2,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if request_delay_seconds < 0:
            raise ValueError("request_delay_seconds cannot be negative")
        self.http_client = http_client
        self.url = url
        self.stock_codes: set[str] = set()
        self.set_stock_codes(stock_codes or ())
        self.request_delay_seconds = request_delay_seconds
        self._sleep = sleep
        self._month_cache: dict[tuple[str, int, int], list[PriceHistory]] = {}
        self.last_trade_date: str | None = None
        self.last_no_data = False
        self.last_raw_record_count = 0
        self.last_skipped_total_count = 0

    def set_stock_codes(self, stock_codes: Iterable[str]) -> None:
        """Replace the code universe used for the next fetch."""

        values = {str(code).strip() for code in stock_codes if str(code).strip()}
        self.stock_codes = values

    def fetch(self, trade_date: date | None = None) -> list[PriceHistory]:
        """Fetch one date from cached monthly code responses."""

        self.last_trade_date = None
        self.last_no_data = False
        self.last_raw_record_count = 0
        self.last_skipped_total_count = 0
        if not self.stock_codes:
            self.last_no_data = True
            logger.info("TPEx price has no configured stock codes")
            return []

        month_date = trade_date or date.today()
        values: list[PriceHistory] = []
        for code in sorted(self.stock_codes):
            monthly = self._get_month(code, month_date.year, month_date.month)
            self.last_raw_record_count += len(monthly)
            if trade_date is None:
                values.extend(monthly)
            else:
                values.extend(
                    record for record in monthly if record.trade_date == trade_date.isoformat()
                )

        if not values:
            self.last_no_data = True
            logger.info("TPEx price has no data for %s", trade_date or "latest")
            return []

        if trade_date is None:
            actual_date = max(record.trade_date for record in values)
            values = [record for record in values if record.trade_date == actual_date]
        else:
            actual_date = trade_date.isoformat()
        self.last_trade_date = actual_date
        logger.info(
            "TPEx price date=%s returned %s normalized records",
            actual_date,
            len(values),
        )
        return values

    def _get_month(self, stock_code: str, year: int, month: int) -> list[PriceHistory]:
        key = (stock_code, year, month)
        cached = self._month_cache.get(key)
        if cached is not None:
            return list(cached)
        if self._month_cache and self.request_delay_seconds:
            self._sleep(self.request_delay_seconds)
        month_parameter = f"{year:04d}/{month:02d}/01"
        request_url = build_query_url(
            self.url,
            {
                "code": stock_code,
                "date": month_parameter,
                "response": "json",
            },
        )
        logger.debug("Fetching TPEx price code=%s month=%s", stock_code, month_parameter)
        payload = require_mapping_payload(
            self.http_client.get_json(request_url), self.market
        )
        status = str(payload.get("stat", "")).strip()
        if is_no_data_status(status) or (status.casefold() == "ok" and not payload.get("tables")):
            self._month_cache[key] = []
            return []
        if status and status.casefold() not in {"ok", "success"}:
            raise StockDataValidationError(
                f"TPEx price response returned unexpected status {status!r}."
            )
        table = self._find_price_table(require_tables(payload, self.market))
        fields, rows = require_table_data(table, market=self.market)
        indexes = {
            "date": find_field_index(fields, ("日期", "日 期"), market=self.market),
            "volume": find_field_index(fields, ("成交張數", "成交張數(張)"), market=self.market),
            "value": find_field_index(fields, ("成交仟元", "成交金額(仟元)"), market=self.market),
            "open": find_field_index(fields, ("開盤", "開盤價"), market=self.market),
            "high": find_field_index(fields, ("最高", "最高價"), market=self.market),
            "low": find_field_index(fields, ("最低", "最低價"), market=self.market),
            "close": find_field_index(fields, ("收盤", "收盤價"), market=self.market),
            "transaction_count": find_field_index(fields, ("筆數", "成交筆數"), market=self.market),
        }
        values: list[PriceHistory] = []
        for row_index, row in enumerate(rows):
            raw_date = _row_value(
                row,
                indexes["date"],
                market=self.market,
                field="trade_date",
                row_index=row_index,
            )
            actual_date = normalize_trade_date(raw_date, self.market)
            lots = parse_non_negative_int(
                _row_value(row, indexes["volume"], market=self.market, field="trade_volume_lots", row_index=row_index),
                market=self.market,
                field="trade_volume_lots",
                record_index=row_index,
            )
            thousand_twd = parse_non_negative_int(
                _row_value(row, indexes["value"], market=self.market, field="trade_value_thousand_twd", row_index=row_index),
                market=self.market,
                field="trade_value_thousand_twd",
                record_index=row_index,
            )
            record = PriceHistory(
                trade_date=actual_date,
                stock_code=stock_code,
                market=self.market,
                trade_volume=lots * 1000,
                trade_value=thousand_twd * 1000,
                open_price=parse_optional_price(
                    _row_value(row, indexes["open"], market=self.market, field="open_price", row_index=row_index),
                    market=self.market,
                    field="open_price",
                    record_index=row_index,
                ),
                high_price=parse_optional_price(
                    _row_value(row, indexes["high"], market=self.market, field="high_price", row_index=row_index),
                    market=self.market,
                    field="high_price",
                    record_index=row_index,
                ),
                low_price=parse_optional_price(
                    _row_value(row, indexes["low"], market=self.market, field="low_price", row_index=row_index),
                    market=self.market,
                    field="low_price",
                    record_index=row_index,
                ),
                close_price=parse_optional_price(
                    _row_value(row, indexes["close"], market=self.market, field="close_price", row_index=row_index),
                    market=self.market,
                    field="close_price",
                    record_index=row_index,
                ),
                transaction_count=parse_optional_non_negative_int(
                    _row_value(row, indexes["transaction_count"], market=self.market, field="transaction_count", row_index=row_index),
                    market=self.market,
                    field="transaction_count",
                    record_index=row_index,
                ),
            )
            validate_price_record(record, self.market)
            values.append(record)
        self._month_cache[key] = values
        return list(values)

    @staticmethod
    def _find_price_table(tables: list[dict[str, Any]]) -> dict[str, Any]:
        for table in tables:
            fields = table.get("fields")
            data = table.get("data")
            if not isinstance(fields, list) or not isinstance(data, list):
                continue
            normalized = {"".join(str(field).split()) for field in fields}
            if {"日期", "成交張數", "成交仟元"} <= normalized:
                return table
        raise StockDataValidationError(
            "TPEx price response schema changed: daily price table not found."
        )

