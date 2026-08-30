import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from stock_master.exceptions import StockDataValidationError
from stock_master.models import MarginHistory
from stock_master.providers.twse_margin import TWSEMarginProvider


FIXTURES = Path(__file__).parent / "fixtures"


class FakeJsonClient:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        return self.payload


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_twse_margin_parse_and_normalize_units():
    client = FakeJsonClient(load_fixture("twse_margin.json"))
    provider = TWSEMarginProvider(client)

    records = provider.fetch(date(2026, 8, 7))

    assert records == [
        MarginHistory(
            trade_date="2026-08-07",
            stock_code="2330",
            market="TWSE",
            margin_buy=1234,
            margin_sell=567,
            margin_cash_redemption=8,
            margin_previous_balance=100000,
            margin_balance=100659,
            short_buy=10,
            short_sell=20,
            short_stock_redemption=3,
            short_previous_balance=4000,
            short_balance=3987,
            offsetting_volume=5,
            margin_limit=200000,
            margin_utilization=50.3295,
        ),
        MarginHistory(
            trade_date="2026-08-07",
            stock_code="0050",
            market="TWSE",
            margin_buy=100,
            margin_sell=20,
            margin_cash_redemption=0,
            margin_previous_balance=1000,
            margin_balance=1080,
            short_buy=1,
            short_sell=2,
            short_stock_redemption=0,
            short_previous_balance=30,
            short_balance=29,
            offsetting_volume=1,
            margin_limit=2000,
            margin_utilization=54.0,
        ),
        MarginHistory(
            trade_date="2026-08-07",
            stock_code="3105",
            market="TWSE",
            margin_buy=0,
            margin_sell=1,
            margin_cash_redemption=0,
            margin_previous_balance=10,
            margin_balance=9,
            short_buy=2,
            short_sell=0,
            short_stock_redemption=1,
            short_previous_balance=3,
            short_balance=4,
            offsetting_volume=None,
            margin_limit=20,
            margin_utilization=45.0,
        ),
    ]
    assert provider.last_trade_date == "2026-08-07"
    assert provider.last_skipped_total_count == 1

    query = parse_qs(urlsplit(client.urls[0]).query)
    assert query["date"] == ["20260807"]
    assert query["selectType"] == ["STOCK"]


def test_twse_margin_latest_uses_payload_date():
    client = FakeJsonClient(load_fixture("twse_margin.json"))

    records = TWSEMarginProvider(client).fetch()

    assert records[0].trade_date == "2026-08-07"
    assert "date" not in parse_qs(urlsplit(client.urls[0]).query)


def test_twse_margin_no_data_is_distinguished_from_schema_failure():
    client = FakeJsonClient(
        {"stat": "很抱歉，沒有符合條件的資料", "date": "20260809"}
    )
    provider = TWSEMarginProvider(client)

    assert provider.fetch(date(2026, 8, 9)) == []
    assert provider.last_no_data is True


def test_twse_margin_invalid_numeric_value_raises():
    payload = load_fixture("twse_margin.json")
    payload["tables"][1]["data"][1][2] = "not-a-number"

    with pytest.raises(StockDataValidationError, match="invalid margin_buy"):
        TWSEMarginProvider(FakeJsonClient(payload)).fetch()


def test_twse_margin_schema_change_raises():
    payload = load_fixture("twse_margin.json")
    payload["tables"][1]["fields"] = ["代號", "名稱"]

    with pytest.raises(StockDataValidationError, match="schema changed"):
        TWSEMarginProvider(FakeJsonClient(payload)).fetch()
