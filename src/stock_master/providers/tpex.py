"""TPEx official OTC-stock provider."""

from __future__ import annotations

import logging

from stock_master.config import TPEX_API_URL
from stock_master.exceptions import StockDataValidationError
from stock_master.models import Stock
from stock_master.services.normalizer import normalize_stock, validate_raw_schema
from stock_master.services.stock_filter import StockFilter

from .base import StockProvider
from .http import JsonHttpClient
from .record_utils import ensure_non_empty, payload_records

logger = logging.getLogger(__name__)


class TPExStockProvider(StockProvider):
    """Fetch OTC common stocks from TPEx official company-data API."""

    def __init__(
        self,
        http_client: JsonHttpClient,
        *,
        url: str = TPEX_API_URL,
        stock_filter: StockFilter | None = None,
    ) -> None:
        self.http_client = http_client
        self.url = url
        self.stock_filter = stock_filter or StockFilter(
            allow_official_profile_fallback=True
        )

    def fetch(self) -> list[Stock]:
        logger.info("Starting TPEx fetch")
        payload = self.http_client.get_json(self.url)
        records = payload_records(payload, "TPEx")
        ensure_non_empty(records, "TPEx")
        validate_raw_schema(records, "TPEx")

        stocks: list[Stock] = []
        for record in records:
            candidate = dict(record)
            candidate["_official_dataset"] = "otc_company_basic"
            if not self.stock_filter.is_common_stock(candidate):
                continue
            stocks.append(normalize_stock(candidate, "TPEX"))

        if not stocks:
            raise StockDataValidationError(
                "TPEx returned no valid common stocks after filtering."
            )

        logger.info("TPEx returned %s valid stocks", len(stocks))
        return stocks

