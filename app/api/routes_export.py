from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.repos.orders_repo import OrdersRepository
from app.services.periods import resolve_period
from app.services.reporting import generate_excel_report

router = APIRouter(prefix="/api")
settings = get_settings()
tz = ZoneInfo(settings.tz)


@router.get("/export")
def export_report(
    period: Optional[str] = Query(default=None),
    date_from: Optional[date] = Query(default=None, alias="from"),
    date_to: Optional[date] = Query(default=None, alias="to"),
    session: Session = Depends(get_session),
) -> FileResponse:
    try:
        start, end, _ = resolve_period(period, date_from, date_to, tz)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    repo = OrdersRepository(session)
    metrics = repo.get_metrics(start, end)
    if not metrics:
        raise HTTPException(status_code=404, detail="No data for period")
    top_sku = repo.get_top_sku(start, end)

    report_path = generate_excel_report(
        metrics,
        top_sku,
        datetime.combine(start, datetime.min.time()),
        datetime.combine(end, datetime.min.time()),
    )
    return FileResponse(
        path=report_path,
        filename=report_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
