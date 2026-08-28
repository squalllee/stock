"""Small standard-library HTTP client with retry and JSON validation."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from http.client import IncompleteRead, RemoteDisconnected
from http.cookiejar import CookieJar
from json import JSONDecodeError
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

_RETRYABLE_REDIRECT_STATUS_CODES = frozenset({307, 308})


def _content_range_total(response: object) -> int | None:
    """Return the total byte count from a ``Content-Range`` header."""

    header: object | None = None
    headers = getattr(response, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        header = headers.get("Content-Range")
    if header is None:
        getheader = getattr(response, "getheader", None)
        if callable(getheader):
            header = getheader("Content-Range")
    if not isinstance(header, str) or "/" not in header:
        return None
    total = header.rsplit("/", 1)[1].strip()
    if not total.isdigit():
        return None
    return int(total)


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
        downloaded_bytes = b""

        for attempt in range(1, self.max_attempts + 1):
            range_requested = bool(downloaded_bytes)
            response_status: int | None = None
            headers = {
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            }
            if range_requested:
                headers["Range"] = f"bytes={len(downloaded_bytes)}-"
                headers["Accept-Encoding"] = "identity"
            request = Request(
                url,
                headers=headers,
                method="GET",
            )

            try:
                response = self._opener(request, timeout=self.timeout)
                with response:
                    status = getattr(response, "status", None)
                    if status is None and hasattr(response, "getcode"):
                        status = response.getcode()
                    if status is not None:
                        response_status = int(status)
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
                        if range_requested and response_status == 206:
                            payload_bytes = downloaded_bytes + payload_bytes
                            total_length = _content_range_total(response)
                            if (
                                total_length is not None
                                and len(payload_bytes) < total_length
                            ):
                                downloaded_bytes = payload_bytes
                                last_error = StockProviderError(
                                    "HTTP range response ended before the full "
                                    "response body was received"
                                )
                                logger.warning(
                                    "HTTP attempt %s/%s received only %s/%s "
                                    "bytes for %s; resuming",
                                    attempt,
                                    self.max_attempts,
                                    len(payload_bytes),
                                    total_length,
                                    url,
                                )
                                payload_bytes = b""
                        if payload_bytes:
                            payload_text = payload_bytes.decode("utf-8-sig")
                            return json.loads(payload_text)
            except StockProviderError:
                raise
            except HTTPError as exc:
                last_error = exc
                if (
                    exc.code < 500
                    and exc.code != 429
                    and exc.code not in _RETRYABLE_REDIRECT_STATUS_CODES
                ):
                    break
                logger.warning(
                    "HTTP attempt %s/%s failed for %s: status=%s",
                    attempt,
                    self.max_attempts,
                    url,
                    exc.code,
                )
            except IncompleteRead as exc:
                last_error = exc
                partial = getattr(exc, "partial", b"")
                if partial:
                    if range_requested and response_status == 206:
                        downloaded_bytes += partial
                    elif range_requested:
                        # The server ignored Range and sent a fresh response.
                        # Keep its partial prefix and try the range request again.
                        downloaded_bytes = partial
                    else:
                        downloaded_bytes = partial
                    logger.warning(
                        "HTTP attempt %s/%s received an incomplete response "
                        "for %s; resuming at byte %s: %s",
                        attempt,
                        self.max_attempts,
                        url,
                        len(downloaded_bytes),
                        exc,
                    )
                else:
                    logger.warning(
                        "HTTP attempt %s/%s failed for %s: %s",
                        attempt,
                        self.max_attempts,
                        url,
                        exc,
                    )
            except (
                RemoteDisconnected,
                URLError,
                TimeoutError,
                OSError,
            ) as exc:
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
        downloaded_bytes = b""

        for attempt in range(1, self.max_attempts + 1):
            range_requested = method == "GET" and bool(downloaded_bytes)
            response_status: int | None = None
            headers = {
                "Accept": "text/html,application/xhtml+xml",
                "User-Agent": self.user_agent,
            }
            if range_requested:
                headers["Range"] = f"bytes={len(downloaded_bytes)}-"
                headers["Accept-Encoding"] = "identity"
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
                    if status is not None:
                        response_status = int(status)
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
                        response_complete = True
                        if range_requested and response_status == 206:
                            payload_bytes = downloaded_bytes + payload_bytes
                            total_length = _content_range_total(response)
                            if (
                                total_length is not None
                                and len(payload_bytes) < total_length
                            ):
                                downloaded_bytes = payload_bytes
                                last_error = StockProviderError(
                                    "HTTP range response ended before the full "
                                    "response body was received"
                                )
                                response_complete = False
                                logger.warning(
                                    "HTTP attempt %s/%s received only %s/%s "
                                    "bytes for %s; resuming",
                                    attempt,
                                    self.max_attempts,
                                    len(payload_bytes),
                                    total_length,
                                    url,
                                )
                        if response_complete:
                            return payload_bytes.decode("utf-8-sig")
            except StockProviderError:
                raise
            except HTTPError as exc:
                last_error = exc
                if (
                    exc.code < 500
                    and exc.code != 429
                    and exc.code not in _RETRYABLE_REDIRECT_STATUS_CODES
                ):
                    break
                logger.warning(
                    "HTTP attempt %s/%s failed for %s: status=%s",
                    attempt,
                    self.max_attempts,
                    url,
                    exc.code,
                )
            except IncompleteRead as exc:
                last_error = exc
                partial = getattr(exc, "partial", b"")
                if method == "GET" and partial:
                    if range_requested and response_status == 206:
                        downloaded_bytes += partial
                    else:
                        # The first request, or a server that ignored Range,
                        # returned a fresh response prefix.
                        downloaded_bytes = partial
                    logger.warning(
                        "HTTP attempt %s/%s received an incomplete response "
                        "for %s; resuming at byte %s: %s",
                        attempt,
                        self.max_attempts,
                        url,
                        len(downloaded_bytes),
                        exc,
                    )
                else:
                    logger.warning(
                        "HTTP attempt %s/%s failed for %s: %s",
                        attempt,
                        self.max_attempts,
                        url,
                        exc,
                    )
            except (
                RemoteDisconnected,
                URLError,
                TimeoutError,
                OSError,
            ) as exc:
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
