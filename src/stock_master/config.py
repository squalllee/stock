"""Application configuration and official data source URLs."""

from pathlib import Path

TWSE_API_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_API_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"

DEFAULT_DATABASE_PATH = Path("data/stocks.db")
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
DEFAULT_USER_AGENT = "taiwan-stock-master/0.1 (+https://openapi.twse.com.tw/)"

# These are intentionally conservative sanity checks, not the business definition
# of a market. They protect the existing database from a truncated upstream feed.
DEFAULT_MIN_EXPECTED_TWSE_STOCKS = 500
DEFAULT_MIN_EXPECTED_TPEX_STOCKS = 100

