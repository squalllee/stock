"""Synchronize one calendar year of local TDCC data to Supabase."""

from __future__ import annotations

import logging
import math
import re
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from stock_master.config import (
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    DEFAULT_SUPABASE_TDCC_BATCH_SIZE,
    TDCC_MAX_HOLDING_LEVEL,
)
from stock_master.exceptions import (
    DatabaseError,
    StockDataValidationError,
    SupabaseSyncError,
)
from stock_master.repositories.connection import connect_sqlite

logger = logging.getLogger(__name__)

_TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_UPSERT_CONFLICT_COLUMNS = "data_date,stock_code,holding_level"


@dataclass(frozen=True, slots=True)
class SupabaseTDCCSyncResult:
    """Summary of one SQLite-to-Supabase TDCC synchronization."""

    year: int
    source_count: int
    synced_count: int
    skipped_count: int
    batch_count: int
    dry_run: bool


def create_supabase_client(url: str, key: str) -> Any:
    """Build the official Supabase Python client without exposing its key."""

    if not isinstance(url, str) or not url.strip():
        raise SupabaseSyncError("SUPABASE_URL must be a non-empty URL.")
    if not isinstance(key, str) or not key.strip():
        raise SupabaseSyncError(
            "Set SUPABASE_SECRET_KEY or SUPABASE_SERVICE_ROLE_KEY before syncing."
        )
    try:
        from supabase import create_client
    except ImportError as exc:
        raise SupabaseSyncError(
            "The Supabase Python client is not installed. "
            "Run python -m pip install -e . first."
        ) from exc

    try:
        return create_client(url.strip().rstrip("/"), key.strip())
    except Exception as exc:
        raise SupabaseSyncError(f"Could not initialize Supabase client: {exc}") from exc


class SupabaseTDCCSyncService:
    """Stream TDCC rows from SQLite and upsert them into Supabase."""

    def __init__(
        self,
        db_path: str | Path,
        supabase_client: Any | None,
        *,
        table_name: str = "tdcc_distributions",
        batch_size: int = DEFAULT_SUPABASE_TDCC_BATCH_SIZE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not _TABLE_NAME_PATTERN.fullmatch(table_name):
            raise ValueError("Supabase table name must be a simple SQL identifier.")
        if not 1 <= batch_size <= 1000:
            raise ValueError("batch_size must be between 1 and 1000.")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one.")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative.")

        self.db_path = Path(db_path)
        self.supabase_client = supabase_client
        self.table_name = table_name
        self.batch_size = batch_size
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self._sleep = sleep

    def sync(
        self,
        *,
        year: int | None = None,
        dry_run: bool = False,
    ) -> SupabaseTDCCSyncResult:
        """Upsert levels 1-15 for ``year``; default to the current year."""

        sync_year = year if year is not None else date.today().year
        if not isinstance(sync_year, int) or isinstance(sync_year, bool):
            raise ValueError("year must be an integer.")
        if not 1 <= sync_year <= 9998:
            raise ValueError("year must be between 1 and 9998.")
        if not dry_run and self.supabase_client is None:
            raise SupabaseSyncError("A Supabase client is required unless --dry-run is used.")

        start_date = date(sync_year, 1, 1).isoformat()
        end_date = date(sync_year + 1, 1, 1).isoformat()
        synced_at = datetime.now(timezone.utc).isoformat()
        source_count = 0
        synced_count = 0
        skipped_count = 0
        batch_count = 0
        batch: list[dict[str, object]] = []

        connection = connect_sqlite(self.db_path, readonly=True)
        try:
            cursor = connection.execute(
                "SELECT data_date, stock_code, holding_level, "
                "shareholder_count, share_count, holding_ratio "
                "FROM tdcc_distributions "
                "WHERE data_date >= ? AND data_date < ? "
                "ORDER BY data_date, stock_code, CAST(holding_level AS INTEGER)",
                (start_date, end_date),
            )
            for row in cursor:
                source_count += 1
                normalized = self._normalize_row(row, sync_year, synced_at)
                if normalized is None:
                    skipped_count += 1
                    continue
                batch.append(normalized)
                if len(batch) < self.batch_size:
                    continue
                batch_count += 1
                if not dry_run:
                    self._upsert_batch(batch, batch_count)
                synced_count += len(batch)
                batch = []

            if batch:
                batch_count += 1
                if not dry_run:
                    self._upsert_batch(batch, batch_count)
                synced_count += len(batch)
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not read TDCC data from SQLite database {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

        if source_count == 0:
            raise StockDataValidationError(
                f"SQLite contains no TDCC data for {sync_year}."
            )
        if synced_count == 0:
            raise StockDataValidationError(
                f"SQLite contains no valid TDCC levels 1-{TDCC_MAX_HOLDING_LEVEL} "
                f"for {sync_year}."
            )

        logger.info(
            "Supabase TDCC sync completed: year=%s source=%s synced=%s "
            "skipped=%s batches=%s dry_run=%s",
            sync_year,
            source_count,
            synced_count,
            skipped_count,
            batch_count,
            dry_run,
        )
        return SupabaseTDCCSyncResult(
            year=sync_year,
            source_count=source_count,
            synced_count=synced_count,
            skipped_count=skipped_count,
            batch_count=batch_count,
            dry_run=dry_run,
        )

    def _upsert_batch(
        self,
        values: list[dict[str, object]],
        batch_number: int,
    ) -> None:
        for attempt in range(1, self.max_attempts + 1):
            try:
                (
                    self.supabase_client.table(self.table_name)
                    .upsert(
                        values,
                        on_conflict=_UPSERT_CONFLICT_COLUMNS,
                        returning="minimal",
                        default_to_null=False,
                    )
                    .execute()
                )
                logger.info(
                    "Supabase TDCC progress: batch=%s rows=%s",
                    batch_number,
                    len(values),
                )
                return
            except Exception as exc:
                if attempt >= self.max_attempts:
                    raise SupabaseSyncError(
                        "Supabase TDCC upsert failed for batch "
                        f"{batch_number} after {attempt} attempt(s): {exc}"
                    ) from exc
                delay = self.backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "Supabase TDCC batch %s failed on attempt %s/%s; retrying "
                    "in %.1f seconds: %s",
                    batch_number,
                    attempt,
                    self.max_attempts,
                    delay,
                    exc,
                )
                if delay:
                    self._sleep(delay)

    @staticmethod
    def _normalize_row(
        row: Mapping[str, object],
        expected_year: int,
        synced_at: str,
    ) -> dict[str, object] | None:
        raw_date = row["data_date"]
        if not isinstance(raw_date, str):
            raise StockDataValidationError("TDCC data_date must be text in SQLite.")
        try:
            parsed_date = date.fromisoformat(raw_date)
        except ValueError as exc:
            raise StockDataValidationError(
                f"Invalid SQLite TDCC data_date {raw_date!r}."
            ) from exc
        if parsed_date.year != expected_year:
            raise StockDataValidationError(
                f"TDCC row {raw_date} is outside requested year {expected_year}."
            )

        stock_code = str(row["stock_code"]).strip()
        if not stock_code:
            raise StockDataValidationError("TDCC stock_code must be non-empty.")

        raw_level = str(row["holding_level"]).strip()
        if not raw_level.isdigit():
            return None
        holding_level = int(raw_level)
        if not 1 <= holding_level <= TDCC_MAX_HOLDING_LEVEL:
            return None

        shareholder_count = SupabaseTDCCSyncService._non_negative_integer(
            row["shareholder_count"], "shareholder_count"
        )
        share_count = SupabaseTDCCSyncService._non_negative_integer(
            row["share_count"], "share_count"
        )
        holding_ratio = row["holding_ratio"]
        if (
            isinstance(holding_ratio, bool)
            or not isinstance(holding_ratio, (int, float))
            or not math.isfinite(float(holding_ratio))
            or not 0 <= float(holding_ratio) <= 100
        ):
            raise StockDataValidationError(
                f"Invalid TDCC holding_ratio {holding_ratio!r}."
            )

        return {
            "data_date": parsed_date.isoformat(),
            "stock_code": stock_code,
            "holding_level": holding_level,
            "shareholder_count": shareholder_count,
            "share_count": share_count,
            "holding_ratio": float(holding_ratio),
            "updated_at": synced_at,
        }

    @staticmethod
    def _non_negative_integer(value: object, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise StockDataValidationError(
                f"TDCC {field} must be a non-negative integer."
            )
        return value
