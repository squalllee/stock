"""Official TWSE/TPEx insider share-transfer disclosure providers."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections.abc import Mapping
from calendar import monthrange
from datetime import date
from typing import Any

from stock_master.config import (
    DEFAULT_INSIDER_HISTORY_REQUEST_DELAY_SECONDS,
    MOPS_INSIDER_HOLDINGS_URL,
    TPEX_INSIDER_TRANSFER_URL,
    TPEX_INSIDER_UNTRANSFERRED_URL,
    TWSE_INSIDER_TRANSFER_URL,
    TWSE_INSIDER_UNTRANSFERRED_URL,
)
from stock_master.exceptions import StockDataValidationError, StockProviderError
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


def _is_recoverable_mops_request_error(exc: StockProviderError) -> bool:
    """Return whether one failed MOPS month may be retried on a later run."""

    message = str(exc).casefold()
    if "invalid json response" in message:
        return False
    status_match = re.search(r"http status\s+(\d{3})", message)
    if status_match:
        status = int(status_match.group(1))
        return status == 429 or status >= 500
    return True


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
        seen_source_keys: set[str] = set()
        for index, row in enumerate(rows):
            record = self._normalize_row(row, index)
            if record is None:
                self.last_skipped_count += 1
                continue
            if allowed and record.stock_code not in allowed:
                self.last_skipped_count += 1
                continue
            if record.source_record_key in seen_source_keys:
                # Some official feeds repeat an identical row.  Keep one
                # normalized disclosure so a single upsert batch never sends
                # the same conflict key more than once.
                self.last_skipped_count += 1
                continue
            seen_source_keys.add(record.source_record_key)
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


def _normalize_mops_label(value: object) -> str:
    """Normalize a MOPS title for resilient column matching."""

    return re.sub(r"\s+", "", clean_text(value)).replace("（", "(").replace("）", ")")


def _flatten_mops_titles(titles: object) -> list[str]:
    """Flatten MOPS grouped titles into the corresponding row columns."""

    if not isinstance(titles, list):
        return []
    labels: list[str] = []
    for title in titles:
        if not isinstance(title, Mapping):
            continue
        main = clean_text(title.get("main"))
        sub_titles = title.get("sub")
        if isinstance(sub_titles, list) and sub_titles:
            for sub_title in sub_titles:
                sub = (
                    clean_text(sub_title.get("main"))
                    if isinstance(sub_title, Mapping)
                    else clean_text(sub_title)
                )
                labels.append(f"{main}-{sub}" if main and sub else main or sub)
        else:
            labels.append(main)
    return labels


class InsiderHoldingHistoryProvider:
    """Fetch one company's monthly MOPS insider holding balances.

    MOPS exposes this report through a company-level JSON POST endpoint rather
    than a market-wide OpenAPI feed.  A year therefore consists of one query
    per stock and per published month.  The normalized rows use the existing
    ``after_report`` report type, which is intentionally reserved in the
    insider table for realized monthly holdings.
    """

    REPORT_TYPE = "after_report"

    def __init__(
        self,
        http_client: JsonHttpClient,
        *,
        url: str = MOPS_INSIDER_HOLDINGS_URL,
        request_delay_seconds: float = DEFAULT_INSIDER_HISTORY_REQUEST_DELAY_SECONDS,
        sleep: Any = time.sleep,
    ) -> None:
        if request_delay_seconds < 0:
            raise ValueError("request_delay_seconds cannot be negative")
        self.http_client = http_client
        self.url = url
        self.request_delay_seconds = request_delay_seconds
        self._sleep = sleep
        self.last_query_count = 0
        self.last_no_data_count = 0
        self.last_failed_query_count = 0
        self.last_failed_months: tuple[int, ...] = ()
        self.last_raw_record_count = 0
        self.last_skipped_count = 0
        self.last_record_count = 0

    def fetch_year(
        self,
        stock_code: str,
        market: str,
        year: int,
        *,
        end_month: int | None = None,
    ) -> list[InsiderTransaction]:
        """Return all published monthly holdings for one stock in ``year``."""

        normalized_code = clean_text(stock_code)
        normalized_market = clean_text(market).upper()
        if not is_valid_stock_code(normalized_code):
            raise ValueError(f"Invalid stock code {stock_code!r}.")
        if normalized_market not in {"TWSE", "TPEX"}:
            raise ValueError(f"Unsupported insider market {market!r}.")
        if not isinstance(year, int) or isinstance(year, bool):
            raise ValueError("year must be an integer.")
        if not 1912 <= year <= 9998:
            raise ValueError("year must be between 1912 and 9998.")

        current = date.today()
        last_month = (
            current.month if year == current.year else 12
        ) if end_month is None else end_month
        if not 1 <= last_month <= 12:
            raise ValueError("end_month must be between 1 and 12.")
        if year > current.year or (
            year == current.year and last_month > current.month
        ):
            raise ValueError("Cannot query a future MOPS month.")

        self.last_query_count = 0
        self.last_no_data_count = 0
        self.last_failed_query_count = 0
        self.last_failed_months = ()
        self.last_raw_record_count = 0
        self.last_skipped_count = 0
        self.last_record_count = 0
        records: list[InsiderTransaction] = []
        failed_months: list[int] = []
        for month in range(1, last_month + 1):
            if self.last_query_count and self.request_delay_seconds:
                self._sleep(self.request_delay_seconds)
            try:
                result = self._fetch_month(normalized_code, year, month)
            except StockProviderError as exc:
                if not _is_recoverable_mops_request_error(exc):
                    raise
                self.last_failed_query_count += 1
                failed_months.append(month)
                logger.warning(
                    "MOPS insider holdings %s %s %s-%02d failed after HTTP "
                    "retries; skipping this month so the annual sync can "
                    "continue: %s",
                    normalized_market,
                    normalized_code,
                    year,
                    month,
                    exc,
                )
                continue
            finally:
                self.last_query_count += 1
            if result is None:
                self.last_no_data_count += 1
                continue
            month_records = self._normalize_result(
                result,
                stock_code=normalized_code,
                market=normalized_market,
                year=year,
                month=month,
            )
            records.extend(month_records)

        self.last_record_count = len(records)
        self.last_failed_months = tuple(failed_months)
        logger.info(
            "MOPS insider holdings %s %s returned %s records from %s month queries "
            "(%s months without data, %s failed months)",
            normalized_market,
            normalized_code,
            len(records),
            self.last_query_count,
            self.last_no_data_count,
            self.last_failed_query_count,
        )
        return records

    def _fetch_month(
        self,
        stock_code: str,
        year: int,
        month: int,
    ) -> Mapping[str, Any] | None:
        payload = self.http_client.post_json(
            self.url,
            {
                "companyId": stock_code,
                "dataType": "2",
                "year": str(year - 1911),
                "month": f"{month:02d}",
                "subsidiaryCompanyId": "",
            },
        )
        if not isinstance(payload, Mapping):
            raise StockDataValidationError(
                f"MOPS insider holdings response for {stock_code} {year}-{month:02d} "
                "is not an object."
            )
        try:
            response_code = int(payload.get("code", 0))
        except (TypeError, ValueError):
            response_code = 0
        message = clean_text(payload.get("message"))
        if response_code == 406 or "查無相符資料" in message:
            return None
        if response_code != 200:
            raise StockDataValidationError(
                f"MOPS insider holdings query failed for {stock_code} "
                f"{year}-{month:02d}: {message or 'unknown response'}"
            )
        result = payload.get("result")
        if not isinstance(result, Mapping):
            return None
        result_message = clean_text(result.get("message"))
        if "查無相符資料" in result_message:
            return None
        if "data" not in result:
            raise StockDataValidationError(
                f"MOPS insider holdings response for {stock_code} "
                f"{year}-{month:02d} is missing data."
            )
        rows = result.get("data")
        if not isinstance(rows, list):
            raise StockDataValidationError(
                f"MOPS insider holdings response for {stock_code} "
                f"{year}-{month:02d} has invalid data."
            )
        if not rows:
            return None
        self.last_raw_record_count += len(rows)
        return result

    def _normalize_result(
        self,
        result: Mapping[str, Any],
        *,
        stock_code: str,
        market: str,
        year: int,
        month: int,
    ) -> list[InsiderTransaction]:
        labels = _flatten_mops_titles(result.get("titles"))
        rows = result.get("data")
        if not labels or not isinstance(rows, list):
            raise StockDataValidationError(
                f"MOPS insider holdings schema changed for {stock_code} "
                f"{year}-{month:02d}: missing titles or data."
            )

        report_date = date(year, month, monthrange(year, month)[1]).isoformat()
        records: list[InsiderTransaction] = []
        seen_source_keys: set[str] = set()
        for row_index, row in enumerate(rows):
            record = self._normalize_row(
                row,
                labels,
                stock_code=stock_code,
                market=market,
                report_date=report_date,
                year=year,
                month=month,
                row_index=row_index,
                result=result,
            )
            if record is None:
                self.last_skipped_count += 1
            elif record.source_record_key in seen_source_keys:
                # The MOPS table may repeat a person/role/holding row.  The
                # natural source key below intentionally ignores row order,
                # so collapse repeats before writing the batch.
                self.last_skipped_count += 1
            else:
                seen_source_keys.add(record.source_record_key)
                records.append(record)
        return records

    @staticmethod
    def _field(
        row: list[Any],
        labels: list[str],
        *candidates: str,
    ) -> object | None:
        wanted = {_normalize_mops_label(candidate) for candidate in candidates}
        for index, label in enumerate(labels):
            if _normalize_mops_label(label) in wanted and index < len(row):
                return row[index]
        return None

    @staticmethod
    def _change_total(
        row: list[Any],
        labels: list[str],
        prefix: str,
    ) -> int | None:
        normalized_prefix = _normalize_mops_label(prefix)
        total = 0
        found = False
        for index, label in enumerate(labels):
            normalized_label = _normalize_mops_label(label)
            if not normalized_label.startswith(normalized_prefix):
                continue
            if "設定質權" in normalized_label or "解除質權" in normalized_label:
                continue
            value = _parse_optional_integer(row[index]) if index < len(row) else None
            if value is not None:
                total += value
                found = True
        return total if found else None

    def _normalize_row(
        self,
        row: object,
        labels: list[str],
        *,
        stock_code: str,
        market: str,
        report_date: str,
        year: int,
        month: int,
        row_index: int,
        result: Mapping[str, Any],
    ) -> InsiderTransaction | None:
        if not isinstance(row, list):
            return None
        identity = clean_text(
            self._field(row, labels, "身份別", "身分別")
            or (row[0] if row else "")
        )
        name = clean_text(
            self._field(row, labels, "姓名", "申報人姓名")
            or (row[1] if len(row) > 1 else "")
        )
        holding_type = clean_text(
            self._field(row, labels, "持股種類")
            or (row[2] if len(row) > 2 else "")
        )
        if not identity and not name:
            return None

        previous_shares = _sum_optional(
            _parse_optional_integer(
                self._field(row, labels, "上月實際持有股數")
            ),
            _parse_optional_integer(
                self._field(row, labels, "截至上月底保留運用決定權信託股數")
            ),
        )
        after_shares = _sum_optional(
            _parse_optional_integer(
                self._field(
                    row,
                    labels,
                    "本月實際自有持有股數",
                    "本月實際持有股數",
                )
            ),
            _parse_optional_integer(
                self._field(row, labels, "截至本月底保留運用決定權信託股數")
            ),
        )
        increases = self._change_total(row, labels, "本月增加")
        decreases = self._change_total(row, labels, "本月減少")
        if previous_shares is not None and after_shares is not None:
            net_change = after_shares - previous_shares
        elif increases is not None or decreases is not None:
            net_change = (increases or 0) - (decreases or 0)
        else:
            net_change = None
        if previous_shares is None and after_shares is None and net_change is None:
            return None
        if (
            previous_shares in (None, 0)
            and after_shares in (None, 0)
            and (net_change or 0) == 0
        ):
            return None

        if net_change is None:
            net_change = 0
        transaction_type = (
            "buy" if net_change > 0 else "sell" if net_change < 0 else "other"
        )
        source = f"{market.casefold()}_mops"
        source_key = {
            "stock_code": stock_code,
            "market": market,
            "report_date": report_date,
            "identity": identity,
            "name": name,
            "holding_type": holding_type,
        }
        raw_data = {
            "query_year": year,
            "query_month": month,
            "market_name": clean_text(result.get("marketName")),
            "titles": result.get("titles"),
            "row": row,
        }
        return InsiderTransaction(
            report_date=report_date,
            stock_code=stock_code,
            market=market,
            report_type=self.REPORT_TYPE,
            transaction_type=transaction_type,
            insider_name=name or identity,
            insider_role=identity or "未提供",
            shares_changed=abs(net_change),
            source=source,
            source_record_key=_source_record_key(
                source=source,
                report_type=self.REPORT_TYPE,
                raw=source_key,
            ),
            transfer_method=holding_type or None,
            current_shares=previous_shares,
            planned_shares=None,
            after_shares=after_shares,
            effective_period=f"{year}/{month:02d}",
            reason=None,
            raw_data=raw_data,
        )
