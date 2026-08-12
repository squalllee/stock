from __future__ import annotations


def test_search_supports_code_name_market_and_pagination(web_client):
    by_code = web_client.get("/api/v1/stocks/search?q=2330")
    assert [item["stock_code"] for item in by_code.json()["items"]] == ["2330"]

    by_name = web_client.get("/api/v1/stocks/search?q=台")
    assert {item["stock_code"] for item in by_name.json()["items"]} == {"1101", "2330"}

    tpex = web_client.get("/api/v1/stocks?market=TPEX&limit=1&offset=0")
    assert tpex.json()["items"] == [
        {"stock_code": "3105", "stock_name": "穩懋", "market": "TPEX"}
    ]


def test_history_range_and_pagination_are_inclusive(web_client):
    response = web_client.get(
        "/api/v1/stocks/2330/prices?from=2026-08-10&to=2026-08-11&limit=1"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["trade_date"] == "2026-08-10"
    assert payload["has_more"] is True

    second_page = web_client.get(
        "/api/v1/stocks/2330/prices?from=2026-08-10&to=2026-08-11&limit=1&offset=1"
    )
    assert [item["trade_date"] for item in second_page.json()["items"]] == [
        "2026-08-11"
    ]
    assert second_page.json()["has_more"] is False


def test_margin_estimates_are_explicitly_marked_as_estimated(web_client):
    response = web_client.get("/api/v1/stocks/2330/margin-estimates/latest")
    assert response.status_code == 200
    assert response.json()["estimated"] is True
    assert response.json()["model_version"] == "margin-cost-v1-wma-daily-market-average"


def test_tdcc_latest_history_contains_holding_levels(web_client):
    response = web_client.get("/api/v1/stocks/2330/tdcc?limit=2")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    assert {item["holding_level"] for item in items} == {"01", "02"}
    assert {item["data_date"] for item in items} == {"2026-08-08"}

    latest = web_client.get("/api/v1/stocks/2330/tdcc/latest")
    assert latest.status_code == 200
    assert latest.json()["data_date"] == "2026-08-08"
    assert len(latest.json()["items"]) == 2
