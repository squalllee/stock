"""Official-field-first filtering for ordinary common stocks."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .normalizer import (
    CODE_FIELDS,
    NAME_FIELDS,
    first_value,
)

SECURITY_TYPE_FIELDS = (
    "證券類別",
    "證券種類",
    "商品類別",
    "商品種類",
    "有價證券種類",
    "股票類型",
    "證券別",
    "SecurityType",
    "SecurityCategory",
    "ProductType",
    "Type",
    "type",
)

MARKET_FIELDS = ("市場別", "市場", "Market", "market")

_KNOWN_NON_COMMON_CODES = frozenset({"0050", "0056", "00878", "00919"})
_DEPOSITARY_RECEIPT_PREFIXES = ("91",)
_PROFILE_DATASETS = frozenset({"listed_company_basic", "otc_company_basic"})

_REJECT_MARKERS = (
    "etf",
    "etn",
    "指數股票型基金",
    "指數投資證券",
    "權證",
    "認購權證",
    "認售權證",
    "牛證",
    "熊證",
    "債券",
    "公司債",
    "金融債",
    "政府公債",
    "公債",
    "可轉換公司債",
    "轉換公司債",
    "可轉債",
    "基金",
    "reit",
    "不動產投資信託",
    "受益證券",
    "受益憑證",
    "存託憑證",
    "存託",
    "興櫃",
    "戰略新板",
    "公開發行",
)

_COMMON_MARKERS = (
    "普通股",
    "普通股票",
    "common stock",
    "common",
)

_DR_TOKEN = re.compile(r"(?:^|[-_/()\s])dr(?:$|[-_/()\s])", re.IGNORECASE)


def is_valid_stock_code(code: str) -> bool:
    """Compatibility helper for the secondary four-digit validation rule."""

    return bool(re.fullmatch(r"\d{4}", str(code).strip()))


class StockFilter:
    """Keep only ordinary stocks using official metadata first.

    The two selected official company-basic endpoints are already scoped to
    listed or OTC company stocks, so providers mark those records with an
    internal dataset name. If an explicit security/product category is present,
    it always takes precedence and unknown categories are rejected.
    """

    def __init__(self, *, allow_official_profile_fallback: bool = False) -> None:
        self.allow_official_profile_fallback = allow_official_profile_fallback

    def is_common_stock(self, record: Mapping[str, Any]) -> bool:
        """Return whether a raw record represents an ordinary common stock."""

        code = first_value(record, CODE_FIELDS)
        name = first_value(record, NAME_FIELDS)
        if not is_valid_stock_code(code):
            return False

        if (
            code in _KNOWN_NON_COMMON_CODES
            or code.startswith("00")
            # The Taiwan exchange code namespace 91xx is used for DR products.
            or any(code.startswith(prefix) for prefix in _DEPOSITARY_RECEIPT_PREFIXES)
        ):
            return False

        security_values = [
            first_value(record, (field,)) for field in SECURITY_TYPE_FIELDS
        ]
        security_values = [value for value in security_values if value]
        market_values = [first_value(record, (field,)) for field in MARKET_FIELDS]
        market_values = [value for value in market_values if value]

        text_to_check = " ".join([name, *security_values, *market_values])
        if self._contains_rejection_marker(text_to_check):
            return False

        if security_values:
            # An explicit official category is authoritative. Do not fall back
            # to the code format if the category is not recognized.
            return self._is_explicit_common_category(security_values)

        if any(self._contains_rejection_marker(value) for value in market_values):
            return False

        dataset = str(record.get("_official_dataset", "")).strip()
        return self.allow_official_profile_fallback and dataset in _PROFILE_DATASETS

    @staticmethod
    def _contains_rejection_marker(value: str) -> bool:
        lowered = value.casefold()
        if _DR_TOKEN.search(value):
            return True
        return any(marker.casefold() in lowered for marker in _REJECT_MARKERS)

    @staticmethod
    def _is_explicit_common_category(values: list[str]) -> bool:
        for value in values:
            lowered = value.casefold().strip()
            if StockFilter._contains_rejection_marker(value):
                return False
            if lowered in {"股票", "普通股", "普通股票", "common", "common stock"}:
                return True
            if any(marker.casefold() in lowered for marker in _COMMON_MARKERS):
                return True
        return False

    def filter_records(
        self, records: list[Mapping[str, Any]]
    ) -> list[Mapping[str, Any]]:
        """Filter a raw record list without changing its shape."""

        return [record for record in records if self.is_common_stock(record)]
