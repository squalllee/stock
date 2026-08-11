"""Small standard-library HTTP client with retry and JSON validation."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from json import JSONDecodeError
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from stock_master.exceptions import StockProviderError

logger = logging.getLogger(__name__)


class JsonHttpClient:
    """Fetch JSON from an HTTPS endpoint with bounded retries.

    opener and sleep are injectable so provider tests never need a real network
    connection or a real delay.
    """

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        user_agent: str = "taiwan-stock-master/0.1",
        opener: Callable[..., object] = urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative")

        self.timeout = timeout
        self.max_attempts = max_attempts
        self.backoff_seconds = backoff_seconds
        self.user_agent = user_agent
        self._opener = opener
        self._sleep = sleep

    def get_json(self, url: str) -> object:
        """Return a decoded JSON payload or raise StockProviderError."""

        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            request = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": self.user_agent,
                },
                method="GET",
            )

            try:
                response = self._opener(request, timeout=self.timeout)
                with response:
                    status = getattr(response, "status", None)
                    if status is None and hasattr(response, "getcode"):
                        status = response.getcode()
                    if status is not None and not 200 <= int(status) < 300:
                        error = StockProviderError(
                            f"HTTP status {status} returned by {url}"
                        )
                        if int(status) < 500 and int(status) != 429:
                            raise error
                        last_error = error
                        logger.warning(
                            "HTTP attempt %s/%s failed for %s: status=%s",
                            attempt,
                            self.max_attempts,
                            url,
                            status,
                        )
                    else:
                        payload_bytes = response.read()
                        payload_text = payload_bytes.decode("utf-8-sig")
                        return json.loads(payload_text)
            except StockProviderError:
                raise
            except HTTPError as exc:
                last_error = exc
                if exc.code < 500 and exc.code != 429:
                    break
                logger.warning(
                    "HTTP attempt %s/%s failed for %s: status=%s",
                    attempt,
                    self.max_attempts,
                    url,
                    exc.code,
                )
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                logger.warning(
                    "HTTP attempt %s/%s failed for %s: %s",
                    attempt,
                    self.max_attempts,
                    url,
                    exc,
                )
            except (UnicodeDecodeError, JSONDecodeError) as exc:
                raise StockProviderError(
                    f"Invalid JSON response from {url}: {exc}"
                ) from exc

            if attempt < self.max_attempts:
                self._sleep(self.backoff_seconds * (2 ** (attempt - 1)))

        detail = str(last_error) if last_error else "unknown HTTP error"
        raise StockProviderError(
            f"Request failed after {self.max_attempts} attempt(s): {url}: {detail}"
        ) from last_error
