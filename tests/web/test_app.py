from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from stock_master.web import create_app


def test_health_and_pages_render(web_client):
    assert web_client.get("/api/v1/health").json() == {
        "status": "ok",
        "database": "ok",
    }
    home = web_client.get("/")
    assert home.status_code == 200
    assert "Taiwan Stock Data" in home.text
    assert "資料概覽" in home.text

    detail = web_client.get("/stocks/2330")
    assert detail.status_code == 200
    assert "台積電" in detail.text
    assert "融資維持率估算" in detail.text


def test_read_only_web_requests_do_not_change_database(web_client, web_db: Path):
    before = (web_db.stat().st_size, web_db.stat().st_mtime_ns)
    for url in (
        "/api/v1/health",
        "/api/v1/stocks/2330/overview",
        "/api/v1/stocks/2330/prices",
        "/api/v1/stocks/2330/margin",
        "/api/v1/stocks/2330/margin-estimates",
        "/api/v1/stocks/2330/tdcc",
        "/",
        "/stocks/2330",
    ):
        assert web_client.get(url).status_code == 200
    after = (web_db.stat().st_size, web_db.stat().st_mtime_ns)
    assert after == before


def test_missing_database_is_a_stable_error_and_is_not_created(tmp_path):
    database = tmp_path / "missing.db"
    client = TestClient(create_app(database))
    response = client.get("/api/v1/health")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"
    assert not database.exists()


def test_api_errors_use_the_documented_envelope(web_client):
    not_found = web_client.get("/api/v1/stocks/9999")
    assert not_found.status_code == 404
    assert not_found.json()["error"]["code"] == "STOCK_NOT_FOUND"

    invalid_code = web_client.get("/api/v1/stocks/23/overview")
    assert invalid_code.status_code == 400
    assert invalid_code.json()["error"]["code"] == "INVALID_STOCK_CODE"

    invalid_date = web_client.get(
        "/api/v1/stocks/2330/prices?from=2026-08-11&to=2026-08-01"
    )
    assert invalid_date.status_code == 400
    assert invalid_date.json()["error"]["code"] == "INVALID_DATE_RANGE"

    invalid_pagination = web_client.get("/api/v1/stocks/2330/prices?limit=1001")
    assert invalid_pagination.status_code == 400
    assert invalid_pagination.json()["error"]["code"] == "INVALID_PAGINATION"


def test_stock_without_optional_history_still_has_an_overview(web_client):
    response = web_client.get("/api/v1/stocks/1101/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["stock"]["stock_code"] == "1101"
    assert payload["price"] is None
    assert payload["margin"] is None
    assert payload["margin_estimate"] is None
    assert payload["tdcc"] is None

