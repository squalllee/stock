"""SQLite repository for official daily price history."""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from stock_master.exceptions import DatabaseError
from stock_master.models import PriceHistory

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS price_history (
    trade_date TEXT NOT NULL,
    stock_code TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('TWSE', 'TPEX')),

    trade_volume INTEGER NOT NULL,
    trade_value INTEGER NOT NULL,
    open_price REAL,
    high_price REAL,
    low_price REAL,
    close_price REAL,
    transaction_count INTEGER,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (trade_date, stock_code),
    FOREIGN KEY (stock_code) REFERENCES stocks(stock_code)
);

CREATE INDEX IF NOT EXISTS idx_price_stock_code
ON price_history(stock_code);

CREATE INDEX IF NOT EXISTS idx_price_trade_date
ON price_history(trade_date);

CREATE INDEX IF NOT EXISTS idx_price_stock_date
ON price_history(stock_code, trade_date);
"""

UPSERT_SQL = """
INSERT INTO price_history (
    trade_date,
    stock_code,
    market,
    trade_volume,
    trade_value,
    open_price,
    high_price,
    low_price,
    close_price,
    transaction_count
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(trade_date, stock_code)
DO UPDATE SET
    market = excluded.market,
    trade_volume = excluded.trade_volume,
    trade_value = excluded.trade_value,
    open_price = excluded.open_price,
    high_price = excluded.high_price,
    low_price = excluded.low_price,
    close_price = excluded.close_price,
    transaction_count = excluded.transaction_count,
    updated_at = CURRENT_TIMESTAMP;
"""

_VALID_MARKETS = frozenset({"TWSE", "TPEX"})


@dataclass(frozen=True, slots=True)
class PriceRepositorySyncStats:
    """Insert/update counts returned by a price UPSERT."""

    inserted_count: int
    updated_count: int


class PriceHistoryRepository:
    """Persist normalized daily price facts without deleting history."""

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
        """Create the price table and indexes without changing rows."""

        connection = self._connect()
        try:
            with connection:
                connection.executescript(SCHEMA_SQL)
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not create price SQLite schema in {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def upsert(self, price: PriceHistory) -> PriceRepositorySyncStats:
        """Upsert one daily price record."""

        return self.upsert_many([price])

    def upsert_many(
        self, prices: Iterable[PriceHistory]
    ) -> PriceRepositorySyncStats:
        """Upsert a batch in one transaction."""

        values = list(prices)
        self._validate_prices(values)
        self.create_tables()
        if not values:
            return PriceRepositorySyncStats(inserted_count=0, updated_count=0)

        keys = [(item.trade_date, item.stock_code) for item in values]
        if len(keys) != len(set(keys)):
            raise DatabaseError(
                "Duplicate price key in one upsert batch: (trade_date, stock_code)."
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
                            item.trade_volume,
                            item.trade_value,
                            item.open_price,
                            item.high_price,
                            item.low_price,
                            item.close_price,
                            item.transaction_count,
                        )
                        for item in values
                    ],
                )
            return PriceRepositorySyncStats(
                inserted_count=sum(1 for key in keys if key not in existing_keys),
                updated_count=sum(1 for key in keys if key in existing_keys),
            )
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not update price SQLite database {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def get_by_stock_code(self, stock_code: str) -> list[PriceHistory]:
        """Return one stock's price history in ascending date order."""

        return self._get_many(
            "WHERE stock_code = ? ORDER BY trade_date",
            (stock_code,),
        )

    def get_by_stock_and_date(
        self, stock_code: str, trade_date: str
    ) -> PriceHistory | None:
        """Return one stock's daily price, if present."""

        self._validate_date(trade_date, "trade_date")
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
    ) -> list[PriceHistory]:
        """Return an inclusive date range, optionally filtered by stock."""

        self._validate_date(start_date, "start_date")
        self._validate_date(end_date, "end_date")
        if start_date > end_date:
            raise DatabaseError("start_date must not be after end_date.")
        if stock_code is None:
            return self._get_many(
                "WHERE trade_date BETWEEN ? AND ? ORDER BY trade_date, stock_code",
                (start_date, end_date),
            )
        return self._get_many(
            "WHERE stock_code = ? AND trade_date BETWEEN ? AND ? "
            "ORDER BY trade_date",
            (stock_code, start_date, end_date),
        )

    def get_latest_by_stock_code(self, stock_code: str) -> PriceHistory | None:
        """Return the latest price for one stock, if present."""

        values = self._get_many(
            "WHERE stock_code = ? ORDER BY trade_date DESC LIMIT 1",
            (stock_code,),
        )
        return values[0] if values else None

    def get_latest_trade_date(self) -> str | None:
        """Return the latest stored trading date, if any."""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT MAX(trade_date) AS latest_trade_date FROM price_history"
            ).fetchone()
            return row["latest_trade_date"] if row else None
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not read latest price trade date from {self.db_path}: {exc}"
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
                "SELECT trade_date, stock_code FROM price_history "
                f"WHERE {clauses}",
                parameters,
            )
            existing.update((row["trade_date"], row["stock_code"]) for row in rows)
        return existing

    def _get_many(
        self, condition: str, parameters: tuple[object, ...]
    ) -> list[PriceHistory]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM price_history " + condition,
                parameters,
            )
            return [self._from_row(row) for row in rows]
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not read price data from {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    @staticmethod
    def _from_row(row: sqlite3.Row) -> PriceHistory:
        return PriceHistory(
            trade_date=row["trade_date"],
            stock_code=row["stock_code"],
            market=row["market"],
            trade_volume=row["trade_volume"],
            trade_value=row["trade_value"],
            open_price=row["open_price"],
            high_price=row["high_price"],
            low_price=row["low_price"],
            close_price=row["close_price"],
            transaction_count=row["transaction_count"],
        )

    @staticmethod
    def _validate_prices(values: list[PriceHistory]) -> None:
        for item in values:
            if not isinstance(item, PriceHistory):
                raise DatabaseError("Price repository accepts PriceHistory instances only.")
            PriceHistoryRepository._validate_date(item.trade_date, "trade_date")
            if not isinstance(item.stock_code, str) or not item.stock_code.strip():
                raise DatabaseError("Price stock_code must be non-empty text.")
            if item.market not in _VALID_MARKETS:
                raise DatabaseError(
                    f"Price market must be TWSE or TPEX, got {item.market!r}."
                )
            for name, value in (
                ("trade_volume", item.trade_volume),
                ("trade_value", item.trade_value),
            ):
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise DatabaseError(f"Price {name} must be a non-negative integer.")
            for name, value in (
                ("open_price", item.open_price),
                ("high_price", item.high_price),
                ("low_price", item.low_price),
                ("close_price", item.close_price),
            ):
                if value is not None and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or value < 0
                ):
                    raise DatabaseError(
                        f"Price {name} must be a finite non-negative number or None."
                    )
            if item.transaction_count is not None and (
                isinstance(item.transaction_count, bool)
                or not isinstance(item.transaction_count, int)
                or item.transaction_count < 0
            ):
                raise DatabaseError(
                    "Price transaction_count must be a non-negative integer or None."
                )

    @staticmethod
    def _validate_date(value: str, field: str) -> None:
        try:
            date.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise DatabaseError(
                f"{field} must be an ISO date in YYYY-MM-DD format."
            ) from exc


# Short name for callers that use the SSD's table-oriented terminology.
PriceRepository = PriceHistoryRepository
