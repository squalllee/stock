"""SQLite repository for daily margin-trading history."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from stock_master.exceptions import DatabaseError
from stock_master.models import MarginHistory

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS margin_history (
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('TWSE', 'TPEX')),

    margin_buy INTEGER NOT NULL,
    margin_sell INTEGER NOT NULL,
    margin_cash_redemption INTEGER NOT NULL,
    margin_previous_balance INTEGER NOT NULL,
    margin_balance INTEGER NOT NULL,

    short_buy INTEGER NOT NULL,
    short_sell INTEGER NOT NULL,
    short_stock_redemption INTEGER NOT NULL,
    short_previous_balance INTEGER NOT NULL,
    short_balance INTEGER NOT NULL,

    offsetting_volume INTEGER,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (trade_date, stock_code),
    FOREIGN KEY (stock_code) REFERENCES stocks(stock_code)
);

CREATE INDEX IF NOT EXISTS idx_margin_stock_code
ON margin_history(stock_code);

CREATE INDEX IF NOT EXISTS idx_margin_trade_date
ON margin_history(trade_date);

CREATE INDEX IF NOT EXISTS idx_margin_stock_date
ON margin_history(stock_code, trade_date);
"""

UPSERT_SQL = """
INSERT INTO margin_history (
    trade_date,
    stock_code,
    market,
    margin_buy,
    margin_sell,
    margin_cash_redemption,
    margin_previous_balance,
    margin_balance,
    short_buy,
    short_sell,
    short_stock_redemption,
    short_previous_balance,
    short_balance,
    offsetting_volume
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(trade_date, stock_code)
DO UPDATE SET
    market = excluded.market,
    margin_buy = excluded.margin_buy,
    margin_sell = excluded.margin_sell,
    margin_cash_redemption = excluded.margin_cash_redemption,
    margin_previous_balance = excluded.margin_previous_balance,
    margin_balance = excluded.margin_balance,
    short_buy = excluded.short_buy,
    short_sell = excluded.short_sell,
    short_stock_redemption = excluded.short_stock_redemption,
    short_previous_balance = excluded.short_previous_balance,
    short_balance = excluded.short_balance,
    offsetting_volume = excluded.offsetting_volume,
    updated_at = CURRENT_TIMESTAMP;
"""

_VALID_MARKETS = frozenset({"TWSE", "TPEX"})


class MarginHistoryRepository:
    """Persist official margin facts without deleting historical rows."""

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
        """Create the margin table and indexes without changing rows."""

        connection = self._connect()
        try:
            with connection:
                connection.executescript(SCHEMA_SQL)
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not create margin SQLite schema in {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def upsert(self, margin: MarginHistory) -> "MarginRepositorySyncStats":
        """Upsert one margin history record."""

        return self.upsert_many([margin])

    def upsert_many(
        self, margins: Iterable[MarginHistory]
    ) -> "MarginRepositorySyncStats":
        """Upsert one date's records in one transaction."""

        values = list(margins)
        self._validate_margins(values)
        self.create_tables()
        if not values:
            return MarginRepositorySyncStats(inserted_count=0, updated_count=0)

        keys = [(item.trade_date, item.stock_code) for item in values]
        if len(keys) != len(set(keys)):
            raise DatabaseError(
                "Duplicate margin key in one upsert batch: (trade_date, stock_code)."
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
                            item.market,
                            item.margin_buy,
                            item.margin_sell,
                            item.margin_cash_redemption,
                            item.margin_previous_balance,
                            item.margin_balance,
                            item.short_buy,
                            item.short_sell,
                            item.short_stock_redemption,
                            item.short_previous_balance,
                            item.short_balance,
                            item.offsetting_volume,
                        )
                        for item in values
                    ],
                )
            return MarginRepositorySyncStats(
                inserted_count=sum(1 for key in keys if key not in existing_keys),
                updated_count=sum(1 for key in keys if key in existing_keys),
            )
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not update margin SQLite database {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    @staticmethod
    def _existing_keys(
        connection: sqlite3.Connection,
        keys: list[tuple[str, str]],
    ) -> set[tuple[str, str]]:
        existing: set[tuple[str, str]] = set()
        for start in range(0, len(keys), 400):
            chunk = keys[start : start + 400]
            clauses = " OR ".join(
                "(trade_date = ? AND stock_code = ?)" for _ in chunk
            )
            parameters = [value for key in chunk for value in key]
            rows = connection.execute(
                "SELECT trade_date, stock_code FROM margin_history "
                f"WHERE {clauses}",
                parameters,
            )
            existing.update((row["trade_date"], row["stock_code"]) for row in rows)
        return existing

    def get_by_stock_code(self, stock_code: str) -> list[MarginHistory]:
        """Return all dates for one stock in ascending date order."""

        return self._get_many(
            "WHERE stock_code = ? ORDER BY trade_date",
            (stock_code,),
        )

    def get_by_stock_and_date(
        self, stock_code: str, trade_date: str
    ) -> MarginHistory | None:
        """Return one stock's record for one date, if present."""

        values = self._get_many(
            "WHERE stock_code = ? AND trade_date = ?",
            (stock_code, trade_date),
        )
        return values[0] if values else None

    def get_range(
        self,
        start_date: str,
        end_date: str,
        stock_code: str | None = None,
    ) -> list[MarginHistory]:
        """Return records in an inclusive date range, optionally by stock."""

        self._validate_date(start_date, "start_date")
        self._validate_date(end_date, "end_date")
        if start_date > end_date:
            raise DatabaseError("start_date must not be after end_date.")
        if stock_code is None:
            return self._get_many(
                "WHERE trade_date BETWEEN ? AND ? "
                "ORDER BY trade_date, stock_code",
                (start_date, end_date),
            )
        return self._get_many(
            "WHERE stock_code = ? AND trade_date BETWEEN ? AND ? "
            "ORDER BY trade_date",
            (stock_code, start_date, end_date),
        )

    def get_latest_by_stock_code(self, stock_code: str) -> MarginHistory | None:
        """Return the latest record for one stock, if present."""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT trade_date FROM margin_history "
                "WHERE stock_code = ? ORDER BY trade_date DESC LIMIT 1",
                (stock_code,),
            ).fetchone()
            if row is None:
                return None
            value = connection.execute(
                "SELECT * FROM margin_history "
                "WHERE stock_code = ? AND trade_date = ?",
                (stock_code, row["trade_date"]),
            ).fetchone()
            return self._from_row(value) if value is not None else None
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not read latest margin data for {stock_code} "
                f"from {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def get_latest_trade_date(self) -> str | None:
        """Return the latest stored trade date, if any."""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT MAX(trade_date) AS latest_trade_date FROM margin_history"
            ).fetchone()
            return row["latest_trade_date"] if row else None
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not read latest margin trade date from {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def _get_many(
        self, condition: str, parameters: tuple[object, ...]
    ) -> list[MarginHistory]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM margin_history "
                f"{condition}",
                parameters,
            )
            return [self._from_row(row) for row in rows]
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not read margin data from {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> MarginHistory:
        return MarginHistory(
            trade_date=row["trade_date"],
            stock_code=row["stock_code"],
            market=row["market"],
            margin_buy=row["margin_buy"],
            margin_sell=row["margin_sell"],
            margin_cash_redemption=row["margin_cash_redemption"],
            margin_previous_balance=row["margin_previous_balance"],
            margin_balance=row["margin_balance"],
            short_buy=row["short_buy"],
            short_sell=row["short_sell"],
            short_stock_redemption=row["short_stock_redemption"],
            short_previous_balance=row["short_previous_balance"],
            short_balance=row["short_balance"],
            offsetting_volume=row["offsetting_volume"],
        )

    @staticmethod
    def _validate_margins(values: list[MarginHistory]) -> None:
        for item in values:
            if not isinstance(item, MarginHistory):
                raise DatabaseError(
                    "Margin repository accepts MarginHistory instances only."
                )
            MarginHistoryRepository._validate_date(item.trade_date, "trade_date")
            if not isinstance(item.stock_code, str) or not item.stock_code.strip():
                raise DatabaseError("Margin stock_code must be non-empty text.")
            if item.market not in _VALID_MARKETS:
                raise DatabaseError(
                    f"Margin market must be TWSE or TPEX, got {item.market!r}."
                )
            quantities = (
                item.margin_buy,
                item.margin_sell,
                item.margin_cash_redemption,
                item.margin_previous_balance,
                item.margin_balance,
                item.short_buy,
                item.short_sell,
                item.short_stock_redemption,
                item.short_previous_balance,
                item.short_balance,
            )
            if any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                for value in quantities
            ):
                raise DatabaseError(
                    "Margin quantity fields must be non-negative integers."
                )
            if item.offsetting_volume is not None and (
                isinstance(item.offsetting_volume, bool)
                or not isinstance(item.offsetting_volume, int)
                or item.offsetting_volume < 0
            ):
                raise DatabaseError(
                    "Margin offsetting_volume must be a non-negative integer or NULL."
                )

    @staticmethod
    def _validate_date(value: str, field: str) -> None:
        if not isinstance(value, str):
            raise DatabaseError(f"Margin {field} must be an ISO date string.")
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise DatabaseError(
                f"Invalid margin {field} {value!r}; expected YYYY-MM-DD."
            ) from exc


@dataclass(frozen=True, slots=True)
class MarginRepositorySyncStats:
    """Insert/update counts for one margin transaction."""

    inserted_count: int
    updated_count: int
