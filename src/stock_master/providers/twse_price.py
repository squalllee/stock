"""TWSE daily closing-price provider."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from stock_master.config import TWSE_PRICE_URL
from stock_master.exceptions import StockDataValidationError
from stock_master.models import PriceHistory

from .http import JsonHttpClient
from .price_base import (
    PriceProvider,
    build_date_parameter,
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
    validate_requested_date,
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


class TWSEPriceProvider(PriceProvider):
    """Fetch all TWSE daily price rows for one trading date."""

    market = "TWSE"

    def __init__(
        self,
        http_client: JsonHttpClient,
        *,
        url: str = TWSE_PRICE_URL,
    ) -> None:
        self.http_client = http_client
        self.url = url
        self.last_trade_date: str | None = None
        self.last_no_data = False
        self.last_raw_record_count = 0
        self.last_skipped_total_count = 0

    def fetch_latest_data_date(self) -> str:
        """Return the latest trade date exposed by the TWSE price API."""

        request_url = build_query_url(
            self.url,
            {"response": "json", "type": "ALLBUT0999"},
        )
        payload = require_mapping_payload(
            self.http_client.get_json(request_url), self.market
        )
        status = str(payload.get("stat", "")).strip()
        if status.casefold() not in {"ok", "success"}:
            raise StockDataValidationError(
                f"TWSE price response returned unexpected status {status!r}."
            )
        raw_date = payload.get("date")
        if raw_date in (None, ""):
            raise StockDataValidationError(
                "TWSE price response schema changed: missing response date."
            )
        return normalize_trade_date(raw_date, self.market)

    def fetch(self, trade_date: date | None = None) -> list[PriceHistory]:
        """Fetch a specified date, or the latest available TWSE date."""

        self.last_trade_date = None
        self.last_no_data = False
        self.last_raw_record_count = 0
        self.last_skipped_total_count = 0
        parameters = {
            "response": "json",
            "type": "ALLBUT0999",
        }
        if trade_date is not None:
            parameters["date"] = build_date_parameter(trade_date)
        request_url = build_query_url(self.url, parameters)
        logger.info(
            "Fetching TWSE price data%s",
            f" for {trade_date}" if trade_date else "",
        )
        payload = require_mapping_payload(
            self.http_client.get_json(request_url), self.market
        )
        status = str(payload.get("stat", "")).strip()
        if is_no_data_status(status):
            self.last_no_data = True
            logger.info("TWSE price has no data for %s", trade_date or "latest")
            return []
        if status.casefold() not in {"ok", "success"}:
            raise StockDataValidationError(
                f"TWSE price response returned unexpected status {status!r}."
            )
        actual_date_raw = payload.get("date")
        if actual_date_raw in (None, ""):
            raise StockDataValidationError(
                "TWSE price response schema changed: missing response date."
            )
        actual_date = normalize_trade_date(actual_date_raw, self.market)
        validate_requested_date(actual_date, trade_date, self.market)
        self.last_trade_date = actual_date

        table = self._find_price_table(require_tables(payload, self.market))
        fields, rows = require_table_data(table, market=self.market)
        indexes = {
            "stock_code": find_field_index(
                fields, ("證券代號", "代號"), market=self.market
            ),
            "volume": find_field_index(
                fields, ("成交股數", "成交股數(股)"), market=self.market
            ),
            "transaction_count": find_field_index(
                fields, ("成交筆數", "成交筆數(筆)"), market=self.market
            ),
            "value": find_field_index(
                fields, ("成交金額", "成交金額(元)"), market=self.market
            ),
            "open": find_field_index(fields, ("開盤價", "開盤"), market=self.market),
            "high": find_field_index(fields, ("最高價", "最高"), market=self.market),
            "low": find_field_index(fields, ("最低價", "最低"), market=self.market),
            "close": find_field_index(fields, ("收盤價", "收盤"), market=self.market),
        }

        records: list[PriceHistory] = []
        self.last_raw_record_count = len(rows)
        for row_index, row in enumerate(rows):
            code = str(
                _row_value(
                    row,
                    indexes["stock_code"],
                    market=self.market,
                    field="stock code",
                    row_index=row_index,
                )
            ).replace("\ufeff", "").strip()
            if not code or code.casefold() in {"合計", "總計", "total"}:
                self.last_skipped_total_count += 1
                continue
            record = PriceHistory(
                trade_date=actual_date,
                stock_code=code,
                market=self.market,
                trade_volume=parse_non_negative_int(
                    _row_value(row, indexes["volume"], market=self.market, field="trade_volume", row_index=row_index),
                    market=self.market,
                    field="trade_volume",
                    record_index=row_index,
                ),
                trade_value=parse_non_negative_int(
                    _row_value(row, indexes["value"], market=self.market, field="trade_value", row_index=row_index),
                    market=self.market,
                    field="trade_value",
                    record_index=row_index,
                ),
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
            records.append(record)

        if not records:
            raise StockDataValidationError(
                "TWSE price returned no stock records; refusing to sync."
            )
        logger.info(
            "TWSE price date=%s returned %s rows; kept %s records",
            actual_date,
            len(rows),
            len(records),
        )
        return records

    @staticmethod
    def _find_price_table(tables: list[dict[str, Any]]) -> dict[str, Any]:
        required = {"成交股數", "成交金額"}
        for table in tables:
            fields = table.get("fields")
            data = table.get("data")
            if not isinstance(fields, list) or not isinstance(data, list):
                continue
            normalized = {"".join(str(field).split()) for field in fields}
            if required <= normalized or "每日收盤行情" in str(table.get("title", "")):
                return table
        raise StockDataValidationError(
            "TWSE price response schema changed: daily price table not found."
        )

