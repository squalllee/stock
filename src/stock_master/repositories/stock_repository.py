"""SQLite repository for the stock master."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from stock_master.exceptions import DatabaseError
from stock_master.models import Stock

_STOCK_CODE_PATTERN = re.compile(r"^\d{4}$")
_VALID_MARKETS = frozenset({"TWSE", "TPEX"})

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS stocks (
    stock_code TEXT PRIMARY KEY,
    stock_name TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('TWSE', 'TPEX')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_stocks_market
ON stocks(market);
"""

UPSERT_SQL = """
INSERT INTO stocks (stock_code, stock_name, market)
VALUES (?, ?, ?)
ON CONFLICT(stock_code)
DO UPDATE SET
    stock_name = excluded.stock_name,
    market = excluded.market,
    updated_at = CURRENT_TIMESTAMP;
"""


@dataclass(frozen=True, slots=True)
class RepositorySyncStats:
    """Insert/update counts for one repository transaction."""

    inserted_count: int
    updated_count: int


class StockRepository:
    """Repository that never deletes rows during a V1 sync."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        try:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.db_path)
            connection.row_factory = sqlite3.Row
            return connection
        except (OSError, sqlite3.Error) as exc:
            raise DatabaseError(
                f"Could not open SQLite database {self.db_path}: {exc}"
            ) from exc

    def create_tables(self) -> None:
        """Create the schema and index without changing existing rows."""

        connection = self._connect()
        try:
            with connection:
                connection.executescript(SCHEMA_SQL)
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not create SQLite schema in {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def upsert(self, stock: Stock) -> RepositorySyncStats:
        """Upsert one stock through the same transaction-safe batch path."""

        return self.upsert_many([stock])

    def upsert_many(self, stocks: Iterable[Stock]) -> RepositorySyncStats:
        """UPSERT all stocks in one transaction and return counts."""

        values = [
            Stock(
                stock_code=stock.stock_code.strip(),
                stock_name=stock.stock_name.strip(),
                market=stock.market,
            )
            if (
                isinstance(stock, Stock)
                and isinstance(stock.stock_code, str)
                and isinstance(stock.stock_name, str)
            )
            else stock
            for stock in stocks
        ]
        self._validate_stocks(values)
        self.create_tables()
        if not values:
            return RepositorySyncStats(inserted_count=0, updated_count=0)

        codes = [stock.stock_code for stock in values]
        if len(codes) != len(set(codes)):
            raise DatabaseError("Duplicate stock_code in one upsert batch.")

        connection = self._connect()
        try:
            with connection:
                existing_codes = self._existing_codes(connection, codes)
                connection.executemany(
                    UPSERT_SQL,
                    [
                        (stock.stock_code, stock.stock_name, stock.market)
                        for stock in values
                    ],
                )
            return RepositorySyncStats(
                inserted_count=sum(
                    1 for stock in values if stock.stock_code not in existing_codes
                ),
                updated_count=sum(
                    1 for stock in values if stock.stock_code in existing_codes
                ),
            )
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not update SQLite database {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    @staticmethod
    def _existing_codes(
        connection: sqlite3.Connection, codes: list[str]
    ) -> set[str]:
        existing: set[str] = set()
        # Keep IN clauses below SQLite's default host-parameter limit.
        for start in range(0, len(codes), 900):
            chunk = codes[start : start + 900]
            placeholders = ",".join("?" for _ in chunk)
            rows = connection.execute(
                f"SELECT stock_code FROM stocks WHERE stock_code IN ({placeholders})",
                chunk,
            )
            existing.update(row["stock_code"] for row in rows)
        return existing

    def get_all(self) -> list[Stock]:
        """Return all master records in stable code order."""

        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT stock_code, stock_name, market FROM stocks "
                "ORDER BY stock_code"
            )
            return [
                Stock(
                    stock_code=row["stock_code"],
                    stock_name=row["stock_name"],
                    market=row["market"],
                )
                for row in rows
            ]
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not read SQLite database {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def get_by_code(self, stock_code: str) -> Stock | None:
        """Return one stock by primary key, or None."""

        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT stock_code, stock_name, market FROM stocks "
                "WHERE stock_code = ?",
                (stock_code,),
            ).fetchone()
            if row is None:
                return None
            return Stock(
                stock_code=row["stock_code"],
                stock_name=row["stock_name"],
                market=row["market"],
            )
        except sqlite3.Error as exc:
            raise DatabaseError(
                f"Could not read stock {stock_code} from {self.db_path}: {exc}"
            ) from exc
        finally:
            connection.close()

    def _validate_stocks(self, stocks: list[Stock]) -> None:
        for stock in stocks:
            if not isinstance(stock, Stock):
                raise DatabaseError("Repository accepts Stock instances only.")
            if not isinstance(stock.stock_code, str) or not isinstance(
                stock.stock_name, str
            ):
                raise DatabaseError("stock_code and stock_name must be text.")
            if not stock.stock_code or not _STOCK_CODE_PATTERN.fullmatch(
                stock.stock_code.strip()
            ):
                raise DatabaseError(
                    f"Invalid stock_code {stock.stock_code!r}; expected four digits."
                )
            if not stock.stock_name or not stock.stock_name.strip():
                raise DatabaseError("stock_name cannot be empty.")
            if stock.market not in _VALID_MARKETS:
                raise DatabaseError(
                    f"Invalid market {stock.market!r}; expected TWSE or TPEX."
                )
