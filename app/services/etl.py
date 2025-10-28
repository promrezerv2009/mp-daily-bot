# app/services/etl.py
from datetime import date, timedelta
from typing import Tuple
from sqlalchemy import create_engine, text
from app.config import get_settings

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


def refresh(period: str = "yesterday") -> dict:
    d1, d2 = _period_dates(period)

    # TODO: здесь позже подключим реальные загрузчики Ozon/WB:
    # load_ozon_orders(d1, d2); load_wb_orders(d1, d2); load_ads(...); load_costs(...)

    # Пересчёт агрегатов в aggregates_daily (upsert по платформам и общей строке)
    with _engine.begin() as conn:
        # По всем платформам вместе
        agg_all = conn.execute(text("""
            WITH o AS (
              SELECT
                COALESCE(SUM(qty_ordered),0)   AS orders_cnt,
                COALESCE(SUM(qty_delivered),0) AS delivered_cnt,
                COALESCE(SUM(qty_returned),0)  AS returns_cnt,
                COALESCE(SUM(price * qty_delivered),0)::numeric AS revenue
              FROM orders WHERE date BETWEEN :d1 AND :d2
            ),
            a AS (
              SELECT COALESCE(SUM(amount),0)::numeric AS ads FROM ads_costs WHERE date BETWEEN :d1 AND :d2
            ),
            c AS (
              SELECT
                COALESCE(SUM(commission_fee),0)::numeric AS commission,
                COALESCE(SUM(logistics_fee),0)::numeric  AS logistics,
                COALESCE(SUM(storage_fee),0)::numeric    AS storage,
                COALESCE(SUM(cogs_per_unit),0)::numeric  AS cogs
              FROM costs WHERE date BETWEEN :d1 AND :d2
            )
            SELECT o.orders_cnt, o.delivered_cnt, o.returns_cnt, o.revenue, a.ads, c.commission, c.logistics, c.storage, c.cogs
            FROM o CROSS JOIN a CROSS JOIN c
        """), {"d1": d1, "d2": d2}).mappings().one()

        # Запишем одну строку “общая” с платформой 'ozon' условно (или 'wb' — не важно для дашборда),
        # лучше использовать специальное значение 'all' — но у нас CHECK, поэтому сложим в обе платформы одинаково.
        for platform in ("ozon", "wb"):
            profit = agg_all["revenue"] - (agg_all["commission"] + agg_all["logistics"] + agg_all["storage"] + agg_all["ads"] + agg_all["cogs"])
            romi = None if float(agg_all["ads"]) == 0 else (float(agg_all["revenue"]) - float(agg_all["ads"])) / float(agg_all["ads"])
            conn.execute(text("""
                INSERT INTO aggregates_daily (
                  platform, date, orders_count, delivered, returns, revenue_delivered,
                  cogs, commission, logistics, storage, ads, gross_profit, profit, romi
                )
                VALUES (:platform, :date, :orders_count, :delivered, :returns, :revenue,
                        :cogs, :commission, :logistics, :storage, :ads, :gross_profit, :profit, :romi)
                ON CONFLICT (platform, date) DO UPDATE SET
                  orders_count = EXCLUDED.orders_count,
                  delivered = EXCLUDED.delivered,
                  returns = EXCLUDED.returns,
                  revenue_delivered = EXCLUDED.revenue_delivered,
                  cogs = EXCLUDED.cogs,
                  commission = EXCLUDED.commission,
                  logistics = EXCLUDED.logistics,
                  storage = EXCLUDED.storage,
                  ads = EXCLUDED.ads,
                  gross_profit = EXCLUDED.gross_profit,
                  profit = EXCLUDED.profit,
                  romi = EXCLUDED.romi
            """), {
                "platform": platform,
                "date": d1,  # для 'yesterday' d1==d2
                "orders_count": int(agg_all["orders_cnt"]),
                "delivered": int(agg_all["delivered_cnt"]),
                "returns": int(agg_all["returns_cnt"]),
                "revenue": agg_all["revenue"],
                "cogs": agg_all["cogs"],
                "commission": agg_all["commission"],
                "logistics": agg_all["logistics"],
                "storage": agg_all["storage"],
                "ads": agg_all["ads"],
                "gross_profit": agg_all["revenue"] - agg_all["cogs"],
                "profit": profit,
                "romi": romi
            })

    return {"ok": True, "period": period, "refreshed": f"{d1}..{d2}"}
