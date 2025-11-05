import os, json, requests, psycopg2
from collections import defaultdict
from datetime import date, timedelta, datetime
from pathlib import Path
import csv

from app.services.loaders.ozon_api import OzonApiClient


def load_thresholds() -> dict[str, float]:
    path = Path(__file__).resolve().parent.parent / "data" / "thresholds.csv"
    prices: dict[str, float] = {}
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh, delimiter=";"):
                sku = (row.get("sku") or "").strip()
                cogs = row.get("cogs") or row.get("min_price") or "0"
                if sku:
                    prices[sku] = float(str(cogs).replace(",", "."))
    return prices


def upsert_orders(day: date, conn) -> None:
    start_dt = datetime.combine(day, datetime.min.time())
    end_dt = datetime.combine(day, datetime.max.time())
    postings = client.fetch_orders(start_dt, end_dt)

    order_rows = []
    cost_rows = defaultdict(lambda: {"commission": 0.0, "logistics": 0.0, "storage": 0.0, "cogs": 0.0})
    for posting in postings:
        analytics = posting.get("analytics_data") or {}
        fdata = posting.get("financial_data") or {}
        products = fdata.get("products") or []
        posting_number = posting.get("posting_number") or posting.get("postingNumber")
        order_date = (analytics.get("processed_at") or analytics.get("created_at") or posting.get("in_process_at") or posting.get("created_at"))
        order_date = datetime.fromisoformat(order_date.replace("Z", "+00:00")).date() if order_date else day

        for product in products:
            sku = str(product.get("sku") or analytics.get("sku") or product.get("offer_id") or "")
            if not sku:
                continue
            key_id = f"ozon::{posting_number}::{sku}"
            price = float(product.get("price") or analytics.get("price") or 0)
            qty_ordered = int(product.get("quantity")) if product.get("quantity") is not None else 0
            qty_delivered = int(product.get("quantity_delivered")) if product.get("quantity_delivered") is not None else qty_ordered
            qty_returned = int(product.get("quantity_returned") or 0)

            order_rows.append(
                (key_id, "ozon", order_date, sku, price, qty_ordered, qty_delivered, qty_returned)
            )

            # комиссии/логистика по SKU
            item_services = product.get("item_services") or []
            for service in item_services:
                stype = service.get("name")
                amount = float(service.get("price") or 0)
                if stype in {"MarketplaceRedistributionOfAcquiringOperation", "OperationMarketplaceSalesFee", "OperationMarketplaceSalesCommission"}:
                    cost_rows[(order_date, sku)]["commission"] += amount
                elif stype in {"OperationMarketplaceDeliveryToCustomer", "OperationMarketplaceDeliveryReturn"}:
                    cost_rows[(order_date, sku)]["logistics"] += amount
                elif stype in {"OperationMarketplaceServiceStorage", "StorageItemOperation"}:
                    cost_rows[(order_date, sku)]["storage"] += amount

            # себестоимость из справочника
            cogs_per_unit = thresholds.get(sku, 0.0)
            cost_rows[(order_date, sku)]["cogs"] += cogs_per_unit * qty_delivered

    with conn, conn.cursor() as cur:
        for row in order_rows:
            cur.execute("""
                INSERT INTO orders(id, platform, date, sku, price, qty_ordered, qty_delivered, qty_returned)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    price = EXCLUDED.price,
                    qty_ordered = EXCLUDED.qty_ordered,
                    qty_delivered = EXCLUDED.qty_delivered,
                    qty_returned = EXCLUDED.qty_returned
            """, row)

        for (cost_date, sku), values in cost_rows.items():
            cur.execute("""
                INSERT INTO costs(platform, date, sku, commission_fee, logistics_fee, storage_fee, cogs_per_unit)
                VALUES ('ozon', %s, %s, %s, %s, %s, %s)
                ON CONFLICT (platform, date, sku)
                DO UPDATE SET
                  commission_fee = EXCLUDED.commission_fee,
                  logistics_fee = EXCLUDED.logistics_fee,
                  storage_fee = EXCLUDED.storage_fee,
                  cogs_per_unit = EXCLUDED.cogs_per_unit
            """, (
                cost_date,
                sku,
                round(values["commission"], 2),
                round(values["logistics"], 2),
                round(values["storage"], 2),
                round(values["cogs"], 2),
            ))
try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

if load_dotenv:
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)

CID, KEY = os.getenv("OZON_CLIENT_ID"), os.getenv("OZON_API_KEY")
if not CID or not KEY:
    raise SystemExit("нет OZON_CLIENT_ID / OZON_API_KEY")

HDR = {"Client-Id": CID, "Api-Key": KEY, "Content-Type": "application/json"}
URL = "https://api-seller.ozon.ru/v3/finance/transaction/list"

DB_HOST = os.getenv("DB_HOST","localhost")
DB_USER = os.getenv("DB_USER","postgres")
DB_PASS = os.getenv("POSTGRES_PASSWORD", os.getenv("DB_PASSWORD","postgres"))
DB_NAME = os.getenv("DB_NAME","mpdaily")

thresholds = load_thresholds()
client = OzonApiClient(
    base_url=os.getenv("OZON_API_URL", "https://api-seller.ozon.ru"),
    client_id=CID,
    api_key=KEY,
)

def kind_from(op: dict) -> str:
    t = (op.get("operation_type") or op.get("operationType") or "").strip()
    # Выручка за доставку
    if t in ("OperationAgentDeliveredToCustomer",):
        return "revenue_delivery"
    # Комиссии/эквайринг
    if t in (
        "MarketplaceRedistributionOfAcquiringOperation",
        "OperationMarketplaceSalesCommission",
        "OperationMarketplaceSalesPercent",
        "OperationMarketplaceSalesFee",
        "MarketplaceRedistributionOfAcquiringOperation",
        "PremiumMembership",
        "StarsMembership",
    ):
        return "commission"
    # Логистика: доставка до клиента, возвраты и т.д.
    if t in (
        "SellerReturnsDeliveryToPickupPoint",
        "ClientReturnAgentOperation",
        "OperationItemReturn",
        "OperationSellerReturnsCargoAssortmentValid",
        "OperationMarketplaceDeliveryToCustomer",
        "OperationMarketplaceDeliveryReturn",
    ):
        return "logistics"
    # Хранение
    if t in (
        "StorageItemOperation",
        "OperationMarketplaceStorageItemService",
        "OperationMarketplaceServiceStorage",
    ):
        return "storage"
    # Реклама
    if t in ("OperationMarketplaceCostPerClick",):
        return "ads"
    # Остальное
    return "other"

def fetch_ops_for_day(day_iso: str):
    page = 1
    ops_all = []
    while True:
        body = {
            "page": page,
            "page_size": 1000,
            "filter": {"date": {"from": f"{day_iso}T00:00:00Z", "to": f"{day_iso}T23:59:59Z"}}
        }
        r = requests.post(URL, headers=HDR, json=body, timeout=60)
        r.raise_for_status()
        result = (r.json() or {}).get("result") or {}
        ops = result.get("operations") or []
        ops_all.extend(ops)
        total = int(result.get("total", 0))
        if len(ops_all) >= total or not ops:
            break
        page += 1
    return ops_all

def upsert_ops_and_recalc(day_iso: str, conn):
    day = date.fromisoformat(day_iso)
    upsert_orders(day, conn)
    ops = fetch_ops_for_day(day_iso)
    inserted = updated = 0
    with conn, conn.cursor() as cur:
        for o in ops:
            oid  = str(o.get("operation_id") or o.get("operationId") or "")
            amt  = float(o.get("amount") or 0)
            desc = o.get("description") or o.get("operation_name") or ""
            kind = kind_from(o)
            cur.execute("""
                INSERT INTO ozon_finance_ops(platform, op_id, op_date, amount, kind, description, payload)
                VALUES ('ozon', %s, %s::date, %s, %s, %s, %s)
                ON CONFLICT (op_id) DO UPDATE
                  SET amount=EXCLUDED.amount, kind=EXCLUDED.kind, description=EXCLUDED.description, payload=EXCLUDED.payload
            """, (oid, day_iso, amt, kind, desc, json.dumps(o, ensure_ascii=False)))
            if cur.rowcount == 1: inserted += 1
            else: updated += 1

        # Пересчёт агрегатов на этот день
        cur.execute(
            """
            SELECT
              COALESCE(SUM(qty_ordered),0)::numeric(14,2)   AS orders_cnt,
              COALESCE(SUM(qty_delivered),0)::numeric(14,2) AS delivered_cnt,
              COALESCE(SUM(qty_returned),0)::numeric(14,2)  AS returns_cnt
            FROM orders
            WHERE platform='ozon' AND date=%s
            """,
            (day,),
        )
        orders_totals = cur.fetchone() or (0, 0, 0)

        cur.execute(
            """
            SELECT
              COALESCE(SUM(commission_fee),0)::numeric(14,2) AS commission,
              COALESCE(SUM(logistics_fee),0)::numeric(14,2)  AS logistics,
              COALESCE(SUM(storage_fee),0)::numeric(14,2)    AS storage,
              COALESCE(SUM(cogs_per_unit),0)::numeric(14,2)  AS cogs
            FROM costs
            WHERE platform='ozon' AND date=%s
            """,
            (day,),
        )
        cost_totals = cur.fetchone() or (0, 0, 0, 0)

        cur.execute(
            """
            SELECT COALESCE(SUM(amount),0)::numeric(14,2) AS ads
            FROM ads_costs
            WHERE platform='ozon' AND date=%s
            """,
            (day,),
        )
        ads_total = cur.fetchone()[0]

        cur.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN kind='revenue_delivery' AND amount>0 THEN amount ELSE 0 END),0)::numeric(14,2) AS revenue,
              COALESCE(SUM(CASE WHEN kind='other' AND amount<0 THEN -amount ELSE 0 END),0)::numeric(14,2)          AS other_costs
            FROM ozon_finance_ops
            WHERE platform='ozon' AND op_date=%s
            """,
            (day,),
        )
        finance_totals = cur.fetchone() or (0, 0)

        revenue = float(finance_totals[0] or 0)
        other_costs = float(finance_totals[1] or 0)
        commission = float(cost_totals[0] or 0)
        logistics = float(cost_totals[1] or 0)
        storage = float(cost_totals[2] or 0)
        cogs_amount = float(cost_totals[3] or 0)
        ads_amount = float(ads_total or 0)

        gross_profit = revenue - cogs_amount
        profit = revenue - (cogs_amount + commission + logistics + storage + ads_amount + other_costs)
        romi = None if ads_amount == 0 else (revenue - ads_amount) / ads_amount

        cur.execute(
            """
            INSERT INTO aggregates_daily (
              platform, date,
              orders_count, delivered, returns,
              revenue_delivered, cogs,
              commission, logistics, storage, ads,
              gross_profit, profit, romi
            )
            VALUES (
              'ozon', %s,
              %s, %s, %s,
              %s, %s,
              %s, %s, %s, %s,
              %s, %s, %s
            )
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
            """,
            (
                day,
                float(orders_totals[0] or 0),
                float(orders_totals[1] or 0),
                float(orders_totals[2] or 0),
                revenue,
                cogs_amount,
                commission,
                logistics,
                storage,
                ads_amount,
                gross_profit,
                profit,
                romi,
            ),
        )


    print(f"{day_iso}: ops={len(ops)} ins={inserted} upd={updated}")

def daterange(d1: date, d2: date):
    cur = d1
    while cur <= d2:
        yield cur
        cur += timedelta(days=1)

if __name__ == "__main__":
    # параметры: from..to (или без — тогда вчера)
    import sys
    if len(sys.argv) == 3:
        d1 = date.fromisoformat(sys.argv[1])
        d2 = date.fromisoformat(sys.argv[2])
    else:
        y = date.today() - timedelta(days=1)
        d1 = d2 = y

    conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASS, host=DB_HOST)
    for d in daterange(d1, d2):
        upsert_ops_and_recalc(d.isoformat(), conn)

    # контрольный вывод по суммам за диапазон
    with conn, conn.cursor() as cur:
        cur.execute("""
        WITH d AS (
          SELECT %s::date AS d1, %s::date AS d2
        ),
        ops AS (
          SELECT
            COALESCE(SUM(CASE WHEN kind='revenue_delivery' AND amount>0 THEN amount ELSE 0 END),0)::numeric(14,2) AS revenue,
            COALESCE(SUM(CASE WHEN kind='other'            AND amount<0 THEN -amount ELSE 0 END),0)::numeric(14,2) AS other_costs
          FROM ozon_finance_ops, d
          WHERE platform='ozon' AND op_date BETWEEN d.d1 AND d.d2
        ),
        agg AS (
          SELECT
            COALESCE(SUM(commission),0)::numeric(14,2) AS commission,
            COALESCE(SUM(logistics),0)::numeric(14,2)  AS logistics,
            COALESCE(SUM(storage),0)::numeric(14,2)    AS storage,
            COALESCE(SUM(ads),0)::numeric(14,2)        AS ads,
            COALESCE(SUM(profit),0)::numeric(14,2)     AS profit
          FROM aggregates_daily, d
          WHERE platform='ozon' AND date BETWEEN d.d1 AND d.d2
        )
        SELECT ops.revenue, ops.other_costs, agg.commission, agg.logistics, agg.storage, agg.ads, agg.profit
        FROM ops, agg;
        """, (d1, d2))
        print("RANGE_SUMS:", cur.fetchone())
    print("DONE")
