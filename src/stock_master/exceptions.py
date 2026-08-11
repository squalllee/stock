"""Domain exceptions raised by Taiwan Stock Master."""


class StockMasterError(Exception):
    """Base exception for expected application failures."""


class StockProviderError(StockMasterError):
    """The official provider could not be reached or decoded."""


class StockDataValidationError(StockMasterError):
    """The provider returned empty, malformed, or unsafe data."""


class DatabaseError(StockMasterError):
    """SQLite could not create or update the stock master."""

