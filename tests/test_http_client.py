import json
import ssl
from http.client import IncompleteRead
from urllib.error import HTTPError

from stock_master.providers.http import (
    JsonHttpClient,
    TextHttpClient,
    _create_compatible_ssl_context,
)


class FakeResponse:
    status = 200

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.value).encode("utf-8")


class FakeTextResponse(FakeResponse):
    def read(self):
        return self.value.encode("utf-8")


class TruncatedResponse(FakeResponse):
    def __init__(self, value, *, fail_read=True):
        super().__init__(value)
        self.fail_read = fail_read

    def read(self):
        if self.fail_read:
            self.fail_read = False
            raise IncompleteRead(b"partial response", 1024)
        return super().read()


class TruncatedTextResponse(FakeTextResponse):
    def __init__(self, value, *, fail_read=True):
        super().__init__(value)
        self.fail_read = fail_read

    def read(self):
        if self.fail_read:
            self.fail_read = False
            raise IncompleteRead(b"partial response", 1024)
        return super().read()


class RangeResponse:
    status = 200

    def __init__(self, payload, *, status=200, headers=None, fail_after=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}
        self.fail_after = fail_after

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        if self.fail_after is not None:
            partial = self.payload[: self.fail_after]
            raise IncompleteRead(partial, len(self.payload) - len(partial))
        return self.payload


def test_compatible_ssl_context_keeps_verification_without_strict_mode():
    context = _create_compatible_ssl_context()

    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED
    strict_flag = getattr(ssl, "VERIFY_X509_STRICT", 0)
    if strict_flag:
        assert not context.verify_flags & strict_flag


def test_http_client_sets_headers_and_decodes_json():
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse([{"ok": True}])

    client = JsonHttpClient(
        timeout=7,
        max_attempts=1,
        opener=opener,
        sleep=lambda _: None,
    )

    assert client.get_json("https://example.test/data") == [{"ok": True}]
    request, timeout = requests[0]
    assert timeout == 7
    assert request.get_header("User-agent") == "taiwan-stock-master/0.1"
    assert request.get_header("Accept") == "application/json"


def test_text_http_client_sets_headers_and_encodes_form():
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeTextResponse("<html>ok</html>")

    client = TextHttpClient(
        timeout=9,
        max_attempts=1,
        opener=opener,
        sleep=lambda _: None,
    )

    assert client.post_form(
        "https://example.test/query", {"stockNo": "2330", "scaDate": "20260731"}
    ) == "<html>ok</html>"
    request, timeout = requests[0]
    assert timeout == 9
    assert request.get_header("User-agent") == "taiwan-stock-master/0.1"
    assert request.get_header("Accept") == "text/html,application/xhtml+xml"
    assert request.get_header("Content-type").startswith(
        "application/x-www-form-urlencoded"
    )
    assert request.data in {
        b"stockNo=2330&scaDate=20260731",
        b"scaDate=20260731&stockNo=2330",
    }


def test_json_http_client_retries_temporary_redirect():
    calls = []
    sleeps = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise HTTPError(
                request.full_url,
                307,
                "Temporary Redirect",
                hdrs={},
                fp=None,
            )
        return FakeResponse({"stat": "ok"})

    client = JsonHttpClient(
        max_attempts=2,
        opener=opener,
        sleep=sleeps.append,
    )

    assert client.get_json("https://example.test/data") == {"stat": "ok"}
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_json_http_client_retries_incomplete_response():
    calls = []
    sleeps = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            return TruncatedResponse({"stat": "partial"})
        return FakeResponse({"stat": "ok"})

    client = JsonHttpClient(
        max_attempts=2,
        opener=opener,
        sleep=sleeps.append,
    )

    assert client.get_json("https://example.test/data") == {"stat": "ok"}
    assert len(calls) == 2
    assert sleeps == [1.0]


def test_json_http_client_resumes_incomplete_response_with_range():
    payload = json.dumps({"stat": "ok"}).encode("utf-8")
    requests = []

    def opener(request, timeout):
        requests.append(request)
        range_header = request.get_header("Range")
        if range_header is None:
            return RangeResponse(payload, fail_after=5)
        start = int(range_header.removeprefix("bytes=").removesuffix("-"))
        return RangeResponse(
            payload[start:],
            status=206,
            headers={
                "Content-Range": f"bytes {start}-{len(payload) - 1}/{len(payload)}"
            },
        )

    client = JsonHttpClient(
        max_attempts=2,
        opener=opener,
        sleep=lambda _: None,
    )

    assert client.get_json("https://example.test/large-data") == {"stat": "ok"}
    assert requests[1].get_header("Range") == "bytes=5-"
    assert requests[1].get_header("Accept-encoding") == "identity"


def test_text_http_client_resumes_incomplete_get_with_range():
    payload = b"<html>ok</html>"
    requests = []

    def opener(request, timeout):
        requests.append(request)
        range_header = request.get_header("Range")
        if range_header is None:
            return RangeResponse(payload, fail_after=5)
        start = int(range_header.removeprefix("bytes=").removesuffix("-"))
        return RangeResponse(
            payload[start:],
            status=206,
            headers={
                "Content-Range": f"bytes {start}-{len(payload) - 1}/{len(payload)}"
            },
        )

    client = TextHttpClient(
        max_attempts=2,
        opener=opener,
        sleep=lambda _: None,
    )

    assert client.get_text("https://example.test/large-page") == "<html>ok</html>"
    assert requests[1].get_header("Range") == "bytes=5-"
    assert requests[1].get_header("Accept-encoding") == "identity"


def test_text_http_client_retries_incomplete_response():
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            return TruncatedTextResponse("partial")
        return FakeTextResponse("<html>ok</html>")

    client = TextHttpClient(
        max_attempts=2,
        opener=opener,
        sleep=lambda _: None,
    )

    assert client.get_text("https://example.test/page") == "<html>ok</html>"
    assert len(calls) == 2
