from stock_master.models import Stock
from stock_master.providers.twse import TWSEStockProvider


class FakeJsonClient:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        return self.payload


def test_fetch_twse_stock_and_exclude_etf():
    client = FakeJsonClient(
        [
            {"公司代號": "2330", "公司簡稱": "台積電"},
            {"公司代號": "0050", "公司簡稱": "元大台灣50"},
            {"公司代號": "9103", "公司簡稱": "明輝-DR"},
        ]
    )
    stocks = TWSEStockProvider(client).fetch()

    assert stocks == [Stock("2330", "台積電", "TWSE")]
    assert client.urls


def test_twse_schema_change_is_not_silent():
    client = FakeJsonClient([{"unexpected_code": "2330", "unexpected_name": "台積電"}])

    try:
        TWSEStockProvider(client).fetch()
    except Exception as exc:
        assert "Expected field" in str(exc)
    else:
        raise AssertionError("schema change should raise")

