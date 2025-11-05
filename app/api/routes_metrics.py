# app/api/routes_metrics.py
from datetime import date, timedelta
from typing import Dict, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import create_engine, text

from app.config import get_settings

router = APIRouter()
_settings = get_settings()
_engine = create_engine(_settings.db_url, future=True)


from datetime import date, timedelta

ALIASES = {
    "yesterday": "yesterday",
    "вчера": "yesterday",
    "7": "7d", "7d": "7d", "7days": "7d",
    "14": "14d", "14d": "14d",
    "30": "30d", "30d": "30d", "month": "30d",
}

def _period_dates(period: str):
    p = (period or "yesterday").strip().lower()
    p = ALIASES.get(p, p)

    today = date.today()
    if p == "yesterday":
        d = today - timedelta(days=1)
        return d, d
    if p.endswith("d") and p[:-1].isdigit():
        days = int(p[:-1])
        return today - timedelta(days=days), today
    if ".." in p:
        a, b = p.split("..", 1)
        return date.fromisoformat(a), date.fromisoformat(b)
    # fallback — вчера
    d = today - timedelta(days=1)
    return d, d


@router.get("/metrics")
def get_metrics(period: str = Query("yesterday")) -> Dict[str, Any]:
    start_d, end_d = _period_dates(period)

    with _engine.begin() as conn:
        agg = conn.execute(
            text(
                """
                SELECT
                  COALESCE(SUM(orders_count),0)   AS orders_cnt,
                  COALESCE(SUM(delivered),0)      AS delivered_cnt,
                  COALESCE(SUM(returns),0)        AS returns_cnt,
                  COALESCE(SUM(revenue_delivered),0) AS revenue_delivered,
                  COALESCE(SUM(cogs),0)           AS cogs,
                  COALESCE(SUM(commission),0)     AS commission,
                  COALESCE(SUM(logistics),0)      AS logistics,
                  COALESCE(SUM(storage),0)        AS storage,
                  COALESCE(SUM(ads),0)            AS ads,
                  COALESCE(SUM(profit),0)         AS profit
                FROM aggregates_daily
                WHERE platform = :platform
                  AND date BETWEEN :d1 AND :d2
                """
            ),
            {"d1": start_d, "d2": end_d, "platform": "ozon"},
        ).mappings().one()

    revenue = float(agg["revenue_delivered"])
    ads = float(agg["ads"])
    commission = float(agg["commission"])
    logistics = float(agg["logistics"])
    storage = float(agg["storage"])
    cogs = float(agg["cogs"])

    profit = float(agg["profit"])
    romi = (revenue - ads) / ads if ads > 0 else None

    return {
        "ok": True,
        "period": period,
        "summary": {
            "revenue_delivered": revenue,
            "orders": int(agg["orders_cnt"]),
            "delivered": int(agg["delivered_cnt"]),
            "returns": int(agg["returns_cnt"]),
            "commission": commission,
            "logistics": logistics,
            "storage": storage,
            "cogs": cogs,
            "ads": ads,
            "profit": profit,
            "romi": romi,
        },
        "by_platform": []
    }
