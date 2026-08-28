#!/usr/bin/env python3
"""Fetch official TDCC data and synchronize it to Supabase BillDB.

The latest-feed mode can be executed directly.  The annual mode additionally
uses the project's tested TDCC historical-page client, but still has no
relative imports and can be started from this file without running an
internal service module directly.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sqlite3
import sys
import time
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TDCC_API_URL = "https://openapi.tdcc.com.tw/v1/opendata/1-5"
TDCC_HISTORY_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
BILLDB_SUPABASE_URL = "https://vngtmamxhvcldecesfwh.supabase.co"
SUPABASE_TABLE = "tdcc_distributions"
MAX_HOLDING_LEVEL = 15
DEFAULT_BATCH_SIZE = 500
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 1.0
DEFAULT_USER_AGENT = "tdcc-supabase-sync/1.0"
DEFAULT_HISTORY_WORKERS = 2
DEFAULT_HISTORY_REQUEST_DELAY_SECONDS = 0.2
DEFAULT_HISTORY_CHUNK_SIZE = 100
DEFAULT_DATABASE_PATH = Path("data/stocks.db")
UPSERT_CONFLICT_COLUMNS = "data_date,stock_code,holding_level"

logger = logging.getLogger("tdcc_supabase_sync")

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
_TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TOTAL_MARKERS = ("合計", "總計", "total", "grandtotal")


class SyncError(RuntimeError):
    """An expected, user-actionable synchronization error."""


@dataclass(frozen=True, slots=True)
class TDCCRow:
    """One TDCC row in the shape expected by BillDB."""

    data_date: str
    stock_code: str
    holding_level: int
    shareholder_count: int
    share_count: int
    holding_ratio: float

    def as_payload(self, updated_at: str) -> dict[str, object]:
        return {
            "data_date": self.data_date,
            "stock_code": self.stock_code,
            "holding_level": self.holding_level,
            "shareholder_count": self.shareholder_count,
            "share_count": self.share_count,
            "holding_ratio": self.holding_ratio,
            "updated_at": updated_at,
        }


@dataclass(frozen=True, slots=True)
class FeedStats:
    raw_count: int
    synced_candidate_count: int
    skipped_count: int
    data_dates: tuple[str, ...]
    stock_count: int


@dataclass(frozen=True, slots=True)
class SyncResult:
    feed: FeedStats
    uploaded_count: int
    batch_count: int
    dry_run: bool
    mode: str = "latest"


def _clean_text(value: object) -> str:
    return str(value).replace("\ufeff", "").strip()


def load_stock_codes(db_path: str | Path = DEFAULT_DATABASE_PATH) -> set[str]:
    """Read the project's listed/OTC common-stock universe from SQLite."""

    path = Path(db_path)
    if not path.is_file():
        raise SyncError(
            f"SQLite stock master not found: {path}. "
            "Run .venv/bin/python -m stock_master sync first, "
            "or pass --all-securities."
        )
    try:
        connection = sqlite3.connect(
            f"file:{path.resolve()}?mode=ro",
            uri=True,
        )
        try:
            rows = connection.execute(
                "SELECT stock_code FROM stocks ORDER BY stock_code"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise SyncError(
            f"Could not read stock master from {path}: {exc}. "
            "Run .venv/bin/python -m stock_master sync first."
        ) from exc

    stock_codes = {
        _clean_text(row[0])
        for row in rows
        if row and _clean_text(row[0])
    }
    if not stock_codes:
        raise SyncError(f"SQLite stock master {path} contains no stocks.")
    return stock_codes


def _first_raw_value(record: Mapping[str, Any], fields: Sequence[str]) -> object:
    """Return the first present, non-blank value from a field alias list."""

    for field in fields:
        # The official response currently contains an embedded BOM in the
        # first property name: ``\ufeff資料日期``.
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
        raise SyncError(
            "TDCC response schema changed: "
            f"record {record_index} missing {semantic_name}; "
            f"expected one of {', '.join(fields)}."
        )
    return value


def _build_date(year: int, month: int, day: int, raw: object) -> str:
    # A three-digit year is the Republic of China calendar.
    if year < 1000:
        year += 1911
    # TDCC distributions are modern market data.  This also prevents a
    # malformed ROC value such as ``51150831`` from becoming year 5115 and
    # being written to the historical table.
    if year < 1900 or year > date.today().year + 1:
        raise SyncError(
            f"TDCC returned unexpected data_date {raw!r}; parsed year {year}."
        )
    try:
        return date(year, month, day).isoformat()
    except ValueError as exc:
        raise SyncError(f"TDCC returned invalid data_date {raw!r}.") from exc


def normalize_data_date(value: object) -> str:
    """Normalize ISO, compact, and ROC TDCC dates to ``YYYY-MM-DD``."""

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

    if raw.isdigit() and len(raw) == 7:
        return _build_date(int(raw[:3]), int(raw[3:5]), int(raw[5:]), value)
    if raw.isdigit() and len(raw) == 8:
        return _build_date(int(raw[:4]), int(raw[4:6]), int(raw[6:]), value)

    raise SyncError(
        f"TDCC returned invalid data_date {value!r}; expected ROC or ISO date."
    )


def _parse_non_negative_integer(
    value: object, field: str, record_index: int
) -> int:
    raw = _clean_text(value).replace(",", "").replace("，", "")
    raw = raw.replace(" ", "").replace("\u00a0", "")
    try:
        number = Decimal(raw)
    except (InvalidOperation, ValueError) as exc:
        raise SyncError(
            f"TDCC record {record_index} has invalid {field} {value!r}."
        ) from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise SyncError(
            f"TDCC record {record_index} has non-integer {field} {value!r}."
        )
    result = int(number)
    if result < 0:
        raise SyncError(
            f"TDCC record {record_index} has negative {field} {value!r}."
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
        raise SyncError(
            f"TDCC record {record_index} has invalid holding_ratio {value!r}."
        ) from exc
    if not number.is_finite():
        raise SyncError(
            f"TDCC record {record_index} has non-finite holding_ratio {value!r}."
        )
    result = float(number)
    if not math.isfinite(result) or not 0 <= result <= 100:
        raise SyncError(
            f"TDCC record {record_index} has out-of-range holding_ratio {value!r}."
        )
    return result


def _holding_level(value: object) -> int | None:
    raw = _clean_text(value)
    if not raw.isdigit():
        return None
    level = int(raw)
    return level if 1 <= level <= MAX_HOLDING_LEVEL else None


def _is_total_label(value: object) -> bool:
    normalized = "".join(_clean_text(value).casefold().split())
    return any(
        marker.casefold() == normalized or marker.casefold() in normalized
        for marker in _TOTAL_MARKERS
    )


def _extract_records(payload: object) -> list[Mapping[str, Any]]:
    """Extract records from the current list response or a common wrapper."""

    candidate = payload
    if isinstance(payload, Mapping):
        for key in ("data", "Data", "result", "results", "records"):
            possible = payload.get(key)
            if isinstance(possible, Sequence) and not isinstance(
                possible, (str, bytes, bytearray)
            ):
                candidate = possible
                break

    if not isinstance(candidate, Sequence) or isinstance(
        candidate, (str, bytes, bytearray)
    ):
        raise SyncError(
            "TDCC response schema changed: expected a JSON array of records."
        )

    records: list[Mapping[str, Any]] = []
    for index, item in enumerate(candidate):
        if not isinstance(item, Mapping):
            raise SyncError(
                "TDCC response schema changed: "
                f"record {index} is not a JSON object."
            )
        records.append(item)
    if not records:
        raise SyncError("TDCC returned an empty distribution list; refusing to sync.")
    return records


def normalize_tdcc_record(
    record: Mapping[str, Any], *, record_index: int = 0
) -> TDCCRow | None:
    """Normalize one official record; return ``None`` outside levels 1-15."""

    if not isinstance(record, Mapping):
        raise SyncError(
            f"TDCC response schema changed: record {record_index} is not an object."
        )

    data_date = normalize_data_date(
        _required_value(record, DATA_DATE_FIELDS, "data_date", record_index)
    )
    stock_code = _clean_text(
        _required_value(record, STOCK_CODE_FIELDS, "stock_code", record_index)
    )
    if not stock_code:
        raise SyncError(f"TDCC record {record_index} has an empty stock_code.")

    raw_level = _clean_text(
        _required_value(record, HOLDING_LEVEL_FIELDS, "holding_level", record_index)
    )
    if not raw_level:
        raise SyncError(f"TDCC record {record_index} has an empty holding_level.")
    if _is_total_label(raw_level):
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
            record,
            HOLDING_RATIO_FIELDS,
            "holding_ratio",
            record_index,
        ),
        record_index,
    )

    level = _holding_level(raw_level)
    if level is None:
        return None
    return TDCCRow(
        data_date=data_date,
        stock_code=stock_code,
        holding_level=level,
        shareholder_count=shareholder_count,
        share_count=share_count,
        holding_ratio=holding_ratio,
    )


def normalize_tdcc_payload(
    payload: object, *, year: int | None = None
) -> tuple[list[TDCCRow], FeedStats]:
    """Validate and normalize a bulk response before any database write."""

    records = _extract_records(payload)
    rows: list[TDCCRow] = []
    rows_by_key: dict[tuple[str, str, int], TDCCRow] = {}
    dates: set[str] = set()
    skipped_count = 0

    for index, record in enumerate(records):
        normalized = normalize_tdcc_record(record, record_index=index)
        if normalized is None:
            skipped_count += 1
            continue
        dates.add(normalized.data_date)
        if year is not None and date.fromisoformat(normalized.data_date).year != year:
            skipped_count += 1
            continue

        key = (
            normalized.data_date,
            normalized.stock_code,
            normalized.holding_level,
        )
        previous = rows_by_key.get(key)
        if previous is not None:
            if previous != normalized:
                raise SyncError(
                    "TDCC response contains conflicting duplicate key "
                    f"{key!r}; refusing to sync."
                )
            skipped_count += 1
            continue
        rows_by_key[key] = normalized
        rows.append(normalized)

    if not rows:
        year_message = f" for year {year}" if year is not None else ""
        raise SyncError(
            "TDCC contained no valid levels 1-15" + year_message + "; refusing to sync."
        )

    stats = FeedStats(
        raw_count=len(records),
        synced_candidate_count=len(rows),
        skipped_count=skipped_count,
        data_dates=tuple(sorted(dates)),
        stock_count=len({row.stock_code for row in rows}),
    )
    return rows, stats


def fetch_json(
    url: str,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> object:
    """Fetch and decode the official JSON feed with bounded retries."""

    if not url.strip():
        raise SyncError("TDCC URL must not be empty.")
    if timeout <= 0:
        raise SyncError("timeout must be greater than zero.")
    if max_attempts < 1:
        raise SyncError("max_attempts must be at least one.")
    if backoff_seconds < 0:
        raise SyncError("backoff_seconds cannot be negative.")

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": DEFAULT_USER_AGENT,
        },
        method="GET",
    )
    for attempt in range(1, max_attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                status = getattr(response, "status", 200)
                if not 200 <= status < 300:
                    raise SyncError(f"TDCC returned unexpected HTTP status {status}.")
                body = response.read()
            try:
                return json.loads(body.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SyncError("TDCC returned invalid JSON.") from exc
        except SyncError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            if attempt >= max_attempts:
                raise SyncError(
                    f"TDCC request failed after {attempt} attempt(s): {url}: {exc}"
                ) from exc
            delay = backoff_seconds * (2 ** (attempt - 1))
            logger.warning(
                "TDCC request failed on attempt %s/%s; retrying in %.1f seconds: %s",
                attempt,
                max_attempts,
                delay,
                exc,
            )
            if delay:
                sleep(delay)

    raise AssertionError("unreachable")


def create_supabase_client(url: str, key: str) -> Any:
    """Create a Supabase client without ever logging the secret key."""

    if not url.strip():
        raise SyncError("Supabase URL must not be empty.")
    if not key.strip():
        raise SyncError(
            "Set SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY before syncing."
        )
    try:
        from supabase import create_client
    except ImportError as exc:
        raise SyncError(
            "The Supabase Python client is not installed. "
            "Run .venv/bin/python -m pip install -e . first."
        ) from exc
    try:
        return create_client(url.strip().rstrip("/"), key.strip())
    except Exception as exc:
        raise SyncError(f"Could not initialize Supabase client: {exc}") from exc


def upsert_rows(
    client: Any,
    rows: Sequence[TDCCRow],
    *,
    table_name: str = SUPABASE_TABLE,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, int]:
    """Upsert normalized rows in bounded batches using the composite key."""

    if not _TABLE_NAME_PATTERN.fullmatch(table_name):
        raise SyncError("Supabase table name must be a simple SQL identifier.")
    if not 1 <= batch_size <= 1000:
        raise SyncError("batch_size must be between 1 and 1000.")
    if max_attempts < 1:
        raise SyncError("max_attempts must be at least one.")
    if backoff_seconds < 0:
        raise SyncError("backoff_seconds cannot be negative.")

    updated_at = datetime.now(timezone.utc).isoformat()
    uploaded_count = 0
    batch_count = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        payload = [row.as_payload(updated_at) for row in batch]
        batch_count += 1
        for attempt in range(1, max_attempts + 1):
            try:
                (
                    client.table(table_name)
                    .upsert(
                        payload,
                        on_conflict=UPSERT_CONFLICT_COLUMNS,
                        returning="minimal",
                        default_to_null=False,
                    )
                    .execute()
                )
                uploaded_count += len(batch)
                logger.info(
                    "Supabase TDCC progress: batch=%s rows=%s/%s",
                    batch_count,
                    uploaded_count,
                    len(rows),
                )
                break
            except Exception as exc:
                if attempt >= max_attempts:
                    raise SyncError(
                        "Supabase TDCC upsert failed for batch "
                        f"{batch_count} after {attempt} attempt(s): {exc}"
                    ) from exc
                delay = backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "Supabase TDCC batch %s failed on attempt %s/%s; "
                    "retrying in %.1f seconds: %s",
                    batch_count,
                    attempt,
                    max_attempts,
                    delay,
                    exc,
                )
                if delay:
                    sleep(delay)
    return uploaded_count, batch_count


def _history_components() -> tuple[Any, Any, Any]:
    """Load the project's tested TDCC historical client on demand."""

    try:
        from stock_master.providers.http import TextHttpClient
        from stock_master.providers.tdcc_history import (
            TDCCHistoricalDistributionProvider,
            parse_history_page,
        )
    except ImportError as exc:
        raise SyncError(
            "Annual TDCC sync requires the project package. "
            "Run .venv/bin/python -m pip install -e . first."
        ) from exc
    return TextHttpClient, TDCCHistoricalDistributionProvider, parse_history_page


def _history_dates(
    *,
    url: str,
    start_date: date,
    end_date: date,
    timeout: float,
    max_attempts: int,
    backoff_seconds: float,
    year: int,
) -> tuple[str, ...]:
    """Read the official date catalog and select the requested calendar year."""

    TextHttpClient, _, parse_history_page = _history_components()
    try:
        catalog_client = TextHttpClient(
            timeout=timeout,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            user_agent=DEFAULT_USER_AGENT,
        )
        catalog = parse_history_page(catalog_client.get_text(url))
    except Exception as exc:
        raise SyncError(f"Could not read TDCC historical date catalog: {exc}") from exc

    selected_dates = tuple(
        sorted(
            {
                available_date
                for available_date in catalog.available_dates
                if start_date.isoformat()
                <= available_date
                <= end_date.isoformat()
            }
        )
    )
    if not selected_dates:
        raise SyncError(
            "TDCC historical page has no available dates in "
            f"{start_date.isoformat()}..{end_date.isoformat()}."
        )
    if date.fromisoformat(selected_dates[0]).year != year:
        raise SyncError(
            "TDCC historical page does not expose the complete requested year "
            f"{year}; earliest available date is {selected_dates[0]}."
        )
    return selected_dates


def _history_provider(
    *,
    url: str,
    start_date: date,
    end_date: date,
    timeout: float,
    max_attempts: int,
    backoff_seconds: float,
    workers: int,
    request_delay_seconds: float,
) -> Any:
    """Build one historical provider for a resumable stock-code chunk."""

    TextHttpClient, TDCCHistoricalDistributionProvider, _ = _history_components()

    def make_client() -> Any:
        return TextHttpClient(
            timeout=timeout,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            user_agent=DEFAULT_USER_AGENT,
        )

    days = max(1, (end_date - start_date).days)
    return TDCCHistoricalDistributionProvider(
        make_client,
        url=url,
        days=days,
        end_date=end_date,
        workers=workers,
        request_delay_seconds=request_delay_seconds,
    )


def _normalize_history_records(
    records: Sequence[Any], *, year: int
) -> tuple[list[TDCCRow], int]:
    """Convert historical provider records and discard levels above 15."""

    rows: list[TDCCRow] = []
    rows_by_key: dict[tuple[str, str, int], TDCCRow] = {}
    skipped_count = 0
    for index, record in enumerate(records):
        raw_level = _clean_text(getattr(record, "holding_level", ""))
        if not raw_level.isdigit():
            skipped_count += 1
            continue
        holding_level = int(raw_level)
        if not 1 <= holding_level <= MAX_HOLDING_LEVEL:
            skipped_count += 1
            continue

        data_date = normalize_data_date(getattr(record, "data_date", ""))
        if date.fromisoformat(data_date).year != year:
            raise SyncError(
                "TDCC historical response returned a row outside requested "
                f"year {year}: {data_date}."
            )
        stock_code = _clean_text(getattr(record, "stock_code", ""))
        if not stock_code:
            raise SyncError(
                f"TDCC historical response record {index} has an empty stock code."
            )

        row = TDCCRow(
            data_date=data_date,
            stock_code=stock_code,
            holding_level=holding_level,
            shareholder_count=int(record.shareholder_count),
            share_count=int(record.share_count),
            holding_ratio=float(record.holding_ratio),
        )
        key = (row.data_date, row.stock_code, row.holding_level)
        previous = rows_by_key.get(key)
        if previous is not None:
            if previous != row:
                raise SyncError(
                    "TDCC historical response contains conflicting duplicate key "
                    f"{key!r}; refusing to sync."
                )
            skipped_count += 1
            continue
        rows_by_key[key] = row
        rows.append(row)
    return rows, skipped_count


def _stock_code_chunks(stock_codes: Sequence[str], chunk_size: int) -> list[list[str]]:
    return [
        list(stock_codes[start : start + chunk_size])
        for start in range(0, len(stock_codes), chunk_size)
    ]


def _restrict_rows_to_stocks(
    rows: Sequence[TDCCRow],
    feed_stats: FeedStats,
    stock_codes: Collection[str],
) -> tuple[list[TDCCRow], FeedStats]:
    """Keep only the stock-master universe in a normalized bulk response."""

    selected_rows = [row for row in rows if row.stock_code in stock_codes]
    if not selected_rows:
        raise SyncError(
            "TDCC returned no rows for the SQLite stock master universe."
        )
    filtered_count = len(rows) - len(selected_rows)
    selected_stats = FeedStats(
        raw_count=feed_stats.raw_count,
        synced_candidate_count=len(selected_rows),
        skipped_count=feed_stats.skipped_count + filtered_count,
        data_dates=feed_stats.data_dates,
        stock_count=len({row.stock_code for row in selected_rows}),
    )
    return selected_rows, selected_stats


def synchronize_historical_year(
    *,
    client: Any | None,
    year: int,
    stock_codes: Collection[str] | None = None,
    tdcc_url: str = TDCC_API_URL,
    history_url: str = TDCC_HISTORY_URL,
    table_name: str = SUPABASE_TABLE,
    batch_size: int = DEFAULT_BATCH_SIZE,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    workers: int = DEFAULT_HISTORY_WORKERS,
    request_delay_seconds: float = DEFAULT_HISTORY_REQUEST_DELAY_SECONDS,
    chunk_size: int = DEFAULT_HISTORY_CHUNK_SIZE,
    dry_run: bool = False,
) -> SyncResult:
    """Fetch every TDCC weekly date available for one calendar year."""

    if year < 1 or year > 9999:
        raise SyncError("year must be between 1 and 9999.")
    if workers < 1:
        raise SyncError("workers must be at least one.")
    if request_delay_seconds < 0:
        raise SyncError("request_delay_seconds cannot be negative.")
    if chunk_size < 1:
        raise SyncError("chunk_size must be at least one.")
    if not 1 <= batch_size <= 1000:
        raise SyncError("batch_size must be between 1 and 1000.")
    if max_attempts < 1:
        raise SyncError("max_attempts must be at least one.")
    if backoff_seconds < 0:
        raise SyncError("backoff_seconds cannot be negative.")
    if not _TABLE_NAME_PATTERN.fullmatch(table_name):
        raise SyncError("Supabase table name must be a simple SQL identifier.")
    if not dry_run and client is None:
        raise SyncError("A Supabase client is required unless --dry-run is used.")

    today = date.today()
    start_date = date(year, 1, 1)
    if start_date > today:
        raise SyncError(f"TDCC cannot provide future year {year}.")
    end_date = min(date(year, 12, 31), today)

    # The bulk endpoint supplies the complete current security-code universe;
    # the historical page then supplies each available week for those codes.
    latest_payload = fetch_json(
        tdcc_url,
        timeout=timeout,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
    )
    latest_rows, _ = normalize_tdcc_payload(latest_payload)
    available_stock_codes = {row.stock_code for row in latest_rows}
    if stock_codes is None:
        selected_stock_codes = sorted(available_stock_codes)
    else:
        requested_stock_codes = {
            _clean_text(stock_code)
            for stock_code in stock_codes
            if _clean_text(stock_code)
        }
        selected_stock_codes = sorted(
            requested_stock_codes & available_stock_codes
        )
        missing_stock_codes = sorted(
            requested_stock_codes - available_stock_codes
        )
        if missing_stock_codes:
            logger.warning(
                "Skipping %s stock code(s) absent from the latest TDCC feed: %s",
                len(missing_stock_codes),
                ", ".join(missing_stock_codes[:20]),
            )
    if not selected_stock_codes:
        raise SyncError("TDCC latest feed returned no stock codes.")

    selected_dates = _history_dates(
        url=history_url,
        start_date=start_date,
        end_date=end_date,
        timeout=timeout,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
        year=year,
    )
    chunks = _stock_code_chunks(selected_stock_codes, chunk_size)
    logger.info(
        "Starting TDCC annual fetch: year=%s stocks=%s dates=%s chunks=%s workers=%s",
        year,
        len(selected_stock_codes),
        len(selected_dates),
        len(chunks),
        workers,
    )

    raw_count = 0
    synced_candidate_count = 0
    skipped_count = 0
    uploaded_count = 0
    batch_count = 0
    data_dates: set[str] = set()

    for chunk_index, stock_chunk in enumerate(chunks, start=1):
        provider = _history_provider(
            url=history_url,
            start_date=start_date,
            end_date=end_date,
            timeout=timeout,
            max_attempts=max_attempts,
            backoff_seconds=backoff_seconds,
            workers=workers,
            request_delay_seconds=request_delay_seconds,
        )
        try:
            records = provider.fetch(set(stock_chunk))
        except Exception as exc:
            raise SyncError(
                f"TDCC historical fetch failed for chunk "
                f"{chunk_index}/{len(chunks)}: {exc}"
            ) from exc

        provider_dates = tuple(provider.last_data_dates)
        if provider_dates != selected_dates:
            raise SyncError(
                "TDCC historical sessions returned inconsistent date catalogs; "
                "refusing to mix incomplete yearly data."
            )
        rows, local_skipped_count = _normalize_history_records(records, year=year)
        chunk_raw_count = max(provider.last_raw_record_count, len(records))
        chunk_skipped_count = max(0, chunk_raw_count - len(records))
        chunk_skipped_count += local_skipped_count
        raw_count += chunk_raw_count
        synced_candidate_count += len(rows)
        skipped_count += chunk_skipped_count
        data_dates.update(row.data_date for row in rows)

        if rows and not dry_run:
            uploaded, batches = upsert_rows(
                client,
                rows,
                table_name=table_name,
                batch_size=batch_size,
                max_attempts=max_attempts,
                backoff_seconds=backoff_seconds,
            )
            uploaded_count += uploaded
            batch_count += batches
        elif rows:
            batch_count += math.ceil(len(rows) / batch_size)

        logger.info(
            "TDCC annual progress: chunk=%s/%s stocks=%s rows=%s",
            chunk_index,
            len(chunks),
            len(stock_chunk),
            synced_candidate_count,
        )

    if not data_dates or synced_candidate_count == 0:
        raise SyncError(f"TDCC returned no usable levels 1-15 for year {year}.")

    feed_stats = FeedStats(
        raw_count=raw_count,
        synced_candidate_count=synced_candidate_count,
        skipped_count=skipped_count,
        data_dates=tuple(sorted(data_dates)),
        stock_count=len(selected_stock_codes),
    )
    return SyncResult(
        feed=feed_stats,
        uploaded_count=uploaded_count,
        batch_count=batch_count,
        dry_run=dry_run,
        mode="historical",
    )


def synchronize(
    *,
    client: Any | None,
    payload: object,
    stock_codes: Collection[str] | None = None,
    year: int | None = None,
    table_name: str = SUPABASE_TABLE,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    dry_run: bool = False,
) -> SyncResult:
    """Validate the feed and upload it, or only validate it in dry-run mode."""

    if not _TABLE_NAME_PATTERN.fullmatch(table_name):
        raise SyncError("Supabase table name must be a simple SQL identifier.")
    if not 1 <= batch_size <= 1000:
        raise SyncError("batch_size must be between 1 and 1000.")
    rows, feed_stats = normalize_tdcc_payload(payload, year=year)
    if stock_codes is not None:
        rows, feed_stats = _restrict_rows_to_stocks(
            rows,
            feed_stats,
            stock_codes,
        )
    if dry_run:
        return SyncResult(
            feed=feed_stats,
            uploaded_count=0,
            batch_count=math.ceil(len(rows) / batch_size),
            dry_run=True,
        )
    if client is None:
        raise SyncError("A Supabase client is required unless --dry-run is used.")
    uploaded_count, batch_count = upsert_rows(
        client,
        rows,
        table_name=table_name,
        batch_size=batch_size,
        max_attempts=max_attempts,
        backoff_seconds=backoff_seconds,
    )
    return SyncResult(
        feed=feed_stats,
        uploaded_count=uploaded_count,
        batch_count=batch_count,
        dry_run=False,
    )


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid integer: {value!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least one")
    return parsed


def _year(value: str) -> int:
    parsed = _positive_int(value)
    if parsed > 9999:
        raise argparse.ArgumentTypeError("year must be at most 9999")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the official TDCC shareholding-distribution feed and "
            "synchronize levels 1-15 to Supabase BillDB."
        )
    )
    parser.add_argument("--tdcc-url", default=TDCC_API_URL)
    parser.add_argument(
        "--supabase-url",
        default=None,
        help="Supabase URL; defaults to SUPABASE_URL or the BillDB project URL",
    )
    parser.add_argument(
        "--table",
        default=SUPABASE_TABLE,
        help=f"Supabase table name (default: {SUPABASE_TABLE})",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help=(
            "SQLite stock master used to keep only TWSE/TPEX common stocks "
            f"(default: {DEFAULT_DATABASE_PATH})"
        ),
    )
    parser.add_argument(
        "--all-securities",
        action="store_true",
        help="include all TDCC securities instead of the SQLite stock master",
    )
    parser.add_argument(
        "--year",
        type=_year,
        default=None,
        help=(
            "sync every available weekly TDCC date in this calendar year; "
            "without it, sync only the latest bulk feed"
        ),
    )
    parser.add_argument(
        "--history-url",
        default=TDCC_HISTORY_URL,
        help="TDCC historical query URL",
    )
    parser.add_argument(
        "--stock-code",
        dest="stock_codes",
        action="append",
        help="limit annual sync to one code; repeat for multiple codes",
    )
    parser.add_argument(
        "--workers",
        type=_positive_int,
        default=DEFAULT_HISTORY_WORKERS,
        help=(
            "parallel historical sessions (default: "
            f"{DEFAULT_HISTORY_WORKERS})"
        ),
    )
    parser.add_argument(
        "--request-delay",
        type=float,
        default=DEFAULT_HISTORY_REQUEST_DELAY_SECONDS,
        help=(
            "seconds between historical requests per session (default: "
            f"{DEFAULT_HISTORY_REQUEST_DELAY_SECONDS})"
        ),
    )
    parser.add_argument(
        "--chunk-size",
        type=_positive_int,
        default=DEFAULT_HISTORY_CHUNK_SIZE,
        help=(
            "stock codes processed per resumable historical chunk (default: "
            f"{DEFAULT_HISTORY_CHUNK_SIZE})"
        ),
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=DEFAULT_BATCH_SIZE,
        help=f"rows per Supabase request, maximum 1000 (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"TDCC request timeout in seconds (default: {DEFAULT_TIMEOUT_SECONDS})",
    )
    parser.add_argument(
        "--max-attempts",
        type=_positive_int,
        default=DEFAULT_MAX_ATTEMPTS,
        help=f"attempts for TDCC/Supabase requests (default: {DEFAULT_MAX_ATTEMPTS})",
    )
    parser.add_argument(
        "--backoff-seconds",
        type=float,
        default=DEFAULT_BACKOFF_SECONDS,
        help=f"initial retry backoff (default: {DEFAULT_BACKOFF_SECONDS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="download and validate official data without connecting to Supabase",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser


def _print_result(result: SyncResult, *, supabase_url: str, table_name: str) -> None:
    feed = result.feed
    print("TDCC Official → Supabase Sync")
    print()
    print(f"Mode                : {result.mode}")
    print(f"TDCC raw rows       : {feed.raw_count}")
    print(f"TDCC stocks         : {feed.stock_count}")
    print(f"Data dates          : {', '.join(feed.data_dates)}")
    print(f"Levels kept         : 1-{MAX_HOLDING_LEVEL}")
    print(f"Rows skipped        : {feed.skipped_count}")
    print(f"Rows in batches     : {feed.synced_candidate_count}")
    print(f"Batches             : {result.batch_count}")
    print(f"Supabase URL        : {supabase_url}")
    print(f"Table               : {table_name}")
    print(f"Dry run             : {'yes' if result.dry_run else 'no'}")
    print()
    if result.dry_run:
        print("Validation completed; no Supabase rows were changed.")
    else:
        print(f"Uploaded/upserted   : {result.uploaded_count}")
        print("TDCC Supabase sync completed successfully.")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(message)s",
    )

    supabase_url = (
        args.supabase_url
        or os.environ.get("SUPABASE_URL")
        or BILLDB_SUPABASE_URL
    )
    try:
        stock_codes = (
            None if args.all_securities else load_stock_codes(args.db)
        )
        client = None
        if not args.dry_run:
            supabase_key = (
                os.environ.get("SUPABASE_SECRET_KEY")
                or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
                or ""
            )
            client = create_supabase_client(supabase_url, supabase_key)
        if args.year is None:
            payload = fetch_json(
                args.tdcc_url,
                timeout=args.timeout,
                max_attempts=args.max_attempts,
                backoff_seconds=args.backoff_seconds,
            )
            result = synchronize(
                client=client,
                payload=payload,
                stock_codes=stock_codes,
                table_name=args.table,
                batch_size=args.batch_size,
                max_attempts=args.max_attempts,
                backoff_seconds=args.backoff_seconds,
                dry_run=args.dry_run,
            )
        else:
            annual_stock_codes = (
                args.stock_codes
                if args.stock_codes is not None
                else stock_codes
            )
            result = synchronize_historical_year(
                client=client,
                year=args.year,
                stock_codes=annual_stock_codes,
                tdcc_url=args.tdcc_url,
                history_url=args.history_url,
                table_name=args.table,
                batch_size=args.batch_size,
                timeout=args.timeout,
                max_attempts=args.max_attempts,
                backoff_seconds=args.backoff_seconds,
                workers=args.workers,
                request_delay_seconds=args.request_delay,
                chunk_size=args.chunk_size,
                dry_run=args.dry_run,
            )
    except SyncError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 1

    _print_result(result, supabase_url=supabase_url, table_name=args.table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
