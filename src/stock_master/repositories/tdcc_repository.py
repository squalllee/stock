"""SQLite repository for TDCC shareholding-distribution history."""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from stock_master.exceptions import DatabaseError
from stock_master.models import TDCCDistribution

_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tdcc_distributions (
    data_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    holding_level TEXT NOT NULL,
    shareholder_count INTEGER NOT NULL,
    share_count INTEGER NOT NULL,
    holding_ratio REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (data_date, stock_code, holding_level),
    FOREIGN KEY (stock_code) REFERENCES stocks(stock_code)
);

CREATE INDEX IF NOT EXISTS idx_tdcc_stock_code
ON tdcc_distributions(stock_code);

CREATE INDEX IF NOT EXISTS idx_tdcc_data_date
ON tdcc_distributions(data_date);

CREATE INDEX IF NOT EXISTS idx_tdcc_stock_date
ON tdcc_distributions(stock_code, data_date);
"""

UPSERT_SQL = """
INSERT INTO tdcc_distributions (
    data_date,
    stock_code,
    holding_level,
    shareholder_count,
    share_count,
    holding_ratio
)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(data_date, stock_code, holding_level)
DO UPDATE SET
    shareholder_count = excluded.shareholder_count,
    share_count = excluded.share_count,
    holding_ratio = excluded.holding_ratio,
    updated_at = CURRENT_TIMESTAMP;
"""


class TDCCDistributionRepository:
    """Persist TDCC records without deleting historical rows."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.db_path)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseError(
                f"Could not open SQLite database {self.db_path}: {exc}"
            ) from exc

    def create_tables(self) -> None:
        """Create the TDCC table and indexes without changing existing rows."""

        connection = self._connect()
        try:
            with connection:
                connection.executescript(SCHEMA_SQL)
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not create TDCC SQLite schema in {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def upsert(self, distribution: TDCCDistribution) -> "TDCCRepositorySyncStats":
        """Upsert one distribution record."""

        return self.upsert_many([distribution])

    def upsert_many(
        self, distributions: Iterable[TDCCDistribution]
    ) -> "TDCCRepositorySyncStats":
        """Upsert all records in one transaction and return insert/update counts."""

        values = list(distributions)
        self._validate_distributions(values)
        self.create_tables()
        if not values:
            return TDCCRepositorySyncStats(inserted_count=0, updated_count=0)

        keys = [
            (item.data_date, item.stock_code, item.holding_level)
            for item in values
        ]
        if len(keys) != len(set(keys)):
            raise DatabaseError(
                "Duplicate TDCC key in one upsert batch: "
                "(data_date, stock_code, holding_level)."
            )

        connection = self._connect()
        try:
            with connection:
                existing_keys = self._existing_keys(connection, keys)
                connection.executemany(
                    UPSERT_SQL,
                    [
                        (
                            item.data_date,
                            item.stock_code,
                            item.holding_level,
                            item.shareholder_count,
                            item.share_count,
                            item.holding_ratio,
                        )
                        for item in values
                    ],
                )
            return TDCCRepositorySyncStats(
                inserted_count=sum(
                    1 for key in keys if key not in existing_keys
                ),
                updated_count=sum(1 for key in keys if key in existing_keys),
            )
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not update TDCC SQLite database {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    @staticmethod
    def _existing_keys(
        connection: sqlite3.Connection,
        keys: list[tuple[str, str, str]],
    ) -> set[tuple[str, str, str]]:
        existing: set[tuple[str, str, str]] = set()
        # Three parameters per OR clause; stay below SQLite's usual parameter
        # limit while keeping one read transaction for the whole batch.
        for start in range(0, len(keys), 300):
            chunk = keys[start : start + 300]
            clauses = " OR ".join(
                "(data_date = ? AND stock_code = ? AND holding_level = ?)"
                for _ in chunk
            )
            parameters = [value for key in chunk for value in key]
            rows = connection.execute(
                "SELECT data_date, stock_code, holding_level "
                f"FROM tdcc_distributions WHERE {clauses}",
                parameters,
            )
            existing.update(
                (row["data_date"], row["stock_code"], row["holding_level"])
                for row in rows
            )
        return existing

    def get_by_stock_code(self, stock_code: str) -> list[TDCCDistribution]:
        """Return all historical distributions for one stock."""

        return self._get_many(
            "WHERE stock_code = ? ORDER BY data_date, holding_level",
            (stock_code,),
        )

    def get_data_dates(self) -> list[str]:
        """Return all dates currently stored in ascending order."""

        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT DISTINCT data_date FROM tdcc_distributions "
                "ORDER BY data_date"
            )
            return [row["data_date"] for row in rows]
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not read TDCC data dates from {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def get_by_stock_and_date(
        self, stock_code: str, data_date: str
    ) -> list[TDCCDistribution]:
        """Return all holding levels for one stock and date."""

        return self._get_many(
            "WHERE stock_code = ? AND data_date = ? ORDER BY holding_level",
            (stock_code, data_date),
        )

    def get_latest_by_stock_code(self, stock_code: str) -> list[TDCCDistribution]:
        """Return all holding levels from the latest available date."""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT MAX(data_date) AS latest_date "
                "FROM tdcc_distributions WHERE stock_code = ?",
                (stock_code,),
            ).fetchone()
            if row is None or row["latest_date"] is None:
                return []
            latest_date = row["latest_date"]
            rows = connection.execute(
                "SELECT data_date, stock_code, holding_level, "
                "shareholder_count, share_count, holding_ratio "
                "FROM tdcc_distributions "
                "WHERE stock_code = ? AND data_date = ? "
                "ORDER BY holding_level",
                (stock_code, latest_date),
            )
            return [self._from_row(item) for item in rows]
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not read latest TDCC data for {stock_code} "
                f"from {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def _get_many(
        self, condition: str, parameters: tuple[object, ...]
    ) -> list[TDCCDistribution]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT data_date, stock_code, holding_level, "
                "shareholder_count, share_count, holding_ratio "
                "FROM tdcc_distributions "
                f"{condition}",
                parameters,
            )
            return [self._from_row(row) for row in rows]
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not read TDCC data from {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> TDCCDistribution:
        return TDCCDistribution(
            data_date=row["data_date"],
            stock_code=row["stock_code"],
            holding_level=row["holding_level"],
            shareholder_count=row["shareholder_count"],
            share_count=row["share_count"],
            holding_ratio=row["holding_ratio"],
        )

    @staticmethod
    def _validate_distributions(
        distributions: list[TDCCDistribution],
    ) -> None:
        for item in distributions:
            if not isinstance(item, TDCCDistribution):
                raise DatabaseError(
                    "TDCC repository accepts TDCCDistribution instances only."
                )
            if not isinstance(item.data_date, str) or not _ISO_DATE_PATTERN.fullmatch(
                item.data_date
            ):
                raise DatabaseError(
                    f"Invalid TDCC data_date {item.data_date!r}; expected YYYY-MM-DD."
                )
            try:
                date.fromisoformat(item.data_date)
            except ValueError as exc:
                raise DatabaseError(
                    f"Invalid TDCC data_date {item.data_date!r}."
                ) from exc
            if not isinstance(item.stock_code, str) or not item.stock_code.strip():
                raise DatabaseError("TDCC stock_code must be non-empty text.")
            if not isinstance(item.holding_level, str) or not item.holding_level.strip():
                raise DatabaseError("TDCC holding_level must be non-empty text.")
            if (
                isinstance(item.shareholder_count, bool)
                or not isinstance(item.shareholder_count, int)
                or item.shareholder_count < 0
            ):
                raise DatabaseError(
                    "TDCC shareholder_count must be a non-negative integer."
                )
            if (
                isinstance(item.share_count, bool)
                or not isinstance(item.share_count, int)
                or item.share_count < 0
            ):
                raise DatabaseError(
                    "TDCC share_count must be a non-negative integer."
                )
            if (
                isinstance(item.holding_ratio, bool)
                or not isinstance(item.holding_ratio, (int, float))
                or not math.isfinite(float(item.holding_ratio))
                or not 0 <= float(item.holding_ratio) <= 100
            ):
                raise DatabaseError(
                    "TDCC holding_ratio must be a finite percentage from 0 to 100."
                )


@dataclass(frozen=True, slots=True)
class TDCCRepositorySyncStats:
    """Insert/update counts for a TDCC repository transaction."""

    inserted_count: int
    updated_count: int
