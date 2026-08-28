"""Orchestration for the complete data synchronization workflow."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Any

ProgressCallback = Callable[[str, str], None]
SyncCallback = Callable[[date, date], Any]


class AllDataSyncService:
    """Run the latest-data workflow in dependency order.

    The callbacks keep this service independent from HTTP/provider wiring. The
    Web layer can therefore construct providers with its own runtime settings,
    while tests can inject small deterministic callbacks.
    """

    STEP_DEFINITIONS: tuple[tuple[str, str], ...] = (
        ("price-latest", "最新成交行情"),
        ("margin-latest", "最新融資融券"),
        ("tdcc-latest", "TDCC 最新資料"),
        ("margin-estimate", "最新融資維持率估算"),
    )

    def __init__(
        self,
        *,
        price_latest_sync: SyncCallback,
        margin_latest_sync: SyncCallback,
        tdcc_latest_sync: SyncCallback,
        margin_estimate_sync: SyncCallback,
    ) -> None:
        self._callbacks = {
            "price-latest": price_latest_sync,
            "margin-latest": margin_latest_sync,
            "tdcc-latest": tdcc_latest_sync,
            "margin-estimate": margin_estimate_sync,
        }

    def sync(
        self,
        end_date: date | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Run all steps for one date and return the completed step keys."""

        sync_date = end_date or date.today()
        completed: list[str] = []
        skipped: list[str] = []

        for key, _label in self.STEP_DEFINITIONS:
            if progress:
                progress(key, "running")
            result = self._callbacks[key](sync_date, sync_date)
            if isinstance(result, dict) and result.get("skipped"):
                skipped.append(key)
                step_status = "skipped"
            else:
                completed.append(key)
                step_status = "completed"
            if progress:
                progress(key, step_status)

        return {
            "sync_date": sync_date.isoformat(),
            "start_date": sync_date.isoformat(),
            "end_date": sync_date.isoformat(),
            "completed_steps": completed,
            "skipped_steps": skipped,
        }
