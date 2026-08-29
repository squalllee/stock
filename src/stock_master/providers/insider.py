"""Official TWSE/TPEx insider share-transfer disclosure providers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from datetime import date
from typing import Any

from stock_master.config import (
    TPEX_INSIDER_TRANSFER_URL,
    TPEX_INSIDER_UNTRANSFERRED_URL,
    TWSE_INSIDER_TRANSFER_URL,
    TWSE_INSIDER_UNTRANSFERRED_URL,
)
from stock_master.exceptions import StockDataValidationError
from stock_master.models import InsiderTransaction
from stock_master.services.normalizer import (
    clean_text,
    first_value,
    is_valid_stock_code,
)

from .http import JsonHttpClient
from .record_utils import payload_records

logger = logging.getLogger(__name__)

_DATE_FIELDS = ("出表日期", "資料日期", "日期", "Date", "report_date")
_CODE_FIELDS = (
    "公司代號",
    "證券代號",
    "股票代號",
    "SecuritiesCompanyCode",
    "stock_code",
)
_NAME_FIELDS = ("公司名稱", "公司簡稱", "CompanyName", "stock_name")
_ROLE_FIELDS = ("申請人身分", "申報人身分", "身分及關係", "insider_role")
_INSIDER_NAME_FIELDS = ("姓名", "申報人姓名", "insider_name")
_METHOD_FIELDS = (
    "預定轉讓方式及股數-轉讓方式",
    "預定轉讓方式",
    "轉讓方式",
    "transfer_method",
)
_TRANSFER_SHARES_FIELDS = (
    "預定轉讓方式及股數-轉讓股數",
    "預定轉讓股數",
    "轉讓股數",
)
_MAX_DAILY_SHARES_FIELDS = ("每日於盤中交易最大得轉讓股數", "每日最大轉讓股數")
_TRANSFEREE_FIELDS = ("受讓人", "transferee")
_CURRENT_OWN_FIELDS = (
    "目前持有股數-自有持股",
    "目前持股-自有持股",
    "目前持有股數",
    "目前持股",
)
_CURRENT_TRUST_FIELDS = (
    "目前持有股數-保留運用決定權信託股數",
    "目前持股-保留運用決定權信託股數",
)
_PLANNED_OWN_FIELDS = ("預定轉讓總股數-自有持股", "原申報預定轉讓股數-自有持股")
_PLANNED_TRUST_FIELDS = (
    "預定轉讓總股數-保留運用決定權信託股數",
    "原申報預定轉讓股數-保留運用決定權信託股數",
)
_AFTER_OWN_FIELDS = ("預定轉讓後持股-自有持股",)
_AFTER_TRUST_FIELDS = ("預定轉讓後持股-保留運用決定權信託股數",)
_UNTRANSFERRED_OWN_FIELDS = ("未轉讓股數-自有持股",)
_UNTRANSFERRED_TRUST_FIELDS = ("未轉讓股數-保留運用決定權信託股數",)
_REASON_FIELDS = ("未轉讓理由", "轉讓理由", "reason")
_PERIOD_FIELDS = ("有效轉讓期間", "轉讓期間", "effective_period")
_MISSING_VALUES = frozenset({"", "-", "--", "—", "－", "無", "NA", "N/A"})


def _parse_optional_integer(value: object) -> int | None:
    """Parse a source share count, returning None for an empty cell."""

    text = clean_text(value).replace(",", "")
    if text.upper() in _MISSING_VALUES:
        return None
    # Some MOPS exports contain a trailing unit or decimal .0.  Keep only the
    # first signed integer/decimal token and reject clearly malformed values.
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None
    if number < 0:
        return None
    return int(number)


def _sum_optional(*values: int | None) -> int | None:
    available = [value for value in values if value is not None]
    return sum(available) if available else None


def _normalize_report_date(value: object) -> str | None:
    """Normalize ROC (YYYMMDD/YYYMMDD with separators) to ISO date."""

    text = clean_text(value)
    if not text:
        return None
    digits = "".join(re.findall(r"\d", text))
    if len(digits) == 7:
        year = int(digits[:3]) + 1911
        month = int(digits[3:5])
        day = int(digits[5:7])
    elif len(digits) == 8:
        year = int(digits[:4])
        month = int(digits[4:6])
        day = int(digits[6:8])
    else:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _source_record_key(
    *, source: str, report_type: str, raw: Mapping[str, Any]
) -> str:
    encoded = json.dumps(
        {
            "source": source,
            "report_type": report_type,
            "raw": dict(raw),
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class InsiderTransferProvider:
    """Fetch one market's current official insider-transfer feed.

    TWSE and TPEx publish the same OpenAPI-style tabular records.  The
    provider deliberately preserves the raw row and only emits rows whose
    stock code belongs to the caller's Supabase stock universe.
    """

    REPORT_TYPE = "planned_transfer"
    TRANSACTION_TYPE = "transfer"

    def __init__(
        self,
        http_client: JsonHttpClient,
        *,
        market: str,
        url: str | None = None,
        report_type: str = REPORT_TYPE,
    ) -> None:
        normalized_market = clean_text(market).upper()
        if normalized_market not in {"TWSE", "TPEX"}:
            raise ValueError(f"Unsupported insider market {market!r}.")
        self.http_client = http_client
        self.market = normalized_market
        self.url = url or (
            TWSE_INSIDER_TRANSFER_URL
            if normalized_market == "TWSE"
            else TPEX_INSIDER_TRANSFER_URL
        )
        self.report_type = report_type
        self.last_report_date: str | None = None
        self.last_raw_record_count = 0
        self.last_skipped_count = 0

    def fetch(self, stock_codes: set[str] | None = None) -> list[InsiderTransaction]:
        """Return normalized rows, restricted to ``stock_codes`` when given."""

        rows = self._fetch_rows()
        allowed = {clean_text(code) for code in (stock_codes or set())}
        source_dates = {
            normalized
            for row in rows
            if (normalized := _normalize_report_date(first_value(row, _DATE_FIELDS)))
        }
        if source_dates:
            self.last_report_date = max(source_dates)
        records: list[InsiderTransaction] = []
        for index, row in enumerate(rows):
            record = self._normalize_row(row, index)
            if record is None:
                self.last_skipped_count += 1
                continue
            if allowed and record.stock_code not in allowed:
                self.last_skipped_count += 1
                continue
            records.append(record)

        logger.info(
            "%s insider %s returned %s raw rows; kept %s Supabase stock rows",
            self.market,
            self.report_type,
            len(rows),
            len(records),
        )
        return records

    def fetch_latest_data_date(self) -> str:
        """Return the newest report date, even when the feed has no records."""

        rows = self._fetch_rows()
        dates = {
            normalized
            for row in rows
            if (normalized := _normalize_report_date(first_value(row, _DATE_FIELDS)))
        }
        if not dates:
            raise StockDataValidationError(
                f"{self.market} insider response has no valid report date."
            )
        self.last_report_date = max(dates)
        return self.last_report_date

    def _fetch_rows(self) -> list[Mapping[str, Any]]:
        self.last_report_date = None
        self.last_raw_record_count = 0
        self.last_skipped_count = 0
        payload = self.http_client.get_json(self.url)
        rows = payload_records(payload, self.market)
        self.last_raw_record_count = len(rows)
        # An empty JSON array is a valid no-disclosure day.  The surrounding
        # sync still records a successful zero-row run instead of poisoning the
        # table with fabricated data.
        return rows

    def _normalize_row(
        self, row: Mapping[str, Any], row_index: int
    ) -> InsiderTransaction | None:
        stock_code = first_value(row, _CODE_FIELDS)
        insider_name = first_value(row, _INSIDER_NAME_FIELDS)
        report_date = _normalize_report_date(first_value(row, _DATE_FIELDS))
        if not stock_code or not insider_name or not report_date:
            return None
        if not is_valid_stock_code(stock_code):
            logger.warning(
                "%s insider row %s has invalid stock code %r; skipping",
                self.market,
                row_index,
                stock_code,
            )
            return None

        current_shares = _sum_optional(
            _parse_optional_integer(first_value(row, _CURRENT_OWN_FIELDS)),
            _parse_optional_integer(first_value(row, _CURRENT_TRUST_FIELDS)),
        )
        planned_shares = _sum_optional(
            _parse_optional_integer(first_value(row, _PLANNED_OWN_FIELDS)),
            _parse_optional_integer(first_value(row, _PLANNED_TRUST_FIELDS)),
        )
        after_shares = _sum_optional(
            _parse_optional_integer(first_value(row, _AFTER_OWN_FIELDS)),
            _parse_optional_integer(first_value(row, _AFTER_TRUST_FIELDS)),
        )

        if self.report_type == "untransferred":
            shares_changed = _sum_optional(
                _parse_optional_integer(first_value(row, _UNTRANSFERRED_OWN_FIELDS)),
                _parse_optional_integer(first_value(row, _UNTRANSFERRED_TRUST_FIELDS)),
            )
            transaction_type = "untransferred"
        else:
            # The daily feeds occasionally concatenate one number per
            # transfer method (for example ``80000008000000``).  The
            # source's planned-total columns are the authoritative aggregate
            # and also reconcile with current/after holdings, so prefer them
            # whenever present and retain the method-specific raw value in
            # ``raw_data`` for auditability.
            direct_transfer_shares = _parse_optional_integer(
                first_value(row, _TRANSFER_SHARES_FIELDS)
            )
            shares_changed = planned_shares
            if shares_changed is None:
                shares_changed = direct_transfer_shares
            transaction_type = self.TRANSACTION_TYPE

        if shares_changed is None:
            # Placeholder rows returned by the official API contain only the
            # report date and empty cells; they are not disclosures.
            return None

        source = f"{self.market.casefold()}_openapi"
        return InsiderTransaction(
            report_date=report_date,
            stock_code=stock_code,
            market=self.market,
            report_type=self.report_type,
            transaction_type=transaction_type,
            insider_name=insider_name,
            insider_role=first_value(row, _ROLE_FIELDS) or "未提供",
            shares_changed=shares_changed,
            source=source,
            source_record_key=_source_record_key(
                source=source,
                report_type=self.report_type,
                raw=row,
            ),
            transfer_method=first_value(row, _METHOD_FIELDS) or None,
            transferee=first_value(row, _TRANSFEREE_FIELDS) or None,
            current_shares=current_shares,
            planned_shares=planned_shares,
            after_shares=after_shares,
            effective_period=first_value(row, _PERIOD_FIELDS) or None,
            reason=first_value(row, _REASON_FIELDS) or None,
            raw_data=dict(row),
        )


class InsiderUntransferredProvider(InsiderTransferProvider):
    """Fetch official notices for planned shares that were not transferred."""

    REPORT_TYPE = "untransferred"
    TRANSACTION_TYPE = "untransferred"

    def __init__(
        self,
        http_client: JsonHttpClient,
        *,
        market: str,
        url: str | None = None,
    ) -> None:
        super().__init__(
            http_client,
            market=market,
            url=url or (
                TWSE_INSIDER_UNTRANSFERRED_URL
                if clean_text(market).upper() == "TWSE"
                else TPEX_INSIDER_UNTRANSFERRED_URL
            ),
            report_type=self.REPORT_TYPE,
        )
