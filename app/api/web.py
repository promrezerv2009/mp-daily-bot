from datetime import date
from typing import Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.repos.aggregates_repo import AggregatesRepository
from app.repos.orders_repo import OrdersRepository
from app.services.periods import resolve_period

router = APIRouter()
settings = get_settings()
templates = Jinja2Templates(directory="app/templates")
tz = ZoneInfo(settings.tz)


def _platform_totals(metrics: Dict[str, Dict]) -> Dict[str, Dict]:
    totals = {
        "orders_count": 0,
        "delivered": 0,
        "returns": 0,
        "revenue_delivered": 0.0,
        "cogs": 0.0,
        "commission": 0.0,
        "logistics": 0.0,
        "storage": 0.0,
        "ads": 0.0,
        "gross_profit": 0.0,
        "profit": 0.0,
        "romi": None,
    }
    for data in metrics.values():
        totals["orders_count"] += data["orders_count"]
        totals["delivered"] += data["delivered"]
        totals["returns"] += data["returns"]
        totals["revenue_delivered"] += data["revenue_delivered"]
        totals["cogs"] += data["cogs"]
        totals["commission"] += data["commission"]
        totals["logistics"] += data["logistics"]
        totals["storage"] += data["storage"]
        totals["ads"] += data["ads"]
        totals["gross_profit"] += data["gross_profit"]
        totals["profit"] += data["profit"]
    totals["romi"] = totals["profit"] / totals["ads"] if totals["ads"] else None
    return totals


@router.get("/")
def index() -> RedirectResponse:
    return RedirectResponse(url="/yesterday")


@router.get("/yesterday")
def yesterday(request: Request, session: Session = Depends(get_session)):
    start, end, _ = resolve_period("yesterday", None, None, tz)
    return _render_dashboard(request, session, start, end, "yesterday")


@router.get("/range")
def range_view(
    request: Request,
    date_from: Optional[date] = Query(default=None, alias="from"),
    date_to: Optional[date] = Query(default=None, alias="to"),
    period: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
):
    try:
        start, end, period_enum = resolve_period(period, date_from, date_to, tz)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _render_dashboard(request, session, start, end, period_enum.value)


def _render_dashboard(
    request: Request,
    session: Session,
    start: date,
    end: date,
    period_value: str,
):
    orders_repo = OrdersRepository(session)
    metrics = orders_repo.get_metrics(start, end)
    if not metrics:
        raise HTTPException(status_code=404, detail="No data")
    aggregates_repo = AggregatesRepository(session)
    daily = aggregates_repo.list_range(start, end)
    chart = {}
    for row in daily:
        row_date = row.aggregate_date
        chart.setdefault(row_date, {"revenue": 0.0, "profit": 0.0})
        chart[row_date]["revenue"] += float(row.revenue_delivered or 0)
        chart[row_date]["profit"] += float(row.profit or 0)
    chart_data = [
        {"date": key.isoformat(), **values} for key, values in sorted(chart.items(), key=lambda item: item[0])
    ]
    totals = _platform_totals(metrics)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "period": period_value,
            "date_from": start,
            "date_to": end,
            "metrics": metrics,
            "totals": totals,
            "chart": chart_data,
        },
    )
