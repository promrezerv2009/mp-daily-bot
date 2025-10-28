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

    q_orders = text("""
        SELECT
          COALESCE(SUM(qty_ordered),0)   AS orders_cnt,
          COALESCE(SUM(qty_delivered),0) AS delivered_cnt,
          COALESCE(SUM(qty_returned),0)  AS returns_cnt,
          COALESCE(SUM(price * qty_delivered),0) AS revenue_delivered
        FROM orders
        WHERE date BETWEEN :d1 AND :d2
    """)

    q_ads = text("""
        SELECT COALESCE(SUM(amount),0) AS ads_sum
        FROM ads_costs
        WHERE date BETWEEN :d1 AND :d2
    """)

    q_costs = text("""
        SELECT
          COALESCE(SUM(commission_fee),0) AS commission,
          COALESCE(SUM(logistics_fee),0)  AS logistics,
          COALESCE(SUM(storage_fee),0)    AS storage,
          COALESCE(SUM(cogs_per_unit),0)  AS cogs   -- грубо, на период (демо)
        FROM costs
        WHERE date BETWEEN :d1 AND :d2
    """)

    with _engine.begin() as conn:
        o = conn.execute(q_orders, {"d1": start_d, "d2": end_d}).mappings().one()
        a = conn.execute(q_ads, {"d1": start_d, "d2": end_d}).mappings().one()
        c = conn.execute(q_costs, {"d1": start_d, "d2": end_d}).mappings().one()

    revenue = float(o["revenue_delivered"])
    ads = float(a["ads_sum"])
    commission = float(c["commission"])
    logistics = float(c["logistics"])
    storage = float(c["storage"])
    cogs = float(c["cogs"])

    profit = revenue - (commission + logistics + storage + ads + cogs)
    romi = (revenue - ads) / ads if ads > 0 else None

    return {
        "ok": True,
        "period": period,
        "summary": {
            "revenue_delivered": revenue,
            "orders": int(o["orders_cnt"]),
            "delivered": int(o["delivered_cnt"]),
            "returns": int(o["returns_cnt"]),
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
