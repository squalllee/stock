"""Windows-friendly local desktop console for stock data synchronization.

The desktop console intentionally uses only Tkinter from the Python standard
library.  It runs the existing synchronization services in a worker thread so
the window remains responsive while official data is being downloaded.
"""

from __future__ import annotations

import argparse
import logging
import os
import queue
import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, timedelta
from typing import Any, Mapping

from stock_master.config import (
    BILLDB_SUPABASE_URL,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_RETRY_BACKOFF_SECONDS,
    DEFAULT_SUPABASE_TDCC_BATCH_SIZE,
    DEFAULT_TIMEOUT_SECONDS,
    DEFAULT_USER_AGENT,
    TDCC_API_URL,
    load_project_dotenv,
)

from stock_master.exceptions import StockDataValidationError, SupabaseSyncError
from stock_master.providers import (
    InsiderTransferProvider,
    InsiderUntransferredProvider,
    JsonHttpClient,
    TDCCDistributionProvider,
    TPExPriceProvider,
    TWSEPriceProvider,
)
from stock_master.services.supabase_market_sync_service import (
    SupabaseMarketSyncService,
)
from stock_master.services.supabase_tdcc_sync_service import create_supabase_client

logger = logging.getLogger(__name__)


class _DesktopLogHandler(logging.Handler):
    """Forward background synchronization logs to the Tk event queue."""

    def __init__(self, events: queue.Queue[tuple[str, Any]]) -> None:
        super().__init__(level=logging.INFO)
        self.events = events

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.events.put(("log", self.format(record)))
        except Exception:  # noqa: BLE001 - logging must never break a sync
            self.handleError(record)


class _DesktopTerminalLogHandler(logging.Handler):
    """Print warning and error logs from desktop synchronization to stderr."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            print(self.format(record), file=sys.stderr, flush=True)
        except Exception:  # noqa: BLE001 - logging must never break a sync
            self.handleError(record)


@dataclass(frozen=True, slots=True)
class SyncSkipped:
    """A successful synchronization that had no new official data."""

    message: str


def _default_price_date(today: date | None = None) -> date:
    """Return today's date, moving back over a weekend when necessary."""

    current = today or date.today()
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current


def sync_stock_master(supabase_client: Any) -> Any:
    """Synchronize the official stock master directly into Supabase."""

    return _market_service(supabase_client).sync_stock_master()


def sync_daily_prices(
    supabase_client: Any,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
) -> Any:
    """Synchronize latest daily prices or an inclusive date range."""

    return _market_service(supabase_client).sync_daily_prices(start_date, end_date)


def sync_tdcc_latest(supabase_client: Any) -> Any:
    """Synchronize the latest TDCC distribution directly into Supabase."""

    try:
        result = _market_service(supabase_client).sync_tdcc_latest()
        if isinstance(result, Mapping) and result.get("skipped"):
            return SyncSkipped(
                str(result.get("reason") or "TDCC 最新一期資料已同步")
            )
        return result
    except (StockDataValidationError, SupabaseSyncError) as exc:
        message = str(exc).casefold()
        if "empty distribution" in message or "no distribution records" in message:
            return SyncSkipped("TDCC 官方目前沒有新的股權分散資料")
        raise


def sync_tdcc_year(supabase_client: Any, year: int) -> Any:
    """Synchronize one year's TDCC history directly into Supabase."""

    return _market_service(supabase_client).sync_tdcc_year(year)


def sync_insider_transactions(supabase_client: Any) -> Any:
    """Synchronize official insider transfer disclosures into Supabase."""

    result = _market_service(supabase_client).sync_insider_transactions()
    if isinstance(result, Mapping) and result.get("skipped"):
        return SyncSkipped(
            str(result.get("reason") or "官方目前沒有新的內部人申報資料")
        )
    return result


def fetch_tdcc_open_data_latest_date() -> str:
    """Read the newest data date directly from the official TDCC Open Data API."""

    client = JsonHttpClient(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
        user_agent=DEFAULT_USER_AGENT,
    )
    return TDCCDistributionProvider(
        client,
        url=TDCC_API_URL,
    ).fetch_latest_data_date()


def fetch_daily_price_api_latest_dates() -> tuple[str, str]:
    """Read the latest dates from the two official daily-price APIs."""

    client = JsonHttpClient(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
        user_agent=DEFAULT_USER_AGENT,
    )
    twse_date = TWSEPriceProvider(client).fetch_latest_data_date()
    tpex_date = TPExPriceProvider(client).fetch_latest_data_date()
    return twse_date, tpex_date


def fetch_insider_api_latest_dates() -> tuple[str, str]:
    """Read the newest report dates from TWSE and TPEx insider feeds."""

    client = JsonHttpClient(
        timeout=DEFAULT_TIMEOUT_SECONDS,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
        user_agent=DEFAULT_USER_AGENT,
    )
    twse_dates = (
        InsiderTransferProvider(client, market="TWSE").fetch_latest_data_date(),
        InsiderUntransferredProvider(client, market="TWSE").fetch_latest_data_date(),
    )
    tpex_dates = (
        InsiderTransferProvider(client, market="TPEX").fetch_latest_data_date(),
        InsiderUntransferredProvider(client, market="TPEX").fetch_latest_data_date(),
    )
    return max(twse_dates), max(tpex_dates)


def format_daily_price_api_latest_dates(dates: tuple[str, str]) -> str:
    """Format matching dates compactly and preserve a market mismatch."""

    twse_date, tpex_date = dates
    if twse_date == tpex_date:
        return twse_date
    return f"TWSE {twse_date}｜TPEx {tpex_date}"


def format_insider_api_latest_dates(dates: tuple[str, str]) -> str:
    """Format insider-feed dates while preserving a market mismatch."""

    twse_date, tpex_date = dates
    if twse_date == tpex_date:
        return twse_date
    return f"TWSE {twse_date}｜TPEx {tpex_date}"


def _market_service(supabase_client: Any) -> SupabaseMarketSyncService:
    return SupabaseMarketSyncService(
        supabase_client,
        batch_size=DEFAULT_SUPABASE_TDCC_BATCH_SIZE,
        max_attempts=DEFAULT_MAX_ATTEMPTS,
        backoff_seconds=DEFAULT_RETRY_BACKOFF_SECONDS,
    )


def _plain_result(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if is_dataclass(value):
        return asdict(value)
    return {"value": value}


def summarize_result(workflow: str, value: Any) -> str:
    """Turn a service result into a short line suitable for the UI log."""

    if isinstance(value, SyncSkipped):
        return f"完成：{value.message}"

    data = _plain_result(value)
    if workflow == "stock-master":
        return (
            "完成：股票主檔 "
            f"{data.get('total_count', 0):,} 筆（TWSE {data.get('twse_count', 0):,}、"
            f"TPEX {data.get('tpex_count', 0):,}）"
        )
    if workflow == "daily-prices":
        if data.get("start_date") is not None:
            synced_dates = data.get("synced_dates") or ()
            skipped_dates = data.get("skipped_non_trading_dates") or ()
            return (
                "完成：每日成交行情 "
                f"{data.get('price_count', 0):,} 筆，"
                f"{data.get('start_date')} ～ {data.get('end_date')}，"
                f"同步 {len(synced_dates)} 個交易日，略過 {len(skipped_dates)} 天"
            )
        if data.get("skipped_non_trading"):
            return "完成：今天沒有可同步的交易日資料"
        return (
            "完成：每日成交行情 "
            f"{data.get('price_count', 0):,} 筆，日期 {data.get('trade_date') or '—'}"
        )
    if workflow == "tdcc-latest":
        return f"完成：TDCC 最新一期 {data.get('tdcc_count', 0):,} 筆"
    if workflow == "tdcc-year":
        result = _plain_result(data.get("result"))
        dates = data.get("data_dates") or []
        return (
            f"完成：TDCC {data.get('year')} 年新增同步 "
            f"{result.get('tdcc_count', 0):,} 筆，共 {len(dates)} 個資料日期；"
            f"本次查詢 {data.get('completed_query_count', 0):,} 組，"
            f"略過 {data.get('skipped_checkpoint_count', 0):,} 組"
        )
    if workflow == "insider-transactions":
        report_types = data.get("report_type_counts") or {}
        planned_count = report_types.get("planned_transfer", 0)
        untransferred_count = report_types.get("untransferred", 0)
        return (
            "完成：內部人申報 "
            f"{data.get('record_count', 0):,} 筆（"
            f"預定轉讓 {planned_count:,}、未轉讓 {untransferred_count:,}；"
            f"最新 {data.get('latest_data_date') or '—'}）"
        )
    return "完成：同步工作已完成"


class DesktopSyncApp:
    """Tkinter application that controls one safe Supabase sync at a time."""

    WORKFLOWS: tuple[tuple[str, str, str], ...] = (
        ("stock-master", "股票主檔", "TWSE + TPEx 普通股票"),
        ("daily-prices", "每日成交行情", "同步選定日期區間"),
        ("tdcc-latest", "TDCC 最新一期", "同步官方最新股權分散"),
        ("tdcc-year", "TDCC 年度資料", "同步指定年度的每週資料"),
        (
            "insider-transactions",
            "內部人申報",
            "同步 TWSE + TPEx 預定轉讓／未轉讓",
        ),
    )

    def __init__(self, root: Any, *, supabase_client: Any, supabase_url: str) -> None:
        # Tkinter is imported lazily in run_desktop_app so importing this
        # module remains safe on headless build machines.
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.root = root
        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.supabase_client = supabase_client
        self.supabase_url = supabase_url
        self.year_var = tk.StringVar(value=str(date.today().year))
        self.default_price_date = _default_price_date()
        self.price_start_date_var = tk.StringVar(
            value=self.default_price_date.isoformat()
        )
        self.price_end_date_var = tk.StringVar(
            value=self.default_price_date.isoformat()
        )
        self.status_var = tk.StringVar(value="準備就緒")
        self.tdcc_open_data_date_var = tk.StringVar(value="查詢中……")
        self.daily_price_api_date_var = tk.StringVar(value="查詢中……")
        self.insider_api_date_var = tk.StringVar(value="查詢中……")
        self.detail_var = tk.StringVar(
            value="請先同步股票主檔，再同步 TDCC、每日成交行情或內部人申報。"
        )
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="desktop-sync",
        )
        self._metadata_executor = ThreadPoolExecutor(
            max_workers=3,
            thread_name_prefix="desktop-api-date",
        )
        self._running = False
        self._buttons: dict[str, Any] = {}
        self._log_handler = _DesktopLogHandler(self._events)
        self._log_handler.setFormatter(
            logging.Formatter("%(levelname)s %(message)s")
        )
        self._terminal_log_handler = _DesktopTerminalLogHandler()
        self._terminal_log_handler.setFormatter(
            logging.Formatter("%(levelname)s %(message)s")
        )
        self._log_logger = logging.getLogger("stock_master")
        self._previous_log_level = self._log_logger.level
        if (
            self._log_logger.level == logging.NOTSET
            or self._log_logger.level > logging.INFO
        ):
            self._log_logger.setLevel(logging.INFO)
        self._log_logger.addHandler(self._log_handler)
        self._log_logger.addHandler(self._terminal_log_handler)

        self._configure_window()
        self._build_widgets()
        self._refresh_tdcc_open_data_date()
        self._refresh_daily_price_api_dates()
        self._refresh_insider_api_dates()
        self.root.after(150, self._poll_events)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _configure_window(self) -> None:
        self.root.title("台股資料同步控制台")
        self.root.geometry("760x620")
        self.root.minsize(680, 520)
        style = self.ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except self.tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Microsoft JhengHei UI", 18, "bold"))
        style.configure("Subtitle.TLabel", foreground="#52616b")
        style.configure("Action.TButton", padding=(12, 9), font=("Microsoft JhengHei UI", 10, "bold"))

    def _build_widgets(self) -> None:
        frame = self.ttk.Frame(self.root, padding=22)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(4, weight=1)

        self.ttk.Label(frame, text="台股資料同步控制台", style="Title.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self.ttk.Label(
            frame,
            text="Windows 本機操作｜資料直接寫入 Supabase BillDB，不需要開啟瀏覽器",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(4, 16))

        supabase_frame = self.ttk.LabelFrame(
            frame, text="Supabase BillDB", padding=10
        )
        supabase_frame.grid(row=2, column=0, sticky="ew", pady=(0, 12))
        supabase_frame.columnconfigure(1, weight=1)
        self.ttk.Label(supabase_frame, text="Project URL").grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )
        self.ttk.Label(
            supabase_frame, text=self.supabase_url, style="Subtitle.TLabel"
        ).grid(row=0, column=1, sticky="w")
        self.ttk.Label(
            supabase_frame,
            text="使用環境變數 SUPABASE_SECRET_KEY（或 SUPABASE_SERVICE_ROLE_KEY）",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(5, 0))
        self.ttk.Label(supabase_frame, text="TDCC Open Data 最新資料日期").grid(
            row=2, column=0, sticky="w", padx=(0, 12), pady=(5, 0)
        )
        self.ttk.Label(
            supabase_frame,
            textvariable=self.tdcc_open_data_date_var,
            style="Subtitle.TLabel",
        ).grid(row=2, column=1, sticky="w", pady=(5, 0))
        self.ttk.Label(supabase_frame, text="每日行情 API 最新資料日期").grid(
            row=3, column=0, sticky="w", padx=(0, 12), pady=(5, 0)
        )
        self.ttk.Label(
            supabase_frame,
            textvariable=self.daily_price_api_date_var,
            style="Subtitle.TLabel",
        ).grid(row=3, column=1, sticky="w", pady=(5, 0))
        self.ttk.Label(supabase_frame, text="內部人申報 API 最新資料日期").grid(
            row=4, column=0, sticky="w", padx=(0, 12), pady=(5, 0)
        )
        self.ttk.Label(
            supabase_frame,
            textvariable=self.insider_api_date_var,
            style="Subtitle.TLabel",
        ).grid(row=4, column=1, sticky="w", pady=(5, 0))

        action_frame = self.ttk.LabelFrame(frame, text="同步操作", padding=12)
        action_frame.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        for column in range(2):
            action_frame.columnconfigure(column, weight=1)

        for index, (key, label, description) in enumerate(self.WORKFLOWS):
            row, column = divmod(index, 2)
            button = self.ttk.Button(
                action_frame,
                text=f"{label}\n{description}",
                style="Action.TButton",
                command=lambda workflow=key: self._start(workflow),
            )
            button.grid(row=row, column=column, sticky="ew", padx=5, pady=5)
            self._buttons[key] = button

        price_date_row = self.ttk.Frame(action_frame)
        price_date_row.grid(
            row=3, column=0, columnspan=2, sticky="w", padx=5, pady=(8, 0)
        )
        self.ttk.Label(price_date_row, text="每日成交行情日期：").pack(side="left")
        self.ttk.Label(price_date_row, text="起").pack(side="left", padx=(8, 3))
        self.ttk.Entry(
            price_date_row,
            width=12,
            textvariable=self.price_start_date_var,
        ).pack(side="left")
        self.ttk.Label(price_date_row, text="迄").pack(side="left", padx=(8, 3))
        self.ttk.Entry(
            price_date_row,
            width=12,
            textvariable=self.price_end_date_var,
        ).pack(side="left")
        self.ttk.Label(
            price_date_row,
            text="（格式 YYYY-MM-DD，預設最近一日）",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=(8, 0))

        year_row = self.ttk.Frame(action_frame)
        year_row.grid(row=4, column=0, columnspan=2, sticky="w", padx=5, pady=(8, 0))
        self.ttk.Label(year_row, text="TDCC 年度資料的年份：").pack(side="left")
        self.ttk.Spinbox(
            year_row,
            from_=2000,
            to=date.today().year,
            width=8,
            textvariable=self.year_var,
        ).pack(side="left")
        self.ttk.Label(
            year_row,
            text="（年度同步可能需要較長時間）",
            style="Subtitle.TLabel",
        ).pack(side="left", padx=(8, 0))

        status_frame = self.ttk.LabelFrame(frame, text="同步狀態", padding=12)
        status_frame.grid(row=4, column=0, sticky="nsew")
        status_frame.columnconfigure(0, weight=1)
        status_frame.rowconfigure(3, weight=1)
        self.ttk.Label(status_frame, textvariable=self.status_var).grid(
            row=0, column=0, sticky="w"
        )
        self.ttk.Label(
            status_frame, textvariable=self.detail_var, style="Subtitle.TLabel"
        ).grid(row=1, column=0, sticky="w", pady=(4, 8))
        self.progress = self.ttk.Progressbar(status_frame, mode="indeterminate")
        self.progress.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self.log = self.tk.Text(status_frame, height=12, wrap="word", state="disabled")
        self.log.grid(row=3, column=0, sticky="nsew")
        self.ttk.Button(status_frame, text="清除紀錄", command=self._clear_log).grid(
            row=4, column=0, sticky="e", pady=(8, 0)
        )

    def _start(self, workflow: str) -> None:
        if self._running:
            self.messagebox.showinfo("同步進行中", "目前已有同步工作，請等待完成。")
            return

        year: int | None = None
        price_start_date: date | None = None
        price_end_date: date | None = None
        if workflow == "daily-prices":
            try:
                price_start_date = date.fromisoformat(
                    self.price_start_date_var.get().strip()
                )
                price_end_date = date.fromisoformat(
                    self.price_end_date_var.get().strip()
                )
            except ValueError:
                self.messagebox.showerror(
                    "日期格式錯誤",
                    "每日成交行情日期請使用 YYYY-MM-DD，例如 2026-08-25。",
                )
                return
            if price_start_date > price_end_date:
                self.messagebox.showerror(
                    "日期區間錯誤", "起始日期不可晚於結束日期。"
                )
                return

        if workflow == "tdcc-year":
            try:
                year = int(self.year_var.get().strip())
            except ValueError:
                self.messagebox.showerror("年份錯誤", "TDCC 年份請輸入四位數字。")
                return
            if year < 2000 or year > date.today().year:
                self.messagebox.showerror(
                    "年份錯誤", f"TDCC 年份必須介於 2000 到 {date.today().year}。"
                )
                return
            if not self.messagebox.askyesno(
                "開始年度同步？",
                f"即將同步 TDCC {year} 年資料，可能需要較長時間。\n\n要繼續嗎？",
            ):
                return

        if (
            workflow == "daily-prices"
            and price_start_date == self.default_price_date
            and price_end_date == self.default_price_date
        ):
            daily_price_task = lambda: sync_daily_prices(self.supabase_client)
        else:
            daily_price_task = lambda: sync_daily_prices(
                self.supabase_client,
                price_start_date,
                price_end_date,
            )

        tasks: dict[str, Callable[[], Any]] = {
            "stock-master": lambda: sync_stock_master(self.supabase_client),
            "daily-prices": daily_price_task,
            "tdcc-latest": lambda: sync_tdcc_latest(self.supabase_client),
            "tdcc-year": lambda: sync_tdcc_year(
                self.supabase_client, year or date.today().year
            ),
            "insider-transactions": lambda: sync_insider_transactions(
                self.supabase_client
            ),
        }
        task = tasks[workflow]
        self._running = True
        self._set_buttons_enabled(False)
        label = dict((key, label) for key, label, _ in self.WORKFLOWS)[workflow]
        self.status_var.set(f"正在同步：{label}")
        self.detail_var.set("同步工作已啟動，正在等待官方資料回應……")
        self.progress.start(12)
        self._append_log(f"開始同步 {label}（Supabase BillDB）")
        future = self._executor.submit(task)
        future.add_done_callback(
            lambda completed: self._events.put(
                ("complete", (workflow, label, completed))
            )
        )

    def _refresh_tdcc_open_data_date(self) -> None:
        self.tdcc_open_data_date_var.set("查詢中……")
        future = self._metadata_executor.submit(fetch_tdcc_open_data_latest_date)
        future.add_done_callback(
            lambda completed: self._events.put(("tdcc-open-data-date", completed))
        )

    def _refresh_daily_price_api_dates(self) -> None:
        self.daily_price_api_date_var.set("查詢中……")
        future = self._metadata_executor.submit(fetch_daily_price_api_latest_dates)
        future.add_done_callback(
            lambda completed: self._events.put(("daily-price-api-dates", completed))
        )

    def _refresh_insider_api_dates(self) -> None:
        self.insider_api_date_var.set("查詢中……")
        future = self._metadata_executor.submit(fetch_insider_api_latest_dates)
        future.add_done_callback(
            lambda completed: self._events.put(("insider-api-dates", completed))
        )

    def _poll_events(self) -> None:
        try:
            while True:
                event_type, payload = self._events.get_nowait()
                if event_type == "log":
                    message = str(payload)
                    if self._running:
                        self.detail_var.set(message)
                        self._append_log(message)
                    continue

                if event_type == "tdcc-open-data-date":
                    try:
                        latest_date = payload.result()
                    except Exception as exc:  # noqa: BLE001 - keep startup usable
                        logger.warning(
                            "Could not read TDCC Open Data latest date: %s", exc
                        )
                        self.tdcc_open_data_date_var.set("讀取失敗")
                    else:
                        self.tdcc_open_data_date_var.set(latest_date)
                    continue

                if event_type == "daily-price-api-dates":
                    try:
                        latest_dates = payload.result()
                    except Exception as exc:  # noqa: BLE001 - keep startup usable
                        logger.warning(
                            "Could not read daily price API latest dates: %s", exc
                        )
                        self.daily_price_api_date_var.set("讀取失敗")
                    else:
                        self.daily_price_api_date_var.set(
                            format_daily_price_api_latest_dates(latest_dates)
                        )
                    continue

                if event_type == "insider-api-dates":
                    try:
                        latest_dates = payload.result()
                    except Exception as exc:  # noqa: BLE001 - keep startup usable
                        logger.warning(
                            "Could not read insider API latest dates: %s", exc
                        )
                        self.insider_api_date_var.set("讀取失敗")
                    else:
                        self.insider_api_date_var.set(
                            format_insider_api_latest_dates(latest_dates)
                        )
                    continue

                if event_type != "complete":
                    continue
                workflow, label, future = payload
                self._running = False
                self.progress.stop()
                self._set_buttons_enabled(True)
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 - show every sync error in UI
                    logger.exception("Desktop synchronization failed")
                    self.status_var.set(f"同步失敗：{label}")
                    self.detail_var.set(str(exc))
                    self._append_log(f"失敗：{exc}")
                    self.messagebox.showerror("同步失敗", str(exc))
                else:
                    summary = summarize_result(workflow, result)
                    self.status_var.set(summary)
                    self.detail_var.set("同步工作已完成，可以開始下一項操作。")
                    self._append_log(summary)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_events)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self._buttons.values():
            button.configure(state=state)

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"{date.today().isoformat()}  {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _close(self) -> None:
        if self._running:
            self.messagebox.showwarning("同步進行中", "同步尚未完成，請等待工作結束後再關閉視窗。")
            return
        self._log_logger.removeHandler(self._log_handler)
        self._log_logger.removeHandler(self._terminal_log_handler)
        self._log_logger.setLevel(self._previous_log_level)
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._metadata_executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def run_desktop_app(supabase_url: str | None = None) -> int:
    """Open the local Tkinter window and return when it is closed."""

    try:
        import tkinter as tk
    except ImportError as exc:
        raise RuntimeError(
            "目前 Python 沒有安裝 Tkinter；Windows 請改用 python.org 的完整 Python 安裝程式。"
        ) from exc

    load_project_dotenv()
    url = (
        supabase_url
        or os.environ.get("SUPABASE_URL")
        or BILLDB_SUPABASE_URL
    )
    key = (
        os.environ.get("SUPABASE_SECRET_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    )
    client = create_supabase_client(url, key or "")
    root = tk.Tk()
    DesktopSyncApp(root, supabase_client=client, supabase_url=url)
    root.mainloop()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Open the local stock sync console.")
    parser.add_argument(
        "--supabase-url",
        default=None,
        help="Supabase project URL (default: SUPABASE_URL or BillDB URL)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        return run_desktop_app(args.supabase_url)
    except (OSError, RuntimeError, SupabaseSyncError, ValueError) as exc:
        print(f"無法啟動本機同步介面：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
