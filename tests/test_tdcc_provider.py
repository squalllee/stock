import json
from pathlib import Path

import pytest

from stock_master.exceptions import StockDataValidationError
from stock_master.models import TDCCDistribution
from stock_master.providers.tdcc import (
    TDCCDistributionProvider,
    normalize_data_date,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "tdcc_distribution.json"


class FakeJsonClient:
    def __init__(self, payload):
        self.payload = payload
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        return self.payload


def fixture_payload():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_tdcc_provider_parses_and_filters_bulk_records():
    client = FakeJsonClient(fixture_payload())

    records = TDCCDistributionProvider(client).fetch({"2330", "3105"})

    assert records == [
        TDCCDistribution(
            data_date="2026-08-07",
            stock_code="2330",
            holding_level="1-999",
            shareholder_count=1234,
            share_count=12345,
            holding_ratio=12.34,
        ),
        TDCCDistribution(
            data_date="2026-08-07",
            stock_code="3105",
            holding_level="17",
            shareholder_count=42,
            share_count=987654,
            holding_ratio=3.21,
        ),
    ]
    assert len(client.urls) == 1


def test_tdcc_provider_excludes_total_and_tracks_count():
    provider = TDCCDistributionProvider(FakeJsonClient(fixture_payload()))

    records = provider.fetch({"2330"})

    assert len(records) == 1
    assert provider.last_skipped_total_count == 1
    assert all(record.holding_level != "合計" for record in records)


def test_tdcc_provider_accepts_bom_prefixed_field_name():
    payload = [
        {
            "\ufeff資料日期": "20260807",
            "證券代號": "2330",
            "持股分級": "1",
            "人數": "1,234",
            "股數": "12,345",
            "占集保庫存數比例%": "12.34",
        }
    ]

    assert TDCCDistributionProvider(FakeJsonClient(payload)).fetch({"2330"}) == [
        TDCCDistribution(
            data_date="2026-08-07",
            stock_code="2330",
            holding_level="1",
            shareholder_count=1234,
            share_count=12345,
            holding_ratio=12.34,
        )
    ]


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("1150810", "2026-08-10"),
        ("115/08/10", "2026-08-10"),
        ("20260810", "2026-08-10"),
        ("2026-08-10", "2026-08-10"),
    ],
)
def test_tdcc_provider_normalizes_dates(raw, normalized):
    assert normalize_data_date(raw) == normalized


def test_tdcc_provider_empty_response_is_rejected():
    with pytest.raises(StockDataValidationError, match="empty"):
        TDCCDistributionProvider(FakeJsonClient([])).fetch({"2330"})


def test_tdcc_provider_invalid_schema_is_not_silent():
    payload = [
        {
            "資料日期": "115/08/07",
            "證券代號": "2330",
            "持股分級": "1-999",
            "人數": "1,234",
            "股數": "12,345",
        }
    ]

    with pytest.raises(StockDataValidationError, match="holding_ratio"):
        TDCCDistributionProvider(FakeJsonClient(payload)).fetch({"2330"})
