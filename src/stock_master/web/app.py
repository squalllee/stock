"""FastAPI application factory for the read-only stock Web platform."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from stock_master.config import DEFAULT_DATABASE_PATH
from stock_master.exceptions import DatabaseError
from stock_master.repositories import (
    MarginEstimateRepository,
    MarginHistoryRepository,
    PriceHistoryRepository,
    StockRepository,
    TDCCDistributionRepository,
)

from .errors import WebError, error_payload
from .routes import api, pages
from .services import StockQueryService

_WEB_DIR = Path(__file__).resolve().parent


def create_app(db_path: str | Path = DEFAULT_DATABASE_PATH) -> FastAPI:
    """Build a Web app whose repositories are all opened read-only."""

    app = FastAPI(
        title="Taiwan Stock Data",
        version="1.0.0",
        description="Read-only dashboard for Taiwan stock history and distributions.",
    )
    app.state.db_path = Path(db_path)
    app.state.templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
    app.state.query_service = StockQueryService(
        stock_repository=StockRepository(db_path, readonly=True),
        price_repository=PriceHistoryRepository(db_path, readonly=True),
        margin_repository=MarginHistoryRepository(db_path, readonly=True),
        margin_estimate_repository=MarginEstimateRepository(db_path, readonly=True),
        tdcc_repository=TDCCDistributionRepository(db_path, readonly=True),
    )
    app.mount(
        "/static",
        StaticFiles(directory=str(_WEB_DIR / "static")),
        name="static",
    )
    app.include_router(api.router)
    app.include_router(pages.router)

    @app.exception_handler(WebError)
    async def handle_web_error(_request: Request, exc: WebError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc),
        )

    @app.exception_handler(DatabaseError)
    async def handle_database_error(
        _request: Request, exc: DatabaseError
    ) -> JSONResponse:
        error = WebError("DATABASE_UNAVAILABLE", str(exc), 500)
        return JSONResponse(status_code=500, content=error_payload(error))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        error = WebError(
            "INVALID_QUERY",
            _validation_message(exc),
            400,
        )
        return JSONResponse(status_code=400, content=error_payload(error))

    return app


def _validation_message(error: RequestValidationError) -> str:
    details = error.errors()
    if not details:
        return "Invalid request parameters."
    first = details[0]
    location = first.get("loc", ())
    field = location[-1] if location else "request"
    return f"Invalid value for {field}."
