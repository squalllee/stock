"""HTML page routes for the read-only stock Web platform."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..errors import WebError

router = APIRouter(include_in_schema=False)


def _templates(request: Request):
    return request.app.state.templates


def _error_page(request: Request, error: WebError) -> HTMLResponse:
    return _templates(request).TemplateResponse(
        request=request,
        name="error.html",
        context={"error": error},
        status_code=error.status_code,
    )


@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    service = request.app.state.query_service
    try:
        summary = service.get_dashboard_summary()
        stocks = service.search_stocks(limit=8)
    except WebError as exc:
        return _error_page(request, exc)
    return _templates(request).TemplateResponse(
        request=request,
        name="index.html",
        context={"summary": summary, "stocks": stocks},
    )


@router.get("/stocks", response_class=HTMLResponse)
def stock_list(request: Request, q: str = "", market: str | None = None) -> HTMLResponse:
    try:
        stocks = request.app.state.query_service.search_stocks(
            q,
            market=market,
            limit=100,
        )
    except WebError as exc:
        return _error_page(request, exc)
    return _templates(request).TemplateResponse(
        request=request,
        name="stocks.html",
        context={"stocks": stocks, "query": q, "market": market or ""},
    )


@router.get("/stocks/{stock_code}", response_class=HTMLResponse)
def stock_detail(request: Request, stock_code: str) -> HTMLResponse:
    try:
        stock = request.app.state.query_service.get_stock(stock_code)
    except WebError as exc:
        return _error_page(request, exc)
    return _templates(request).TemplateResponse(
        request=request,
        name="stock_detail.html",
        context={"stock": stock},
    )


@router.get("/about", response_class=HTMLResponse)
def about(request: Request) -> HTMLResponse:
    return _templates(request).TemplateResponse(
        request=request,
        name="about.html",
        context={},
    )

