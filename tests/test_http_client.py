import json

from stock_master.providers.http import JsonHttpClient


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

