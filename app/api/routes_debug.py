# app/api/routes_debug.py
from datetime import date, timedelta
from fastapi import APIRouter
from sqlalchemy import create_engine, text
from app.config import get_settings

router = APIRouter()
_engine = create_engine(get_settings().db_url, future=True)

def _seed():
    y = date.today() - timedelta(days=1)
    with _engine.begin() as conn:
        # orders: 3 доставлено, 1 возврат (id уникальны — не дублируются)
        conn.execute(text("""
            INSERT INTO orders (id, platform, date, sku, price, qty_ordered, qty_delivered, qty_returned)
            VALUES
            ('o1','ozon', :d, 'SKU1', 1000, 3, 3, 0),
            ('o2','wb',   :d, 'SKU2', 1000, 1, 0, 1)
            ON CONFLICT (id) DO UPDATE SET
              platform=EXCLUDED.platform,
              date=EXCLUDED.date,
              sku=EXCLUDED.sku,
              price=EXCLUDED.price,
              qty_ordered=EXCLUDED.qty_ordered,
              qty_delivered=EXCLUDED.qty_delivered,
              qty_returned=EXCLUDED.qty_returned
        """), {"d": y})

        # ads_costs: фиксируем sku='SEED' и делаем UPSERT по (platform,date,sku)
        conn.execute(text("""
            INSERT INTO ads_costs (platform, sku, date, amount)
            VALUES ('ozon', 'SEED', :d, 500)
            ON CONFLICT (platform, date, sku) DO UPDATE SET
              amount = EXCLUDED.amount
        """), {"d": y})

        # costs: тоже UPSERT по (platform,date,sku)
        conn.execute(text("""
            INSERT INTO costs (platform, sku, date, commission_fee, logistics_fee, storage_fee, cogs_per_unit)
            VALUES ('ozon', 'SKU1', :d, 300, 200, 100, 900)
            ON CONFLICT (platform, date, sku) DO UPDATE SET
              commission_fee = EXCLUDED.commission_fee,
              logistics_fee  = EXCLUDED.logistics_fee,
              storage_fee    = EXCLUDED.storage_fee,
              cogs_per_unit  = EXCLUDED.cogs_per_unit
        """), {"d": y})

    return {"ok": True, "seeded_for": str(y)}


@router.post("/debug/seed")
def seed_post():
    return _seed()

@router.get("/debug/seed")
def seed_get():
    return _seed()
