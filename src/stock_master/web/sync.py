"""Background synchronization wiring for the Web dashboard."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Callable
from uuid import uuid4

from stock_master.config import (
    DEFAULT_MARGIN_FINANCING_RATIO,
    DEFAULT_MARGIN_MODEL_VERSION,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_PRICE_HISTORY_REQUEST_DELAY_SECONDS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    TDCC_API_URL,
    TPEX_MARGIN_URL,
    TPEX_PRICE_URL,
    TWSE_MARGIN_URL,
    TWSE_PRICE_URL,
)
from stock_master.providers import (
    JsonHttpClient,
    TDCCDistributionProvider,
    TPExMarginProvider,
    TPExPriceProvider,
    TWSEMarginProvider,
    TWSEPriceProvider,
)
from stock_master.repositories import (
    MarginEstimateRepository,
    MarginHistoryRepository,
    PriceHistoryRepository,
    StockRepository,
    TDCCDistributionRepository,
)
from stock_master.exceptions import StockDataValidationError
from stock_master.services import (
    AllDataSyncService,
    MarginCostEstimator,
    MarginSyncService,
    PriceSyncService,
    TDCCSyncService,
)

logger = logging.getLogger(__name__)


class SyncAlreadyRunning(RuntimeError):
    """Raised when a second complete sync is requested before the first ends."""


class SyncJobNotFound(KeyError):
    """Raised when a status request refers to an unknown or expired job."""


def build_all_data_sync_service(db_path: str | Path) -> AllDataSyncService:
    """Build the complete sync workflow using the application's defaults."""

    client = JsonHttpClient(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
        user_agent=DEFAULT_USER_AGENT,
    )

    stock_repository = StockRepository(db_path)
    tdcc_repository = TDCCDistributionRepository(db_path)
    margin_repository = MarginHistoryRepository(db_path)
    price_repository = PriceHistoryRepository(db_path)
    estimate_repository = MarginEstimateRepository(db_path)

    tdcc_latest_sync = TDCCSyncService(
        TDCCDistributionProvider(client, url=TDCC_API_URL),
        stock_repository,
        tdcc_repository,
    )
    margin_sync = MarginSyncService(
        TWSEMarginProvider(client, url=TWSE_MARGIN_URL),
        TPExMarginProvider(client, url=TPEX_MARGIN_URL),
        stock_repository,
        margin_repository,
    )
    price_sync = PriceSyncService(
        TWSEPriceProvider(client, url=TWSE_PRICE_URL),
        TPExPriceProvider(
            client,
            url=TPEX_PRICE_URL,
            request_delay_seconds=DEFAULT_PRICE_HISTORY_REQUEST_DELAY_SECONDS,
        ),
        stock_repository,
        price_repository,
    )
    estimator = MarginCostEstimator(
        margin_repository,
        price_repository,
        estimate_repository,
        margin_financing_ratio=DEFAULT_MARGIN_FINANCING_RATIO,
        model_version=DEFAULT_MARGIN_MODEL_VERSION,
    )

    def sync_no_range(callback: Callable[[], Any]) -> Callable[[Any, Any], Any]:
        return lambda _start, _end: callback()

    def sync_tdcc_if_available(_start, _end):
        try:
            return tdcc_latest_sync.sync()
        except StockDataValidationError as exc:
            message = str(exc).casefold()
            if "empty distribution" in message or "no distribution records" in message:
                logger.info("TDCC has no new distribution data; skipping this sync")
                return {"skipped": True, "reason": "TDCC 暫無新資料"}
            raise

    def sync_latest_estimate(_start, _end):
        latest_margin_date = margin_repository.get_latest_trade_date()
        latest_price_date = price_repository.get_latest_trade_date()
        if not latest_margin_date or not latest_price_date:
            return []
        latest_common_date = min(latest_margin_date, latest_price_date)
        return estimator.estimate_all(latest_common_date, latest_common_date)

    return AllDataSyncService(
        price_latest_sync=sync_no_range(price_sync.sync),
        margin_latest_sync=sync_no_range(margin_sync.sync),
        tdcc_latest_sync=sync_tdcc_if_available,
        margin_estimate_sync=sync_latest_estimate,
    )


class SyncJobManager:
    """Run one complete sync in a worker and expose a thread-safe status."""

    def __init__(
        self,
        runner: Callable[..., dict[str, Any]],
        step_definitions: tuple[tuple[str, str], ...],
    ) -> None:
        self._runner = runner
        self._step_definitions = step_definitions
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="stock-master-sync",
        )
        self._lock = Lock()
        self._job: dict[str, Any] | None = None

    def start(self) -> dict[str, Any]:
        """Queue a complete sync and return its initial status."""

        with self._lock:
            if self._job and self._job["status"] in {"queued", "running"}:
                raise SyncAlreadyRunning
            job_id = uuid4().hex
            now = _timestamp()
            self._job = {
                "job_id": job_id,
                "status": "queued",
                "current_step": None,
                "message": "同步工作已排入佇列",
                "steps": [
                    {"key": key, "label": label, "status": "pending"}
                    for key, label in self._step_definitions
                ],
                "result": None,
                "error": None,
                "created_at": now,
                "updated_at": now,
            }
            self._executor.submit(self._run, job_id)
            return self._snapshot_locked()

    def get(self, job_id: str) -> dict[str, Any]:
        """Return the current status for one job."""

        with self._lock:
            if self._job is None or self._job["job_id"] != job_id:
                raise SyncJobNotFound(job_id)
            return self._snapshot_locked()

    def shutdown(self) -> None:
        """Stop accepting work when the Web application is shutting down."""

        self._executor.shutdown(wait=False, cancel_futures=True)

    def _run(self, job_id: str) -> None:
        self._update(job_id, status="running", message="開始同步所有資料")
        try:
            result = self._runner(
                progress=lambda key, status: self._progress(job_id, key, status)
            )
        except Exception as exc:  # noqa: BLE001 - the job must report all failures
            self._fail(job_id, str(exc))
            return
        self._update(
            job_id,
            status="completed",
            current_step=None,
            message="所有資料同步完成",
            result=result,
            error=None,
        )

    def _progress(self, job_id: str, key: str, status: str) -> None:
        with self._lock:
            if self._job is None or self._job["job_id"] != job_id:
                return
            for step in self._job["steps"]:
                if step["key"] == key:
                    step["status"] = status
                    self._job["current_step"] = key
                    self._job["message"] = next(
                        (
                            f"正在同步 {step['label']}"
                            if status == "running"
                            else f"略過 {step['label']}"
                            if status == "skipped"
                            else f"已完成 {step['label']}"
                        )
                        for candidate in self._job["steps"]
                        if candidate["key"] == key
                    )
                    break
            self._job["updated_at"] = _timestamp()

    def _fail(self, job_id: str, message: str) -> None:
        with self._lock:
            if self._job is None or self._job["job_id"] != job_id:
                return
            current = self._job["current_step"]
            for step in self._job["steps"]:
                if step["key"] == current:
                    step["status"] = "failed"
                    break
            self._job.update(
                status="failed",
                message="同步失敗",
                error=message or "同步工作失敗",
                updated_at=_timestamp(),
            )

    def _update(self, job_id: str, **values: Any) -> None:
        with self._lock:
            if self._job is None or self._job["job_id"] != job_id:
                return
            self._job.update(values, updated_at=_timestamp())

    def _snapshot_locked(self) -> dict[str, Any]:
        return deepcopy(self._job)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
