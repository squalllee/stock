"""TPEx daily margin-trading history provider."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from stock_master.config import TPEX_MARGIN_URL
from stock_master.exceptions import StockDataValidationError
from stock_master.models import MarginHistory

from .http import JsonHttpClient
from .margin_base import (
    MarginProvider,
    build_date_parameter,
    build_query_url,
    find_field_index,
    is_no_data_status,
    normalize_trade_date,
    parse_non_negative_int,
    parse_optional_non_negative_int,
    parse_optional_non_negative_float,
    require_mapping_payload,
    require_table_data,
    require_tables,
    validate_margin_record,
    validate_requested_date,
)

logger = logging.getLogger(__name__)


def _row_value(
    row: list[object], index: int, *, market: str, field: str, row_index: int
) -> object:
    if index >= len(row):
        raise StockDataValidationError(
            f"{market} margin response schema changed: row {row_index} "
            f"is missing {field}."
        )
    return row[index]


class TPExMarginProvider(MarginProvider):
    """Fetch all TPEx margin rows for one trading date."""

    market = "TPEX"

    def __init__(
        self,
        http_client: JsonHttpClient,
        *,
        url: str = TPEX_MARGIN_URL,
    ) -> None:
        self.http_client = http_client
        self.url = url
        self.last_trade_date: str | None = None
        self.last_no_data = False
        self.last_raw_record_count = 0
        self.last_skipped_total_count = 0

    def fetch(self, trade_date: date | None = None) -> list[MarginHistory]:
        """Fetch a specified date, or the latest available TPEx date."""

        self.last_trade_date = None
        self.last_no_data = False
        self.last_raw_record_count = 0
        self.last_skipped_total_count = 0

        parameters = {"l": "zh-tw", "o": "json", "s": "0"}
        if trade_date is not None:
            parameters["d"] = build_date_parameter(trade_date, roc=True)
        request_url = build_query_url(self.url, parameters)
        logger.info("Fetching TPEx margin data%s", f" for {trade_date}" if trade_date else "")
        payload = require_mapping_payload(
            self.http_client.get_json(request_url), self.market
        )

        status = str(payload.get("stat", "")).strip()
        if is_no_data_status(status):
            self.last_no_data = True
            logger.info("TPEx margin has no data for %s", trade_date or "latest")
            return []
        if status and status.casefold() not in {"ok", "success"}:
            raise StockDataValidationError(
                f"TPEx margin response returned unexpected status {status!r}."
            )
        tables = require_tables(payload, self.market)
        table = self._find_data_table(tables)
        fields, rows = require_table_data(table, market=self.market)

        actual_date_raw = payload.get("date") or table.get("date")
        if actual_date_raw in (None, ""):
            raise StockDataValidationError(
                "TPEx margin response schema changed: missing response date."
            )
        actual_date = normalize_trade_date(actual_date_raw, self.market)
        validate_requested_date(actual_date, trade_date, self.market)
        self.last_trade_date = actual_date

        indexes = {
            "stock_code": find_field_index(
                fields, ("代號", "證券代號"), market=self.market
            ),
            "stock_name": find_field_index(
                fields, ("名稱", "證券名稱"), market=self.market
            ),
            "margin_previous_balance": find_field_index(
                fields, ("前資餘額(張)", "前資餘額"), market=self.market
            ),
            "margin_buy": find_field_index(
                fields, ("資買",), market=self.market
            ),
            "margin_sell": find_field_index(
                fields, ("資賣",), market=self.market
            ),
            "margin_cash_redemption": find_field_index(
                fields, ("現償",), market=self.market
            ),
            "margin_balance": find_field_index(
                fields, ("資餘額",), market=self.market
            ),
            "margin_utilization": find_field_index(
                fields, ("資使用率(%)", "資使用率"), market=self.market
            ),
            "margin_limit": find_field_index(
                fields, ("資限額",), market=self.market
            ),
            "short_previous_balance": find_field_index(
                fields, ("前券餘額(張)", "前券餘額"), market=self.market
            ),
            "short_sell": find_field_index(
                fields, ("券賣",), market=self.market
            ),
            "short_buy": find_field_index(
                fields, ("券買",), market=self.market
            ),
            "short_stock_redemption": find_field_index(
                fields, ("券償",), market=self.market
            ),
            "short_balance": find_field_index(
                fields, ("券餘額",), market=self.market
            ),
            "offsetting_volume": find_field_index(
                fields, ("資券相抵(張)", "資券相抵"), market=self.market
            ),
        }

        records: list[MarginHistory] = []
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
            if not code:
                self.last_skipped_total_count += 1
                continue
            record = MarginHistory(
                trade_date=actual_date,
                stock_code=code,
                market=self.market,
                margin_buy=parse_non_negative_int(
                    _row_value(row, indexes["margin_buy"], market=self.market, field="margin_buy", row_index=row_index),
                    market=self.market,
                    field="margin_buy",
                    record_index=row_index,
                ),
                margin_sell=parse_non_negative_int(
                    _row_value(row, indexes["margin_sell"], market=self.market, field="margin_sell", row_index=row_index),
                    market=self.market,
                    field="margin_sell",
                    record_index=row_index,
                ),
                margin_cash_redemption=parse_non_negative_int(
                    _row_value(row, indexes["margin_cash_redemption"], market=self.market, field="margin_cash_redemption", row_index=row_index),
                    market=self.market,
                    field="margin_cash_redemption",
                    record_index=row_index,
                ),
                margin_previous_balance=parse_non_negative_int(
                    _row_value(row, indexes["margin_previous_balance"], market=self.market, field="margin_previous_balance", row_index=row_index),
                    market=self.market,
                    field="margin_previous_balance",
                    record_index=row_index,
                ),
                margin_balance=parse_non_negative_int(
                    _row_value(row, indexes["margin_balance"], market=self.market, field="margin_balance", row_index=row_index),
                    market=self.market,
                    field="margin_balance",
                    record_index=row_index,
                ),
                short_buy=parse_non_negative_int(
                    _row_value(row, indexes["short_buy"], market=self.market, field="short_buy", row_index=row_index),
                    market=self.market,
                    field="short_buy",
                    record_index=row_index,
                ),
                short_sell=parse_non_negative_int(
                    _row_value(row, indexes["short_sell"], market=self.market, field="short_sell", row_index=row_index),
                    market=self.market,
                    field="short_sell",
                    record_index=row_index,
                ),
                short_stock_redemption=parse_non_negative_int(
                    _row_value(row, indexes["short_stock_redemption"], market=self.market, field="short_stock_redemption", row_index=row_index),
                    market=self.market,
                    field="short_stock_redemption",
                    record_index=row_index,
                ),
                short_previous_balance=parse_non_negative_int(
                    _row_value(row, indexes["short_previous_balance"], market=self.market, field="short_previous_balance", row_index=row_index),
                    market=self.market,
                    field="short_previous_balance",
                    record_index=row_index,
                ),
                short_balance=parse_non_negative_int(
                    _row_value(row, indexes["short_balance"], market=self.market, field="short_balance", row_index=row_index),
                    market=self.market,
                    field="short_balance",
                    record_index=row_index,
                ),
                offsetting_volume=parse_optional_non_negative_int(
                    _row_value(row, indexes["offsetting_volume"], market=self.market, field="offsetting_volume", row_index=row_index),
                    market=self.market,
                    field="offsetting_volume",
                    record_index=row_index,
                ),
                margin_limit=parse_optional_non_negative_int(
                    _row_value(row, indexes["margin_limit"], market=self.market, field="margin_limit", row_index=row_index),
                    market=self.market,
                    field="margin_limit",
                    record_index=row_index,
                ),
                margin_utilization=parse_optional_non_negative_float(
                    _row_value(row, indexes["margin_utilization"], market=self.market, field="margin_utilization", row_index=row_index),
                    market=self.market,
                    field="margin_utilization",
                    record_index=row_index,
                ),
            )
            validate_margin_record(record, self.market)
            records.append(record)

        if not records:
            raise StockDataValidationError(
                "TPEx margin returned no stock records; refusing to sync."
            )
        logger.info(
            "TPEx margin date=%s returned %s rows; kept %s records",
            actual_date,
            len(rows),
            len(records),
        )
        return records

    @staticmethod
    def _find_data_table(tables: list[dict[str, Any]]) -> dict[str, Any]:
        for table in tables:
            if table.get("fields") and isinstance(table.get("data"), list):
                return table
        raise StockDataValidationError(
            "TPEx margin response schema changed: data table not found."
        )
