from datetime import date

from stock_master.services import AllDataSyncService


def test_all_data_sync_runs_steps_in_dependency_order_and_reports_progress():
    calls = []
    progress = []

    def callback(key):
        def run(start_date, end_date):
            calls.append((key, start_date, end_date))

        return run

    service = AllDataSyncService(
        price_latest_sync=callback("price-latest"),
        margin_latest_sync=callback("margin-latest"),
        tdcc_latest_sync=callback("tdcc-latest"),
        margin_estimate_sync=callback("margin-estimate"),
    )

    result = service.sync(
        date(2026, 8, 12),
        progress=lambda key, status: progress.append((key, status)),
    )

    expected_keys = [key for key, _label in service.STEP_DEFINITIONS]
    expected_start = date(2026, 8, 12)
    assert [key for key, _start, _end in calls] == expected_keys
    assert all(start == expected_start for _key, start, _end in calls)
    assert all(end == date(2026, 8, 12) for _key, _start, end in calls)
    assert progress == [
        item
        for key in expected_keys
        for item in ((key, "running"), (key, "completed"))
    ]
    assert result == {
        "sync_date": "2026-08-12",
        "start_date": "2026-08-12",
        "end_date": "2026-08-12",
        "completed_steps": expected_keys,
        "skipped_steps": [],
    }


def test_all_data_sync_marks_optional_tdcc_as_skipped():
    progress = []

    service = AllDataSyncService(
        price_latest_sync=lambda _start, _end: None,
        margin_latest_sync=lambda _start, _end: None,
        tdcc_latest_sync=lambda _start, _end: {"skipped": True},
        margin_estimate_sync=lambda _start, _end: None,
    )

    result = service.sync(
        date(2026, 8, 12),
        progress=lambda key, status: progress.append((key, status)),
    )

    assert result["completed_steps"] == [
        "price-latest",
        "margin-latest",
        "margin-estimate",
    ]
    assert result["skipped_steps"] == ["tdcc-latest"]
    assert ("tdcc-latest", "skipped") in progress
