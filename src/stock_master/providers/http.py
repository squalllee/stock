"""Small standard-library HTTP client with retry and JSON validation."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from json import JSONDecodeError
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import (
    HTTPCookieProcessor,
    Request,
    build_opener,
    urlopen,
)

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


class TextHttpClient:
    """Fetch HTML text and submit forms while preserving a cookie session.

    TDCC's historical query page uses a session cookie and rotates its CSRF
    token after each successful form submission.  This small client keeps the
    standard-library-only runtime while providing the browser-like session the
    provider needs.
    """

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        user_agent: str = "taiwan-stock-master/0.1",
        opener: Callable[..., object] | None = None,
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
        self._sleep = sleep
        if opener is None:
            cookie_jar = CookieJar()
            self._opener = build_opener(HTTPCookieProcessor(cookie_jar)).open
        else:
            self._opener = opener

    def get_text(self, url: str) -> str:
        """Fetch one HTML page with GET."""

        return self._request(url, method="GET")

    def post_form(self, url: str, fields: dict[str, str]) -> str:
        """Submit a UTF-8 URL-encoded form and return the response text."""

        body = urlencode(fields).encode("utf-8")
        return self._request(
            url,
            method="POST",
            data=body,
            content_type="application/x-www-form-urlencoded; charset=UTF-8",
        )

    def _request(
        self,
        url: str,
        *,
        method: str,
        data: bytes | None = None,
        content_type: str | None = None,
    ) -> str:
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            headers = {
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": self.user_agent,
            }
            if content_type:
                headers["Content-Type"] = content_type
            request = Request(
                url,
                data=data,
                headers=headers,
                method=method,
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
                        payload = response.read().decode("utf-8-sig")
                        return payload
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
            except UnicodeDecodeError as exc:
                raise StockProviderError(
                    f"Invalid text response from {url}: {exc}"
                ) from exc

            if attempt < self.max_attempts:
                self._sleep(self.backoff_seconds * (2 ** (attempt - 1)))

        detail = str(last_error) if last_error else "unknown HTTP error"
        raise StockProviderError(
            f"Request failed after {self.max_attempts} attempt(s): {url}: {detail}"
        ) from last_error
