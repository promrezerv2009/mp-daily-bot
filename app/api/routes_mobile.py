from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.schemas_mobile import (
    MobileBreakevenResponse,
    MobileProductsResponse,
    MobileStockForecastResponse,
    MobileSummary,
)
from app.services.mobile import MobileService, MobileProductsResult

router = APIRouter(prefix="/api/mobile", tags=["mobile"])
settings = get_settings()
ALLOWED_SORTS = {"margin"}


def _resolve_tenant_id(request: Request, tenant_id: Optional[str]) -> Optional[str]:
    header_value = request.headers.get(settings.tenant_header)
    return tenant_id or header_value


def get_mobile_service(session: Session = Depends(get_session)) -> MobileService:
    return MobileService(session)


@router.get("/summary", response_model=MobileSummary)
def mobile_summary(
    request: Request,
    period: str = Query("7d", pattern="^(1d|7d|30d)$"),
    tenant_id: Optional[str] = Query(default=None, alias="tenant_id"),
    service: MobileService = Depends(get_mobile_service),
) -> MobileSummary:
    identifier = _resolve_tenant_id(request, tenant_id)
    try:
        return service.get_summary(period=period, tenant_id=identifier)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/products", response_model=MobileProductsResponse)
def mobile_products(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
    sort: str = Query("margin"),
    tenant_id: Optional[str] = Query(default=None, alias="tenant_id"),
    service: MobileService = Depends(get_mobile_service),
) -> MobileProductsResponse:
    identifier = _resolve_tenant_id(request, tenant_id)
    if sort not in ALLOWED_SORTS:
        raise HTTPException(status_code=400, detail="Unsupported sort mode.")
    skip = 0
    if cursor:
        try:
            skip = int(cursor)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid cursor value.") from exc

    result: MobileProductsResult = service.get_products(
        limit=limit,
        cursor=skip,
        sort=sort,
        tenant_id=identifier,
    )
    return MobileProductsResponse(items=result.items, next_cursor=result.next_cursor)


@router.get("/stocks/forecast", response_model=MobileStockForecastResponse)
def mobile_stock_forecast(
    request: Request,
    days: int = Query(30, ge=1, le=180),
    limit: int = Query(50, ge=1, le=200),
    tenant_id: Optional[str] = Query(default=None, alias="tenant_id"),
    service: MobileService = Depends(get_mobile_service),
) -> MobileStockForecastResponse:
    identifier = _resolve_tenant_id(request, tenant_id)
    return service.get_stock_forecast(days=days, limit=limit, tenant_id=identifier)


@router.get("/finance/breakeven", response_model=MobileBreakevenResponse)
def mobile_finance_breakeven(
    request: Request,
    cabinet: Optional[str] = Query(default=None),
    tenant_id: Optional[str] = Query(default=None, alias="tenant_id"),
    service: MobileService = Depends(get_mobile_service),
) -> MobileBreakevenResponse:
    identifier = _resolve_tenant_id(request, tenant_id)
    # TODO: use cabinet parameter once dedicated billing configs are available.
    return service.get_finance_breakeven(period="30d", tenant_id=identifier)
