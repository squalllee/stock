"""Versioned JSON API routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from stock_master.config import DEFAULT_MARGIN_MODEL_VERSION

from ..errors import WebError

router = APIRouter(prefix="/api/v1", tags=["api"])


def _service(request: Request) -> Any:
    return request.app.state.query_service


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    return _service(request).health()


@router.get("/stocks")
def stocks(
    request: Request,
    q: str = "",
    market: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    items = _service(request).search_stocks(
        q,
        market=market,
        limit=limit,
        offset=offset,
    )
    return {
        "items": [_stock(item) for item in items],
        "limit": limit,
        "offset": offset,
        "has_more": len(items) == limit,
    }


@router.get("/stocks/search")
def search_stocks(
    request: Request,
    q: str = Query(default=""),
    market: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> dict[str, Any]:
    return stocks(request, q=q, market=market, limit=limit, offset=offset)


@router.get("/stocks/{stock_code}/overview")
def stock_overview(request: Request, stock_code: str) -> dict[str, Any]:
    return _service(request).get_overview(stock_code)


@router.get("/stocks/{stock_code}/prices")
def stock_prices(
    request: Request,
    stock_code: str,
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    return _service(request).get_price_history(
        stock_code,
        start_date=from_date,
        end_date=to_date,
        limit=limit,
        offset=offset,
    )


@router.get("/stocks/{stock_code}/margin")
def stock_margin(
    request: Request,
    stock_code: str,
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    return _service(request).get_margin_history(
        stock_code,
        start_date=from_date,
        end_date=to_date,
        limit=limit,
        offset=offset,
    )


@router.get("/stocks/{stock_code}/margin-estimates/latest")
def latest_margin_estimate(
    request: Request,
    stock_code: str,
    model_version: str = DEFAULT_MARGIN_MODEL_VERSION,
) -> dict[str, Any]:
    estimate = _service(request).get_latest_margin_estimate(
        stock_code,
        model_version=model_version,
    )
    if estimate is None:
        raise WebError(
            "NO_MARGIN_DATA",
            f"No margin estimate data is available for {stock_code}.",
            404,
        )
    return estimate


@router.get("/stocks/{stock_code}/margin-estimates")
def stock_margin_estimates(
    request: Request,
    stock_code: str,
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    limit: int = 100,
    offset: int = 0,
    model_version: str = DEFAULT_MARGIN_MODEL_VERSION,
) -> dict[str, Any]:
    return _service(request).get_margin_estimates(
        stock_code,
        start_date=from_date,
        end_date=to_date,
        limit=limit,
        offset=offset,
        model_version=model_version,
    )


@router.get("/stocks/{stock_code}/tdcc")
def stock_tdcc(
    request: Request,
    stock_code: str,
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    return _service(request).get_tdcc_history(
        stock_code,
        start_date=from_date,
        end_date=to_date,
        limit=limit,
        offset=offset,
    )


@router.get("/stocks/{stock_code}/tdcc/latest")
def latest_tdcc(request: Request, stock_code: str) -> dict[str, Any]:
    return _service(request).get_latest_tdcc(stock_code)


@router.get("/stocks/{stock_code}")
def stock(request: Request, stock_code: str) -> dict[str, Any]:
    return {"stock": _stock(_service(request).get_stock(stock_code))}


def _stock(value: Any) -> dict[str, str]:
    return {
        "stock_code": value.stock_code,
        "stock_name": value.stock_name,
        "market": value.market,
    }
