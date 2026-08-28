"""Application configuration and official data source URLs."""

from pathlib import Path
import os
import re

_ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_project_dotenv(path: str | Path | None = None) -> Path | None:
    """Load a small, dependency-free ``.env`` file without overriding env vars.

    The desktop launcher uses this so Windows users can keep the Supabase
    Secret key in the project root. Explicit operating-system environment
    variables always win over values from the file.
    """

    candidates: list[Path] = []
    if path is not None:
        candidates.append(Path(path))
    else:
        candidates.append(Path.cwd() / ".env")
        source_root = Path(__file__).resolve().parents[2]
        candidates.append(source_root / ".env")

    dotenv_path: Path | None = next(
        (candidate for candidate in candidates if candidate.is_file()),
        None,
    )
    if dotenv_path is None:
        return None

    for raw_line in dotenv_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not _ENV_KEY_PATTERN.fullmatch(key):
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        os.environ.setdefault(key, value)
    return dotenv_path

TWSE_API_URL = "https://openapi.twse.com.tw/v1/opendata/t187ap03_L"
TPEX_API_URL = "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap03_O"
TDCC_API_URL = "https://openapi.tdcc.com.tw/v1/opendata/1-5"
TDCC_HISTORY_URL = "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock"
TDCC_MAX_HOLDING_LEVEL = 15
BILLDB_SUPABASE_URL = "https://vngtmamxhvcldecesfwh.supabase.co"
DEFAULT_SUPABASE_TDCC_BATCH_SIZE = 500
TWSE_MARGIN_URL = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
TPEX_MARGIN_URL = (
    "https://www.tpex.org.tw/web/stock/margin_trading/"
    "margin_balance/margin_bal_result.php"
)
TWSE_PRICE_URL = "https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX"
TPEX_PRICE_URL = "https://www.tpex.org.tw/www/zh-tw/afterTrading/tradingStock"
TPEX_LATEST_PRICE_URL = (
    "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
)

DEFAULT_DATABASE_PATH = Path("data/stocks.db")
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
DEFAULT_USER_AGENT = "taiwan-stock-master/0.1 (+https://openapi.twse.com.tw/)"
DEFAULT_TDCC_HISTORY_DAYS = 30
DEFAULT_TDCC_HISTORY_WORKERS = 2
DEFAULT_TDCC_HISTORY_REQUEST_DELAY_SECONDS = 0.2
DEFAULT_TDCC_HISTORY_STOCK_BATCH_SIZE = 50
DEFAULT_TDCC_HISTORY_DEGRADED_DELAY_SECONDS = 1.0
DEFAULT_TDCC_HISTORY_RECOVERY_BATCHES = 3
DEFAULT_MARGIN_HISTORY_DAYS = 30
DEFAULT_MARGIN_HISTORY_REQUEST_DELAY_SECONDS = 0.2
DEFAULT_PRICE_HISTORY_REQUEST_DELAY_SECONDS = 0.2
DEFAULT_MARGIN_FINANCING_RATIO = 0.60
DEFAULT_MARGIN_MODEL_VERSION = "margin-cost-v1-wma-daily-market-average"

# These are intentionally conservative sanity checks, not the business definition
# of a market. They protect the existing database from a truncated upstream feed.
DEFAULT_MIN_EXPECTED_TWSE_STOCKS = 500
DEFAULT_MIN_EXPECTED_TPEX_STOCKS = 100
