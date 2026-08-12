"""SQLite repository for versioned margin-cost/maintenance estimates."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from stock_master.exceptions import DatabaseError
from stock_master.models import DEFAULT_MARGIN_MODEL_VERSION, MarginEstimate

from .connection import connect_sqlite

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS margin_estimates (
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    estimated_margin_avg_cost REAL NOT NULL,
    margin_financing_ratio REAL NOT NULL,
    estimated_financing_per_share REAL NOT NULL,
    close_price REAL NOT NULL,
    estimated_maintenance_ratio REAL NOT NULL,
    estimated_130_price REAL NOT NULL,
    model_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (trade_date, stock_code, model_version),
    FOREIGN KEY (stock_code) REFERENCES stocks(stock_code)
);

CREATE INDEX IF NOT EXISTS idx_margin_estimate_stock_date
ON margin_estimates(stock_code, trade_date);

CREATE INDEX IF NOT EXISTS idx_margin_estimate_trade_date
ON margin_estimates(trade_date);
"""

UPSERT_SQL = """
INSERT INTO margin_estimates (
    trade_date,
    stock_code,
    estimated_margin_avg_cost,
    margin_financing_ratio,
    estimated_financing_per_share,
    close_price,
    estimated_maintenance_ratio,
    estimated_130_price,
    model_version
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(trade_date, stock_code, model_version)
DO UPDATE SET
    estimated_margin_avg_cost = excluded.estimated_margin_avg_cost,
    margin_financing_ratio = excluded.margin_financing_ratio,
    estimated_financing_per_share = excluded.estimated_financing_per_share,
    close_price = excluded.close_price,
    estimated_maintenance_ratio = excluded.estimated_maintenance_ratio,
    estimated_130_price = excluded.estimated_130_price;
"""


@dataclass(frozen=True, slots=True)
class MarginEstimateRepositorySyncStats:
    """Insert/update counts returned by an estimate UPSERT."""

    inserted_count: int
    updated_count: int


class MarginEstimateRepository:
    """Persist model-versioned estimate outputs independently of raw facts."""

    def __init__(self, db_path: str | Path, *, readonly: bool = False) -> None:
        self.db_path = Path(db_path)
        self.readonly = readonly

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = connect_sqlite(self.db_path, readonly=self.readonly)
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except DatabaseError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseError(
                f"Could not open SQLite database {self.db_path}: {exc}"
            ) from exc

    def create_tables(self) -> None:
        """Create the estimate table and indexes without changing rows."""

        connection = self._connect()
        try:
            with connection:
                connection.executescript(SCHEMA_SQL)
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not create estimate SQLite schema in {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def upsert(self, estimate: MarginEstimate) -> MarginEstimateRepositorySyncStats:
        """Upsert one estimate."""

        return self.upsert_many([estimate])

    def upsert_many(
        self, estimates: Iterable[MarginEstimate]
    ) -> MarginEstimateRepositorySyncStats:
        """Upsert estimates in one transaction."""

        values = list(estimates)
        self._validate_estimates(values)
        self.create_tables()
        if not values:
            return MarginEstimateRepositorySyncStats(inserted_count=0, updated_count=0)
        keys = [
            (item.trade_date, item.stock_code, item.model_version)
            for item in values
        ]
        if len(keys) != len(set(keys)):
            raise DatabaseError(
                "Duplicate estimate key in one batch: "
                "(trade_date, stock_code, model_version)."
            )
        connection = self._connect()
        try:
            with connection:
                existing_keys = self._existing_keys(connection, keys)
                connection.executemany(
                    UPSERT_SQL,
                    [
                        (
                            item.trade_date,
                            item.stock_code,
                            item.estimated_margin_avg_cost,
                            item.margin_financing_ratio,
                            item.estimated_financing_per_share,
                            item.close_price,
                            item.estimated_maintenance_ratio,
                            item.estimated_130_price,
                            item.model_version,
                        )
                        for item in values
                    ],
                )
            return MarginEstimateRepositorySyncStats(
                inserted_count=sum(1 for key in keys if key not in existing_keys),
                updated_count=sum(1 for key in keys if key in existing_keys),
            )
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not update estimate SQLite database {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def get_by_stock_code(
        self,
        stock_code: str,
        model_version: str = DEFAULT_MARGIN_MODEL_VERSION,
    ) -> list[MarginEstimate]:
        """Return one model version's estimates for a stock."""

        return self._get_many(
            "WHERE stock_code = ? AND model_version = ? ORDER BY trade_date",
            (stock_code, model_version),
        )

    def get_by_stock_and_date(
        self,
        stock_code: str,
        trade_date: str,
        model_version: str = DEFAULT_MARGIN_MODEL_VERSION,
    ) -> MarginEstimate | None:
        """Return one versioned estimate, if present."""

        self._validate_date(trade_date, "trade_date")
        values = self._get_many(
            "WHERE stock_code = ? AND trade_date = ? AND model_version = ?",
            (stock_code, trade_date, model_version),
        )
        return values[0] if values else None

    def get_range(
        self,
        start_date: str,
        end_date: str,
        stock_code: str | None = None,
        model_version: str = DEFAULT_MARGIN_MODEL_VERSION,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[MarginEstimate]:
        """Return estimates in an inclusive range."""

        self._validate_date(start_date, "start_date")
        self._validate_date(end_date, "end_date")
        if start_date > end_date:
            raise DatabaseError("start_date must not be after end_date.")
        clause = "WHERE trade_date BETWEEN ? AND ? AND model_version = ?"
        parameters: tuple[object, ...] = (start_date, end_date, model_version)
        order = " ORDER BY trade_date, stock_code"
        if stock_code is not None:
            clause = (
                "WHERE stock_code = ? AND trade_date BETWEEN ? AND ? "
                "AND model_version = ?"
            )
            parameters = (stock_code, start_date, end_date, model_version)
            order = " ORDER BY trade_date"
        clause += order
        clause, parameters = _add_pagination(clause, parameters, limit, offset)
        return self._get_many(clause, parameters)

    def get_latest_trade_date(
        self, model_version: str = DEFAULT_MARGIN_MODEL_VERSION
    ) -> str | None:
        """Return the latest date for one model version, if any."""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT MAX(trade_date) AS latest_trade_date FROM margin_estimates "
                "WHERE model_version = ?",
                (model_version,),
            ).fetchone()
            return row["latest_trade_date"] if row else None
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not read latest estimate date from {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def get_recent_by_stock_code(
        self,
        stock_code: str,
        limit: int = 90,
        model_version: str = DEFAULT_MARGIN_MODEL_VERSION,
    ) -> list[MarginEstimate]:
        """Return the latest versioned estimates in ascending date order."""

        if limit < 1:
            raise DatabaseError("limit must be at least 1.")
        values = self._get_many(
            "WHERE stock_code = ? AND model_version = ? "
            "ORDER BY trade_date DESC LIMIT ?",
            (stock_code, model_version, limit),
        )
        return list(reversed(values))

    @staticmethod
    def _existing_keys(
        connection: sqlite3.Connection,
        keys: list[tuple[str, str, str]],
    ) -> set[tuple[str, str, str]]:
        existing: set[tuple[str, str, str]] = set()
        for start in range(0, len(keys), 300):
            chunk = keys[start : start + 300]
            clauses = " OR ".join(
                "(trade_date = ? AND stock_code = ? AND model_version = ?)"
                for _ in chunk
            )
            parameters = [value for key in chunk for value in key]
            rows = connection.execute(
                "SELECT trade_date, stock_code, model_version FROM margin_estimates "
                f"WHERE {clauses}",
                parameters,
            )
            existing.update(
                (row["trade_date"], row["stock_code"], row["model_version"])
                for row in rows
            )
        return existing

    def _get_many(
        self, condition: str, parameters: tuple[object, ...]
    ) -> list[MarginEstimate]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM margin_estimates " + condition,
                parameters,
            )
            return [self._from_row(row) for row in rows]
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not read estimate data from {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> MarginEstimate:
        return MarginEstimate(
            trade_date=row["trade_date"],
            stock_code=row["stock_code"],
            estimated_margin_avg_cost=row["estimated_margin_avg_cost"],
            margin_financing_ratio=row["margin_financing_ratio"],
            estimated_financing_per_share=row["estimated_financing_per_share"],
            close_price=row["close_price"],
            estimated_maintenance_ratio=row["estimated_maintenance_ratio"],
            estimated_130_price=row["estimated_130_price"],
            model_version=row["model_version"],
        )

    @staticmethod
    def _validate_estimates(values: list[MarginEstimate]) -> None:
        for item in values:
            if not isinstance(item, MarginEstimate):
                raise DatabaseError(
                    "Estimate repository accepts MarginEstimate instances only."
                )
            MarginEstimateRepository._validate_date(item.trade_date, "trade_date")
            if not isinstance(item.stock_code, str) or not item.stock_code.strip():
                raise DatabaseError("Estimate stock_code must be non-empty text.")
            if not isinstance(item.model_version, str) or not item.model_version.strip():
                raise DatabaseError("Estimate model_version must be non-empty text.")
            for name, value in (
                ("estimated_margin_avg_cost", item.estimated_margin_avg_cost),
                ("margin_financing_ratio", item.margin_financing_ratio),
                ("estimated_financing_per_share", item.estimated_financing_per_share),
                ("close_price", item.close_price),
                ("estimated_maintenance_ratio", item.estimated_maintenance_ratio),
                ("estimated_130_price", item.estimated_130_price),
            ):
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or value < 0
                ):
                    raise DatabaseError(
                        f"Estimate {name} must be a finite non-negative number."
                    )
            if not 0 < item.margin_financing_ratio <= 1:
                raise DatabaseError(
                    "Estimate margin_financing_ratio must be greater than 0 and <= 1."
                )

    @staticmethod
    def _validate_date(value: str, field: str) -> None:
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise DatabaseError(
                f"{field} must be an ISO date in YYYY-MM-DD format."
            ) from exc


def _add_pagination(
    clause: str,
    parameters: tuple[object, ...],
    limit: int | None,
    offset: int,
) -> tuple[str, tuple[object, ...]]:
    if offset < 0:
        raise DatabaseError("offset must be non-negative.")
    if limit is not None and limit < 1:
        raise DatabaseError("limit must be at least 1.")
    if limit is None:
        if offset:
            return clause + " LIMIT -1 OFFSET ?", parameters + (offset,)
        return clause, parameters
    return clause + " LIMIT ? OFFSET ?", parameters + (limit, offset)
