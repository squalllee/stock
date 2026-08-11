import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from stock_master.exceptions import StockDataValidationError
from stock_master.models import MarginHistory
from stock_master.providers.tpex_margin import TPExMarginProvider


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


def test_tpex_margin_parse_uses_trading_units():
    client = FakeJsonClient(load_fixture("tpex_margin.json"))
    provider = TPExMarginProvider(client)

    records = provider.fetch(date(2026, 8, 7))

    assert records[0] == MarginHistory(
        trade_date="2026-08-07",
        stock_code="3105",
        market="TPEX",
        margin_buy=291,
        margin_sell=0,
        margin_cash_redemption=0,
        margin_previous_balance=3843,
        margin_balance=4134,
        short_buy=0,
        short_sell=0,
        short_stock_redemption=0,
        short_previous_balance=8,
        short_balance=8,
        offsetting_volume=0,
    )
    assert len(records) == 3
    assert provider.last_trade_date == "2026-08-07"

    query = parse_qs(urlsplit(client.urls[0]).query)
    assert query["d"] == ["115/08/07"]
    assert query["l"] == ["zh-tw"]
    assert query["o"] == ["json"]


def test_tpex_margin_invalid_date_is_not_silently_accepted():
    payload = load_fixture("tpex_margin.json")
    payload["date"] = "5115-08-31"

    with pytest.raises(StockDataValidationError, match="unexpected trade date"):
        TPExMarginProvider(FakeJsonClient(payload)).fetch(date(2026, 8, 7))


def test_tpex_margin_no_data_status_is_explicit():
    provider = TPExMarginProvider(FakeJsonClient({"stat": "查無資料"}))

    assert provider.fetch(date(2026, 8, 9)) == []
    assert provider.last_no_data is True


def test_tpex_margin_schema_change_raises():
    payload = load_fixture("tpex_margin.json")
    payload["tables"][0]["fields"] = ["代號", "名稱"]

    with pytest.raises(StockDataValidationError, match="schema changed"):
        TPExMarginProvider(FakeJsonClient(payload)).fetch()
