import json
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from stock_master.exceptions import StockDataValidationError
from stock_master.providers.tpex_price import TPExPriceProvider
from stock_master.providers.twse_price import TWSEPriceProvider


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


def test_twse_price_provider_normalizes_daily_rows_and_skips_total():
    client = FakeJsonClient(load_fixture("twse_price.json"))
    provider = TWSEPriceProvider(client, url="https://example.test/MI_INDEX")

    records = provider.fetch(date(2026, 8, 7))

    assert [record.stock_code for record in records] == ["2330", "0050"]
    assert records[0].trade_volume == 24_414_025
    assert records[0].trade_value == 57_947_015_347
    assert records[0].market_average_price == pytest.approx(
        57_947_015_347 / 24_414_025
    )
    assert records[0].close_price == 2370.0
    assert provider.last_trade_date == "2026-08-07"
    assert provider.last_skipped_total_count == 1
    query = parse_qs(urlsplit(client.urls[0]).query)
    assert query["date"] == ["20260807"]
    assert query["type"] == ["ALLBUT0999"]


def test_twse_price_provider_reads_latest_api_date():
    client = FakeJsonClient({"stat": "OK", "date": "20260828"})

    result = TWSEPriceProvider(client).fetch_latest_data_date()

    assert result == "2026-08-28"
    assert "date" not in parse_qs(urlsplit(client.urls[0]).query)


def test_tpex_price_provider_normalizes_lots_and_thousand_twd_and_caches_month():
    client = FakeJsonClient(load_fixture("tpex_price.json"))
    provider = TPExPriceProvider(
        client,
        url="https://example.test/tradingStock",
        stock_codes={"3105"},
        request_delay_seconds=0,
        sleep=lambda _: pytest.fail("cache should avoid a second request"),
    )

    first = provider.fetch(date(2026, 8, 7))
    second = provider.fetch(date(2026, 8, 3))

    assert first[0].trade_volume == 10_000_000
    assert first[0].trade_value == 3_100_000_000
    assert first[0].close_price == 310.0
    assert first[0].market_average_price == pytest.approx(310.0)
    assert second[0].trade_date == "2026-08-03"
    assert len(client.urls) == 1
    query = parse_qs(urlsplit(client.urls[0]).query)
    assert query["code"] == ["3105"]
    assert query["date"] == ["2026/08/01"]
    assert query["response"] == ["json"]


def test_price_provider_rejects_unexpected_date():
    payload = load_fixture("twse_price.json")
    payload["date"] = "20260808"
    provider = TWSEPriceProvider(FakeJsonClient(payload))

    with pytest.raises(StockDataValidationError, match="unexpected trade date"):
        provider.fetch(date(2026, 8, 7))


def test_tpex_price_provider_explicit_no_data():
    client = FakeJsonClient({"stat": "很抱歉，沒有符合條件的資料"})
    provider = TPExPriceProvider(
        client,
        stock_codes={"3105"},
        request_delay_seconds=0,
    )

    assert provider.fetch(date(2026, 8, 9)) == []
    assert provider.last_no_data is True


def test_tpex_price_provider_ignores_date_footnote_marker():
    payload = load_fixture("tpex_price.json")
    payload["tables"][0]["data"][0][0] = "115/08/07*"
    provider = TPExPriceProvider(
        FakeJsonClient(payload),
        stock_codes={"3105"},
        request_delay_seconds=0,
    )

    records = provider.fetch(date(2026, 8, 7))

    assert records[0].trade_date == "2026-08-07"


def test_tpex_price_provider_latest_uses_market_wide_endpoint():
    client = FakeJsonClient(
        [
            {
                "Date": "1150824",
                "SecuritiesCompanyCode": "3105",
                "CompanyName": "穩懋",
                "Close": "310.00",
                "Open": "---",
                "High": "315.00",
                "Low": "300.00",
                "Average": "310.00",
                "TradingShares": "10000000",
                "TransactionAmount": "3100000000",
                "TransactionNumber": "9000",
            },
            {
                "Date": "1150824",
                "SecuritiesCompanyCode": "00679B",
                "CompanyName": "元大美債20年",
                "Close": "25.83",
                "Open": "25.80",
                "High": "25.83",
                "Low": "25.76",
                "Average": "25.78",
                "TradingShares": "12414050",
                "TransactionAmount": "320070003",
                "TransactionNumber": "3112",
            },
        ]
    )
    provider = TPExPriceProvider(
        client,
        stock_codes={"3105"},
        request_delay_seconds=0,
    )

    records = provider.fetch()

    assert records[0].stock_code == "3105"
    assert records[0].trade_date == "2026-08-24"
    assert records[0].trade_volume == 10_000_000
    assert records[0].trade_value == 3_100_000_000
    assert records[0].open_price is None
    assert records[0].market_average_price == 310.0
    assert provider.last_trade_date == "2026-08-24"
    assert client.urls == [
        "https://www.tpex.org.tw/openapi/v1/"
        "tpex_mainboard_daily_close_quotes"
    ]


def test_tpex_price_provider_reads_latest_api_date_without_stock_universe():
    client = FakeJsonClient(
        [
            {"Date": "1150828", "SecuritiesCompanyCode": "3105"},
            {"Date": "1150828", "SecuritiesCompanyCode": "6488"},
        ]
    )

    result = TPExPriceProvider(client).fetch_latest_data_date()

    assert result == "2026-08-28"
