"""TDCC historical shareholding-distribution provider."""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, timedelta
from html import unescape
from html.parser import HTMLParser
from typing import Protocol

from stock_master.config import TDCC_HISTORY_URL
from stock_master.exceptions import StockDataValidationError, StockProviderError
from stock_master.models import TDCCDistribution

from .tdcc import normalize_data_date, normalize_tdcc_record

logger = logging.getLogger(__name__)

_DATE_HEADING_PATTERN = re.compile(
    r"資料日期\s*[：:]\s*(?P<value>"
    r"\d{3,4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"|\d{3,4}\s*[./-]\s*\d{1,2}\s*[./-]\s*\d{1,2}"
    r"|\d{7,8}"
    r")"
)
_STOCK_HEADING_PATTERN = re.compile(
    r"證券代號\s*[：:]\s*(?P<code>[A-Za-z0-9]+)"
)
_NO_DATA_MARKERS = (
    "查無此資料",
    "查無資料",
    "查無符合",
    "沒有資料",
    "無資料",
    "no data",
)
_ADJUSTMENT_MARKER = "差異數調整"
_TOTAL_MARKERS = ("合計", "總計", "total", "grandtotal")
_SESSION_OPEN_ATTEMPTS = 3
_RESULT_SESSION_ATTEMPTS = 2
_RETRYABLE_RESULT_ERROR_MARKERS = (
    "missing data date",
    "missing stock code",
    "missing distribution table",
    "missing form session fields",
    "no available data dates",
)


class TDCCHistoryHttpClient(Protocol):
    """The small HTTP surface required by the historical provider."""

    def get_text(self, url: str) -> str:
        """Fetch a page."""

    def post_form(self, url: str, fields: dict[str, str]) -> str:
        """Submit a form."""


class _TDCCHTMLParser(HTMLParser):
    """Extract the TDCC form metadata and distribution table rows."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hidden_inputs: dict[str, str] = {}
        self.available_date_values: list[str] = []
        self.selected_date_values: list[str] = []
        self.tables: list[list[list[str]]] = []
        self._in_date_select = False
        self._in_option = False
        self._option_value: str | None = None
        self._option_selected = False
        self._option_text: list[str] = []
        self._table_rows: list[list[str]] | None = None
        self._current_row: list[str] | None = None
        self._current_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        tag = tag.casefold()

        if tag == "input":
            name = attributes.get("name")
            if name in {"SYNCHRONIZER_TOKEN", "SYNCHRONIZER_URI", "firDate"}:
                self.hidden_inputs[name] = attributes.get("value") or ""

        if tag == "select" and attributes.get("name") == "scaDate":
            self._in_date_select = True
        elif tag == "option" and self._in_date_select:
            self._in_option = True
            self._option_value = attributes.get("value")
            self._option_selected = "selected" in attributes
            self._option_text = []

        if tag == "table" and self._table_rows is None:
            self._table_rows = []
        elif tag == "tr" and self._table_rows is not None:
            self._current_row = []
        elif (
            tag in {"th", "td"}
            and self._table_rows is not None
            and self._current_row is not None
        ):
            self._current_cell = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()

        if tag in {"th", "td"} and self._current_cell is not None:
            if self._current_row is not None:
                self._current_row.append(_clean_cell(self._current_cell))
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            if self._table_rows is not None:
                self._table_rows.append(self._current_row)
            self._current_row = None
        elif tag == "table" and self._table_rows is not None:
            self.tables.append(self._table_rows)
            self._table_rows = None
        elif tag == "option" and self._in_option:
            value = self._option_value or _clean_cell(self._option_text)
            if value:
                self.available_date_values.append(value)
                if self._option_selected:
                    self.selected_date_values.append(value)
            self._in_option = False
            self._option_value = None
            self._option_selected = False
            self._option_text = []
        elif tag == "select" and self._in_date_select:
            self._in_date_select = False

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell.append(data)
        elif self._in_option:
            self._option_text.append(data)


@dataclass(frozen=True, slots=True)
class TDCCHistoryPage:
    """Parsed TDCC page metadata and, when present, its table."""

    token: str
    uri: str
    first_date: str
    available_dates: tuple[str, ...]
    selected_dates: tuple[str, ...] = ()
    records: tuple[TDCCDistribution, ...] = ()
    skipped_total_count: int = 0
    skipped_adjustment_count: int = 0
    no_data: bool = False


@dataclass(frozen=True, slots=True)
class _WorkerResult:
    records: tuple[TDCCDistribution, ...]
    query_results: tuple[TDCCHistoricalQueryResult, ...]
    skipped_total_count: int
    skipped_adjustment_count: int
    request_count: int


@dataclass(frozen=True, slots=True)
class TDCCHistoricalQueryResult:
    """One successfully completed stock/date query, including no-data results."""

    data_date: str
    stock_code: str
    record_count: int

    @property
    def status(self) -> str:
        return "completed" if self.record_count else "no_data"


def _clean_cell(parts: Sequence[str]) -> str:
    return " ".join("".join(parts).replace("\xa0", " ").split())


def _compact_date(iso_date: str) -> str:
    return iso_date.replace("-", "")


def _normalized_label(value: str) -> str:
    return "".join(value.casefold().split())


def _is_adjustment_label(value: str) -> bool:
    return _ADJUSTMENT_MARKER.casefold() in _normalized_label(value)


def _is_total_label(value: str) -> bool:
    normalized = _normalized_label(value)
    return any(marker.casefold() in normalized for marker in _TOTAL_MARKERS)


def _header_index(headers: Sequence[str], markers: Sequence[str]) -> int | None:
    for index, header in enumerate(headers):
        normalized = _normalized_label(header)
        if any(marker.casefold() in normalized for marker in markers):
            return index
    return None


def _distribution_table_rows(
    tables: Sequence[Sequence[Sequence[str]]],
) -> tuple[Sequence[str], list[Sequence[str]]] | None:
    for table in tables:
        for row_index, row in enumerate(table):
            if _header_index(row, ("持股/單位數分級", "持股分級")) is None:
                continue
            holding_index = _header_index(row, ("持股/單位數分級", "持股分級"))
            shareholder_index = _header_index(row, ("人數",))
            share_index = _header_index(row, ("股數/單位數", "股數"))
            ratio_index = _header_index(row, ("占集保庫存數比例",))
            if None in {
                holding_index,
                shareholder_index,
                share_index,
                ratio_index,
            }:
                raise StockDataValidationError(
                    "TDCC historical response schema changed: distribution table "
                    "is missing a required column."
                )
            # The indexes are guaranteed to be integers after the None check.
            required_index = max(
                holding_index, shareholder_index, share_index, ratio_index
            )
            rows = [
                candidate
                for candidate in table[row_index + 1 :]
                if len(candidate) > required_index
                and any(cell.strip() for cell in candidate)
            ]
            return row, rows
    return None


def _extract_result_date(html: str) -> str:
    # The result page has appeared with both ROC ``115年08月07日`` and
    # slash-separated ``115/08/07`` labels. Search visible text so an HTML
    # element or ``&nbsp;`` between the label and value does not hide a valid
    # date from the parser.
    visible_text = unescape(re.sub(r"<[^>]+>", " ", html)).replace("\xa0", " ")
    match = _DATE_HEADING_PATTERN.search(visible_text)
    if match is None:
        raise StockDataValidationError(
            "TDCC historical response schema changed: missing data date."
        )
    return normalize_data_date(match.group("value"))


def _is_no_data_response(html: str) -> bool:
    """Return whether TDCC rendered its no-result message."""

    visible_text = unescape(re.sub(r"<[^>]+>", " ", html)).replace("\xa0", " ")
    normalized = "".join(visible_text.casefold().split())
    return any(
        "".join(marker.casefold().split()) in normalized
        for marker in _NO_DATA_MARKERS
    )


def _is_retryable_result_error(exc: StockDataValidationError) -> bool:
    message = str(exc).casefold()
    return any(marker in message for marker in _RETRYABLE_RESULT_ERROR_MARKERS)


def _normalize_date_values(raw_values: Sequence[str]) -> tuple[str, ...]:
    normalized_values: list[str] = []
    for raw_date in raw_values:
        try:
            normalized = normalize_data_date(raw_date)
        except StockDataValidationError as exc:
            raise StockDataValidationError(
                f"TDCC historical response has invalid available date {raw_date!r}."
            ) from exc
        if normalized not in normalized_values:
            normalized_values.append(normalized)
    return tuple(normalized_values)


def parse_history_page(
    html: str,
    *,
    expected_date: str | None = None,
    expected_stock_code: str | None = None,
) -> TDCCHistoryPage:
    """Parse a TDCC form page or a submitted stock-result page."""

    parser = _TDCCHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError) as exc:
        raise StockDataValidationError(
            f"Could not parse TDCC historical HTML: {exc}"
        ) from exc

    token = parser.hidden_inputs.get("SYNCHRONIZER_TOKEN", "").strip()
    uri = parser.hidden_inputs.get("SYNCHRONIZER_URI", "").strip()
    first_date_raw = parser.hidden_inputs.get("firDate", "").strip()
    if not token or not uri or not first_date_raw:
        raise StockDataValidationError(
            "TDCC historical response schema changed: missing form session fields."
        )

    first_date = normalize_data_date(first_date_raw)
    available_dates = _normalize_date_values(parser.available_date_values)
    if not available_dates:
        raise StockDataValidationError(
            "TDCC historical response schema changed: no available data dates."
        )

    if _is_no_data_response(html):
        return TDCCHistoryPage(
            token=token,
            uri=uri,
            first_date=first_date,
            available_dates=available_dates,
            selected_dates=_normalize_date_values(parser.selected_date_values),
            no_data=True,
        )

    # The first GET only supplies the date selector and the CSRF session
    # fields. It intentionally has no result heading or distribution table.
    if expected_date is None and expected_stock_code is None:
        return TDCCHistoryPage(
            token=token,
            uri=uri,
            first_date=first_date,
            available_dates=available_dates,
            selected_dates=_normalize_date_values(parser.selected_date_values),
        )

    selected_dates = _normalize_date_values(parser.selected_date_values)
    try:
        actual_date = _extract_result_date(html)
    except StockDataValidationError:
        if expected_date is None or expected_date not in selected_dates:
            raise
        # TDCC occasionally omits the visible date heading while retaining the
        # selected form option and a valid result table. The submitted date and
        # selected option independently agree, so use that value safely.
        logger.warning(
            "TDCC historical result omitted its display date; using selected "
            "date %s",
            expected_date,
        )
        actual_date = expected_date
    if expected_date is not None:
        if selected_dates and expected_date not in selected_dates:
            raise StockDataValidationError(
                "TDCC historical response selected an unexpected data date: "
                f"expected {expected_date}, got {', '.join(selected_dates)}."
            )
        if actual_date != expected_date:
            if selected_dates and expected_date in selected_dates:
                logger.warning(
                    "TDCC historical display date %s disagrees with selected "
                    "date %s; using selected date",
                    actual_date,
                    expected_date,
                )
                actual_date = expected_date
            else:
                raise StockDataValidationError(
                    "TDCC historical response returned an unexpected data date: "
                    f"expected {expected_date}, got {actual_date}."
                )

    if expected_stock_code is not None:
        stock_match = _STOCK_HEADING_PATTERN.search(html)
        if stock_match is None:
            raise StockDataValidationError(
                "TDCC historical response schema changed: missing stock code."
            )
        if stock_match.group("code").strip() != expected_stock_code:
            raise StockDataValidationError(
                "TDCC historical response returned an unexpected stock code: "
                f"expected {expected_stock_code}, got {stock_match.group('code')}."
            )

    table = _distribution_table_rows(parser.tables)
    if table is None:
        raise StockDataValidationError(
            "TDCC historical response schema changed: missing distribution table."
        )
    headers, rows = table
    holding_index = _header_index(headers, ("持股/單位數分級", "持股分級"))
    shareholder_index = _header_index(headers, ("人數",))
    share_index = _header_index(headers, ("股數/單位數", "股數"))
    ratio_index = _header_index(headers, ("占集保庫存數比例",))
    # _distribution_table_rows already validates these indexes.
    assert (
        holding_index is not None
        and shareholder_index is not None
        and share_index is not None
        and ratio_index is not None
    )

    if expected_stock_code is None:
        raise StockDataValidationError(
            "TDCC historical result parsing requires an expected stock code."
        )

    records: list[TDCCDistribution] = []
    skipped_total_count = 0
    skipped_adjustment_count = 0
    for row_index, row in enumerate(rows):
        holding_level = row[holding_index].strip()
        if _is_adjustment_label(holding_level):
            skipped_adjustment_count += 1
            continue
        if _is_total_label(holding_level):
            skipped_total_count += 1
            continue
        raw_record = {
            "資料日期": actual_date,
            "證券代號": expected_stock_code,
            "持股分級": holding_level,
            "人數": row[shareholder_index],
            "股數": row[share_index],
            "占集保庫存數比例 (%)": row[ratio_index],
        }
        normalized = normalize_tdcc_record(raw_record, record_index=row_index)
        if normalized is None:
            skipped_total_count += 1
            continue

        # The bulk CSV uses numeric level identifiers. Use the result row's
        # sequence number so a historical fetch updates the same keys instead
        # of creating a second set of range-label keys for the same date.
        sequence = row[0].strip() if row else ""
        if sequence.isdigit():
            normalized = TDCCDistribution(
                data_date=normalized.data_date,
                stock_code=normalized.stock_code,
                holding_level=sequence,
                shareholder_count=normalized.shareholder_count,
                share_count=normalized.share_count,
                holding_ratio=normalized.holding_ratio,
            )
        records.append(normalized)

    return TDCCHistoryPage(
        token=token,
        uri=uri,
        first_date=first_date,
        available_dates=available_dates,
        selected_dates=selected_dates,
        records=tuple(records),
        skipped_total_count=skipped_total_count,
        skipped_adjustment_count=skipped_adjustment_count,
    )


def _chunk_values(values: Sequence[str], chunk_count: int) -> list[list[str]]:
    chunks = [[] for _ in range(chunk_count)]
    for index, value in enumerate(values):
        chunks[index % chunk_count].append(value)
    return [chunk for chunk in chunks if chunk]


class TDCCHistoricalDistributionProvider:
    """Fetch recent weekly TDCC distributions from the official history page.

    The official historical page accepts one security code per form request.
    Workers therefore use independent cookie/CSRF sessions and process their
    assigned stock codes sequentially, which keeps each session's token chain
    valid while allowing the caller to choose a conservative level of
    concurrency.
    """

    def __init__(
        self,
        client_factory: Callable[[], TDCCHistoryHttpClient]
        | TDCCHistoryHttpClient,
        *,
        url: str = TDCC_HISTORY_URL,
        days: int = 30,
        end_date: date | None = None,
        workers: int = 2,
        request_delay_seconds: float = 0.2,
        newest_first: bool = False,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if days < 1:
            raise ValueError("days must be at least one")
        if workers < 1:
            raise ValueError("workers must be at least one")
        if request_delay_seconds < 0:
            raise ValueError("request_delay_seconds cannot be negative")

        if callable(client_factory):
            self._client_factory = client_factory
        else:
            self._client_factory = lambda: client_factory
        self.url = url
        self.days = days
        self.end_date = end_date or date.today()
        self.start_date = self.end_date - timedelta(days=days)
        self.workers = workers
        self.request_delay_seconds = request_delay_seconds
        self.newest_first = newest_first
        self._sleep = sleep
        self.last_data_dates: tuple[str, ...] = ()
        self.last_skipped_total_count = 0
        self.last_skipped_adjustment_count = 0
        self.last_request_count = 0
        self.last_empty_query_count = 0
        self.last_raw_record_count = 0
        self.last_query_results: tuple[TDCCHistoricalQueryResult, ...] = ()

    def available_dates(self) -> tuple[str, ...]:
        """Return official TDCC dates in this provider's configured window."""

        _client, catalog = self._open_session()
        selected_dates = self._select_dates(catalog.available_dates)
        if not selected_dates:
            raise StockDataValidationError(
                "TDCC historical query has no available dates in the requested "
                f"window {self.start_date.isoformat()}..{self.end_date.isoformat()}."
            )
        self.last_data_dates = selected_dates
        return selected_dates

    def fetch(
        self,
        stock_codes: set[str],
        *,
        completed_queries: set[tuple[str, str]] | None = None,
        selected_dates: Sequence[str] | None = None,
    ) -> list[TDCCDistribution]:
        """Fetch pending weekly stock/date queries in the configured window."""

        valid_stock_codes = sorted(
            {
                str(stock_code).replace("\ufeff", "").strip()
                for stock_code in stock_codes
                if str(stock_code).replace("\ufeff", "").strip()
            }
        )
        self._reset_stats()
        if not valid_stock_codes:
            logger.info("TDCC historical fetch skipped because stock universe is empty")
            return []

        catalog_client, catalog = self._open_session()
        official_dates = self._select_dates(catalog.available_dates)
        if selected_dates is None:
            selected_dates = official_dates
        else:
            selected_dates = tuple(selected_dates)
            invalid_dates = set(selected_dates) - set(official_dates)
            if invalid_dates:
                raise StockDataValidationError(
                    "TDCC historical requested dates are not available in the "
                    "official catalog: " + ", ".join(sorted(invalid_dates))
                )
        if not selected_dates:
            raise StockDataValidationError(
                "TDCC historical query has no available dates in the requested "
                f"window {self.start_date.isoformat()}..{self.end_date.isoformat()}."
            )
        self.last_data_dates = tuple(selected_dates)

        completed = completed_queries or set()
        pending_stock_codes = [
            stock_code
            for stock_code in valid_stock_codes
            if any(
                (data_date, stock_code) not in completed
                for data_date in selected_dates
            )
        ]
        if not pending_stock_codes:
            logger.info(
                "TDCC historical fetch skipped because all %s stock/date queries "
                "are checkpointed",
                len(valid_stock_codes) * len(selected_dates),
            )
            return []

        worker_count = min(self.workers, len(pending_stock_codes))
        chunks = _chunk_values(pending_stock_codes, worker_count)
        logger.info(
            "Starting TDCC historical fetch for %s pending stocks, dates=%s, workers=%s",
            len(pending_stock_codes),
            ",".join(selected_dates),
            len(chunks),
        )

        if len(chunks) == 1:
            worker_results = [
                self._fetch_chunk(
                    chunks[0],
                    selected_dates,
                    client=catalog_client,
                    catalog=catalog,
                    completed_queries=completed,
                )
            ]
        else:
            with ThreadPoolExecutor(max_workers=len(chunks)) as executor:
                futures = [
                    executor.submit(
                        self._fetch_chunk,
                        chunk,
                        selected_dates,
                        completed_queries=completed,
                    )
                    for chunk in chunks
                ]
                worker_results = [future.result() for future in futures]

        records = [
            record
            for result in worker_results
            for record in result.records
        ]
        query_results = tuple(
            query_result
            for result in worker_results
            for query_result in result.query_results
        )
        self.last_skipped_total_count = sum(
            result.skipped_total_count for result in worker_results
        )
        self.last_skipped_adjustment_count = sum(
            result.skipped_adjustment_count for result in worker_results
        )
        self.last_request_count = sum(
            result.request_count for result in worker_results
        )
        self.last_empty_query_count = sum(
            1 for result in query_results if result.record_count == 0
        )
        self.last_raw_record_count = (
            len(records)
            + self.last_skipped_total_count
            + self.last_skipped_adjustment_count
        )
        self.last_query_results = query_results
        records.sort(
            key=lambda record: (
                record.data_date,
                record.stock_code,
                record.holding_level,
            )
        )
        logger.info(
            "TDCC historical returned %s usable records across %s dates; "
            "queries=%s no-data=%s skipped totals=%s adjustments=%s",
            len(records),
            len(selected_dates),
            self.last_request_count,
            self.last_empty_query_count,
            self.last_skipped_total_count,
            self.last_skipped_adjustment_count,
        )
        return records

    def _select_dates(self, available_dates: Sequence[str]) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    available_date
                    for available_date in available_dates
                    if self.start_date.isoformat()
                    <= available_date
                    <= self.end_date.isoformat()
                },
                reverse=self.newest_first,
            )
        )

    def _open_session(
        self,
    ) -> tuple[TDCCHistoryHttpClient, TDCCHistoryPage]:
        """Create a TDCC session, retrying temporary HTML/error pages."""

        for attempt in range(1, _SESSION_OPEN_ATTEMPTS + 1):
            session = self._client_factory()
            try:
                page = parse_history_page(session.get_text(self.url))
                return session, page
            except StockProviderError as caught:
                error: Exception = caught
                retryable = True
            except StockDataValidationError as caught:
                error = caught
                retryable = _is_retryable_result_error(caught)

            if not retryable or attempt >= _SESSION_OPEN_ATTEMPTS:
                raise StockDataValidationError(
                    "TDCC historical session initialization failed after "
                    f"{attempt} attempt(s): {error}"
                ) from error
            delay = max(self.request_delay_seconds, 0.5) * (2 ** (attempt - 1))
            logger.warning(
                "TDCC historical session attempt %s/%s returned an incomplete "
                "page; opening a new session in %.1f seconds: %s",
                attempt,
                _SESSION_OPEN_ATTEMPTS,
                delay,
                error,
            )
            self._sleep(delay)

        raise AssertionError("unreachable TDCC session retry state")

    def _fetch_chunk(
        self,
        stock_codes: Sequence[str],
        selected_dates: Sequence[str],
        *,
        client: TDCCHistoryHttpClient | None = None,
        catalog: TDCCHistoryPage | None = None,
        completed_queries: set[tuple[str, str]] | None = None,
    ) -> _WorkerResult:
        if client is not None and catalog is not None:
            session, page = client, catalog
        else:
            session, page = self._open_session()
        if not set(selected_dates).issubset(page.available_dates):
            raise StockDataValidationError(
                "TDCC historical sessions expose different available dates; "
                "refusing to mix an incomplete date catalog."
            )

        token = page.token
        uri = page.uri
        first_date = page.first_date
        records: list[TDCCDistribution] = []
        query_results: list[TDCCHistoricalQueryResult] = []
        skipped_total_count = 0
        skipped_adjustment_count = 0
        request_count = 0
        completed = completed_queries or set()

        for stock_index, stock_code in enumerate(stock_codes, start=1):
            for data_date in selected_dates:
                if (data_date, stock_code) in completed:
                    continue
                for result_attempt in range(1, _RESULT_SESSION_ATTEMPTS + 1):
                    response_html = session.post_form(
                        self.url,
                        {
                            "method": "submit",
                            "firDate": _compact_date(first_date),
                            "scaDate": _compact_date(data_date),
                            "sqlMethod": "StockNo",
                            "stockNo": stock_code,
                            "stockName": "",
                            "SYNCHRONIZER_URI": uri,
                            "SYNCHRONIZER_TOKEN": token,
                        },
                    )
                    request_count += 1
                    try:
                        response = parse_history_page(
                            response_html,
                            expected_date=data_date,
                            expected_stock_code=stock_code,
                        )
                    except StockDataValidationError as exc:
                        if (
                            result_attempt >= _RESULT_SESSION_ATTEMPTS
                            or not _is_retryable_result_error(exc)
                        ):
                            raise StockDataValidationError(
                                "TDCC historical query failed for "
                                f"stock {stock_code}, date {data_date}: {exc}"
                            ) from exc
                        delay = max(self.request_delay_seconds, 0.5)
                        logger.warning(
                            "TDCC historical result was incomplete for stock %s, "
                            "date %s; refreshing session and retrying in %.1f "
                            "seconds: %s",
                            stock_code,
                            data_date,
                            delay,
                            exc,
                        )
                        self._sleep(delay)
                        session, page = self._open_session()
                        if data_date not in page.available_dates:
                            raise StockDataValidationError(
                                "TDCC refreshed session no longer provides date "
                                f"{data_date}."
                            ) from exc
                        token = page.token
                        uri = page.uri
                        first_date = page.first_date
                        continue
                    break
                token = response.token
                uri = response.uri
                first_date = response.first_date
                query_results.append(
                    TDCCHistoricalQueryResult(
                        data_date=data_date,
                        stock_code=stock_code,
                        record_count=len(response.records),
                    )
                )
                if response.no_data:
                    if self.request_delay_seconds:
                        self._sleep(self.request_delay_seconds)
                    continue

                records.extend(response.records)
                skipped_total_count += response.skipped_total_count
                skipped_adjustment_count += response.skipped_adjustment_count
                if self.request_delay_seconds:
                    self._sleep(self.request_delay_seconds)

            if stock_index == len(stock_codes) or stock_index % 100 == 0:
                logger.info(
                    "TDCC historical progress: %s/%s stocks in worker",
                    stock_index,
                    len(stock_codes),
                )

        return _WorkerResult(
            records=tuple(records),
            query_results=tuple(query_results),
            skipped_total_count=skipped_total_count,
            skipped_adjustment_count=skipped_adjustment_count,
            request_count=request_count,
        )

    def _reset_stats(self) -> None:
        self.last_data_dates = ()
        self.last_skipped_total_count = 0
        self.last_skipped_adjustment_count = 0
        self.last_request_count = 0
        self.last_empty_query_count = 0
        self.last_raw_record_count = 0
        self.last_query_results = ()
