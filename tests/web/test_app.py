from __future__ import annotations

from pathlib import Path
import time
from threading import Event

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
    assert "tdcc-high-chart" in detail.text
    assert "tdcc-low-chart" in detail.text
    assert "14 級距以上持股比例總和" in detail.text
    assert "6 級距以下持股比例總和" in detail.text
    overview = web_client.get("/api/v1/stocks/2330/overview").json()
    assert "margin_limit" in overview["margin"]
    assert "margin_utilization" in overview["margin"]
    home = web_client.get("/")
    assert "同步所有資料" in home.text
    assert "data-sync-all" in home.text


def test_all_data_sync_api_runs_a_background_job(web_client, monkeypatch):
    manager = web_client.app.state.sync_jobs

    def fake_runner(*, progress):
        progress("price-latest", "running")
        progress("price-latest", "completed")
        return {
            "sync_date": "2026-08-12",
            "start_date": "2026-08-12",
            "end_date": "2026-08-12",
            "completed_steps": ["price-latest"],
            "skipped_steps": [],
        }

    monkeypatch.setattr(manager, "_runner", fake_runner)
    started = web_client.post("/api/v1/sync/all")
    assert started.status_code == 202
    job_id = started.json()["job_id"]

    for _ in range(30):
        status = web_client.get(f"/api/v1/sync/all/{job_id}")
        if status.json()["status"] == "completed":
            break
        time.sleep(0.01)

    assert status.status_code == 200
    payload = status.json()
    assert payload["status"] == "completed"
    assert payload["result"]["completed_steps"] == ["price-latest"]
    assert payload["steps"][0]["status"] == "completed"


def test_all_data_sync_api_rejects_a_second_running_job(web_client, monkeypatch):
    manager = web_client.app.state.sync_jobs
    started_event = Event()
    release_event = Event()

    def blocking_runner(*, progress):
        started_event.set()
        release_event.wait(timeout=2)
        return {"completed_steps": []}

    monkeypatch.setattr(manager, "_runner", blocking_runner)
    first = web_client.post("/api/v1/sync/all")
    assert first.status_code == 202
    assert started_event.wait(timeout=1)

    second = web_client.post("/api/v1/sync/all")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "SYNC_IN_PROGRESS"

    release_event.set()


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
