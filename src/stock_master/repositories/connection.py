"""SQLite connection helpers shared by read/write repositories."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import quote

from stock_master.exceptions import DatabaseError


def connect_sqlite(
    db_path: str | Path,
    *,
    readonly: bool = False,
) -> sqlite3.Connection:
    """Open a short-lived SQLite connection.

    Web repositories pass ``readonly=True`` so a missing or malformed web
    query can never create a database file or write schema/data accidentally.
    Batch repositories keep the historical read/write behavior by using the
    default.
    """

    path = Path(db_path)
    try:
        if readonly:
            if not path.is_file():
                raise DatabaseError(f"SQLite database does not exist: {path}")
            uri = f"file:{quote(str(path.resolve()), safe='/')}?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection
    except DatabaseError:
        raise
    except (OSError, sqlite3.Error) as exc:
        mode = "read-only " if readonly else ""
        raise DatabaseError(
            f"Could not open {mode}SQLite database {path}: {exc}"
        ) from exc

