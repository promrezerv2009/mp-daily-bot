# app/main.py
import uvicorn
import os
import requests
import psycopg2
import csv
OZ_HOST = "https://api-seller.ozon.ru"
from math import isfinite
from typing import List, Dict
from fastapi import Query
from datetime import date, timedelta
from fastapi import FastAPI
from app.config import get_settings
from app.logging_conf import setup_logging
from app.db import init_db


# роуты
from app.api.routes_metrics import router as metrics_router
from app.api.routes_export import router as export_router
from app.api.health import router as health_router
from app.api.routes_debug import router as debug_router
from app.api.routes_refresh import router as refresh_router
settings = get_settings()
setup_logging(settings.log_level)

app = FastAPI(title="mp-daily-bot")

# Подключаем роутеры
app.include_router(health_router)          # /health
app.include_router(metrics_router, prefix="/api")
app.include_router(export_router, prefix="/api")
app.include_router(debug_router, prefix="/api")
app.include_router(refresh_router, prefix="/api")

@app.post("/api/perf/ads/update_range")
def perf_ads_update_range(period: str = Query("7d")):
    import os, requests, psycopg2
    from datetime import date, timedelta

    client_id = os.getenv("OZON_PERF_CLIENT_ID")
    client_secret = os.getenv("OZON_PERF_CLIENT_SECRET")
    if not client_id or not client_secret:
        return {"ok": False, "error": "нет OZONE_PERF_CLIENT_ID/SECRET в .env"}

    def get_token():
        r = requests.post(
            "https://performance.ozon.ru/api/client/token",
            json={"client_id": client_id, "client_secret": client_secret},
            timeout=30
        )
        j = r.json()
        return j.get("access_token")

    token = get_token()
    if not token:
        return {"ok": False, "error": "не получили access_token"}

    today = date.today()
    if period == "yesterday":
        start = end = today - timedelta(days=1)
    elif period == "7d":
        end = today; start = end - timedelta(days=6)
    elif period == "14d":
        end = today; start = end - timedelta(days=13)
    elif period == "month":
        end = today; start = today.replace(day=1)
    elif ".." in period:
        s,e = period.split("..",1); start,end = date.fromisoformat(s), date.fromisoformat(e)
    else:
        start = end = today - timedelta(days=1)

    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )

    def rub(s):
        try: return float(str(s).replace(" ", "").replace("\u00A0","").replace(",", "."))
        except: return 0.0

    saved = 0
    cur = start
    while cur <= end:
        day = cur.isoformat()
        # JSON-вариант — меньше плясок с CSV/кодировкой
        url = f"https://performance.ozon.ru/api/client/statistics/expense/json?date={day}&dateTo={day}"
        r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, timeout=60)
        if not r.ok:
            return {"ok": False, "status": r.status_code, "text": r.text[:400], "day": day}

        j = r.json()
        rows = j.get("rows") or []
        spent = sum(rub(x.get("moneySpent", 0)) for x in rows)

        with psycopg2.connect(dsn) as conn, conn.cursor() as c:
            c.execute("""
                INSERT INTO ads_costs(platform, sku, date, amount)
                VALUES ('ozon','OZ-AGG', %s, %s)
                ON CONFLICT (platform, sku, date) DO UPDATE
                SET amount = EXCLUDED.amount;
            """, (day, round(spent,2)))
            conn.commit()

        saved += 1
        cur += timedelta(days=1)

    return {"ok": True, "saved_days": saved, "period": period}


@app.post("/api/ozon/finance/save_other_fees")
def ozon_finance_save_other_fees(period: str = Query("7d")):
    import os, requests, psycopg2
    from datetime import date, timedelta

    client_id = os.getenv("OZON_CLIENT_ID"); api_key = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "нет OZON_CLIENT_ID / OZON_API_KEY"}

    today = date.today()
    if period == "yesterday":
        date_from, date_to = today - timedelta(days=1), today - timedelta(days=1)
    elif period.endswith("d") and period[:-1].isdigit():
        d = int(period[:-1])
        date_from, date_to = today - timedelta(days=d), today - timedelta(days=1)
    else:
        date_from, date_to = today - timedelta(days=7), today - timedelta(days=1)

    def iso_z(dd, t): return f"{dd.isoformat()}T{t}Z"
    url = f"{OZ_HOST}/v3/finance/transaction/list"
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    body = {
        "page": 1, "page_size": 1000,
        "filter": {
            "date": {"from": iso_z(date_from, "00:00:00"), "to": iso_z(date_to, "23:59:59")},
            "transaction_type": "all"
        }
    }

    r = requests.post(url, json=body, headers=headers, timeout=60)
    if not r.ok:
        try:
            return {"ok": False, "status": r.status_code, **r.json()}
        except Exception:
            return {"ok": False, "status": r.status_code, "text": r.text}

    j = r.json()
    ops = (j.get("result") or {}).get("operations") or []

    # исключаем то, что считаем отдельно: реклама, логистика, хранение
    EXCLUDE = [
        "доставка покупателю",
        "доставка и обработка возврата",
        "кросс-докинг",
        "вывоз товара со склада",
        "подготовка товара к вывозу",
        "размещение товара на складе",
        "услуга размещения",
        "хранение",
        "оплата за клик",
        "продвижение с оплатой за заказ",
    ]

    other_total_by_day = {}
    for op in ops:
        name = str(op.get("operation_type_name") or op.get("operation_name") or op.get("type") or "").lower()
        amount = float(op.get("amount") or 0.0)
        # берём только расходы
        if amount >= 0:
            continue
        # дата операции (ISO-строка → yyyy-mm-dd)
        dt_raw = str(op.get("operation_date") or op.get("date") or "")
        day = (dt_raw[:10] if dt_raw else date_to.isoformat())

        if any(k in name for k in EXCLUDE):
            continue

        # сюда попадут: премиум подписки, эквайринг, fbo-обработка, «звёздные товары», «закрепление отзыва», рассрочка и т.д.
        other_total_by_day[day] = round(other_total_by_day.get(day, 0.0) + abs(amount), 2)

    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        for day, total in other_total_by_day.items():
            cur.execute("""
                INSERT INTO costs (platform, sku, date, commission_fee, logistics_fee, storage_fee, cogs_per_unit)
                VALUES ('ozon','OTHER', %s, 0,0,0, %s)
                ON CONFLICT (platform, sku, date) DO UPDATE
                SET cogs_per_unit = EXCLUDED.cogs_per_unit;
            """, (day, total))
        conn.commit()

    return {"ok": True, "period": period, "days": len(other_total_by_day), "total": round(sum(other_total_by_day.values()), 2)}



# --- Импорт себестоимости (COGS) и минимальной цены из CSV ---
@app.post("/api/cogs/recalc_range_from_analytics")
def cogs_recalc_range_from_analytics(days: int = Query(7)):
    client_id = os.getenv("OZON_CLIENT_ID"); api_key = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "нет OZON_CLIENT_ID / OZON_API_KEY"}

    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}
    url = f"{OZ_HOST}/v1/analytics/data"

    def fetch_delivered(day_str):
        bodies = [
            {"date_from": day_str, "date_to": day_str, "dimension": ["offer_id"], "metrics": ["delivered_units"], "limit": 1000, "offset": 0},
            {"date_from": day_str, "date_to": day_str, "dimension": ["sku"],      "metrics": ["delivered_units"], "limit": 1000, "offset": 0},
        ]
        delivered_by_key = {}
        for body in bodies:
            try:
                r = requests.post(url, json=body, headers=headers, timeout=60)
                j = r.json()
            except Exception:
                continue
            data = (j.get("result") or {}).get("data") or []
            for row in data:
                dims = row.get("dimensions") or []
                mets = row.get("metrics") or []
                if not dims or not mets: 
                    continue
                dim0 = dims[0]
                if isinstance(dim0, dict):
                    key = str(dim0.get("id") or dim0.get("offer_id") or dim0.get("sku") or "").strip()
                else:
                    key = str(dim0).strip()
                try:
                    delivered = int(float(mets[0] or 0))
                except:
                    delivered = 0
                if delivered > 0 and key:
                    delivered_by_key[key] = delivered
            if delivered_by_key:
                break
        return delivered_by_key

    saved = []
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        for i in range(days):
            day = (date.today() - timedelta(days=i+1)).isoformat()
            delivered_by_key = fetch_delivered(day)
            total_cogs = 0.0
            for key, delivered in delivered_by_key.items():
                # пробуем найти COGS по ключу (offer_id/sku)
                cur.execute("SELECT cogs_per_unit FROM sku_cogs WHERE platform='ozon' AND sku=%s", (key,))
                row = cur.fetchone()
                cogs = float(row[0]) if row else None
                if cogs is None:
                    cur.execute("SELECT cogs_per_unit FROM sku_cogs WHERE platform='ozon' AND lower(sku)=lower(%s)", (key,))
                    row = cur.fetchone()
                    cogs = float(row[0]) if row else 0.0
                total_cogs += cogs * float(delivered or 0)
            cur.execute("""
                INSERT INTO costs (platform, sku, date, commission_fee, logistics_fee, storage_fee, cogs_per_unit)
                VALUES ('ozon','OZ-AGG', %s, 0,0,0, %s)
                ON CONFLICT (platform, sku, date) DO UPDATE
                SET cogs_per_unit = EXCLUDED.cogs_per_unit;
            """, (day, round(total_cogs,2)))
            saved.append({"date": day, "cogs_total": round(total_cogs,2)})

    return {"ok": True, "saved": saved[:10], "days": days}

# --- COGS: пересчёт за вчера по SKU из analytics (без orders) ---
@app.post("/api/cogs/recalc_yesterday_from_analytics")
def cogs_recalc_yesterday_from_analytics():
    client_id = os.getenv("OZON_CLIENT_ID"); api_key = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "нет OZON_CLIENT_ID / OZON_API_KEY"}

    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )

    day = (date.today() - timedelta(days=1)).isoformat()
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}
    url = f"{OZ_HOST}/v1/analytics/data"

    # Попробуем разные варианты, т.к. схемы у аккаунтов отличаются
    bodies = [
        # A: распространённый вариант
        {"date_from": day, "date_to": day, "dimension": ["offer_id"], "metrics": ["delivered_units"], "limit": 1000, "offset": 0},
        # B: иногда метрика называется иначе
        {"date_from": day, "date_to": day, "dimension": ["offer_id"], "metrics": ["delivered"], "limit": 1000, "offset": 0},
        # C: измерение sku
        {"date_from": day, "date_to": day, "dimension": ["sku"], "metrics": ["delivered_units"], "limit": 1000, "offset": 0},
        {"date_from": day, "date_to": day, "dimension": ["sku"], "metrics": ["delivered"], "limit": 1000, "offset": 0},
    ]

    delivered_by_key = {}  # ключ может быть offer_id или sku (numeric)
    tried = []
    for body in bodies:
        try:
            r = requests.post(url, json=body, headers=headers, timeout=60)
            j = r.json()
            tried.append({"status": r.status_code, "keys": list(j.keys())})
        except Exception as e:
            continue

        data = (j.get("result") or {}).get("data") or []
        if not data:
            continue

        # Разбираем строки (dimensions могут быть объектами с id/name)
        for row in data:
            dims = row.get("dimensions") or []
            mets = row.get("metrics") or []
            if not dims or not mets:
                continue

            dim0 = dims[0]
            if isinstance(dim0, dict):
                key = str(dim0.get("id") or dim0.get("offer_id") or dim0.get("sku") or "").strip()
            else:
                key = str(dim0).strip()

            # первая метрика — доставленные штуки
            try:
                delivered = int(float((mets[0] or 0)))
            except Exception:
                delivered = 0

            if delivered > 0 and key:
                delivered_by_key[key] = delivered


        if delivered_by_key:
            break  # достаточно первого сработавшего варианта

    # Если ничего не получили — выходим с подсказкой
    if not delivered_by_key:
        return {"ok": False, "saved": False, "hint": "analytics пустой (нет доставок или другая схема метрик/измерений)", "tried": tried}

    # Складываем COGS: ищем сначала точное совпадение в sku_cogs по offer_id,
    # потом пробуем по numeric sku (вдруг ключ число), затем по lower()
    total_cogs = 0.0
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        for key, delivered in delivered_by_key.items():
            # 1) прямой матч по ключу (он теперь равен id/offer_id/sku из analytics)
            cur.execute("SELECT cogs_per_unit FROM sku_cogs WHERE platform='ozon' AND sku=%s", (key,))
            row = cur.fetchone()
            cogs = float(row[0]) if row else None

            # 2) если не нашли — пробуем case-insensitive
            if cogs is None:
                cur.execute("SELECT cogs_per_unit FROM sku_cogs WHERE platform='ozon' AND lower(sku)=lower(%s)", (key,))
                row = cur.fetchone()
                cogs = float(row[0]) if row else None

            # 3) если всё ещё нет — 0
            if cogs is None:
                cogs = 0.0

            total_cogs += cogs * float(delivered or 0)


        # сохраняем как агрегат в costs на OZ-AGG
        cur.execute("""
            INSERT INTO costs (platform, sku, date, commission_fee, logistics_fee, storage_fee, cogs_per_unit)
            VALUES ('ozon','OZ-AGG', %s, 0,0,0, %s)
            ON CONFLICT (platform, sku, date) DO UPDATE
            SET cogs_per_unit = EXCLUDED.cogs_per_unit;
        """, (day, round(total_cogs,2)))

    return {"ok": True, "date": day, "cogs_total": round(total_cogs,2), "keys": list(delivered_by_key.keys())[:10]}


# --- OZON: получить пары offer_id ↔ numeric sku и продублировать COGS ---
@app.post("/api/ozon/catalog_map_dup_cogs")
def ozon_catalog_map_dup_cogs():
    client_id = os.getenv("OZON_CLIENT_ID"); api_key = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "нет OZON_CLIENT_ID / OZON_API_KEY"}

    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    paths = [
        f"{OZ_HOST}/v1/product/list",       # часто отдаёт items[{ offer_id, product_id/sku }]
        f"{OZ_HOST}/v2/product/list",       # альтернативный
        f"{OZ_HOST}/v2/product/info/list",  # ещё вариант
    ]
    def bodies(page, page_size):
        # попробуем разные схемы
        yield {"page": page, "page_size": page_size, "filter": {"visibility": "ALL"}}
        yield {"page": page, "page_size": page_size}
        yield {"filter": {"visibility": "ALL"}, "last_id": str((page-1)*page_size)}

    duplicated = 0
    page_size = 100
    for url in paths:
        page = 1
        while True:
            got = False
            for body in bodies(page, page_size):
                try:
                    r = requests.post(url, json=body, headers=headers, timeout=60)
                    j = r.json()
                except Exception:
                    continue
                items = (j.get("result") or {}).get("items") or []
                if not items and isinstance(j.get("result"), list):
                    items = j["result"]
                if not items:
                    continue

                # дублируем COGS: из sku_cogs по offer_id -> запись по numeric sku
                with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                    for it in items:
                        offer = str(it.get("offer_id") or it.get("offerId") or "").strip()
                        num_sku = it.get("sku") or it.get("product_id") or it.get("id")
                        if not offer or not num_sku:
                            continue
                        # берём cogs/min_price с offer_id
                        cur.execute("SELECT cogs_per_unit, min_price FROM sku_cogs WHERE platform='ozon' AND sku=%s", (offer,))
                        row = cur.fetchone()
                        if not row:
                            continue
                        cogs, minp = float(row[0] or 0), float(row[1] or 0)
                        cur.execute("""
                            INSERT INTO sku_cogs(platform, sku, cogs_per_unit, min_price)
                            VALUES ('ozon', %s, %s, %s)
                            ON CONFLICT (platform, sku) DO UPDATE
                            SET cogs_per_unit = EXCLUDED.cogs_per_unit,
                                min_price = EXCLUDED.min_price,
                                updated_at = now();
                        """, (str(num_sku), round(cogs,2), round(minp,2)))
                        duplicated += 1
                got = True
                break
            if not got:
                break
            page += 1
            if page > 200:  # защита от бесконечного цикла
                break

    return {"ok": True, "duplicated_numeric": duplicated}


# --- Импорт COGS из CSV + автодублирование по числовому SKU Ozon ---
@app.post("/api/cogs/import_csv_plus")
def import_cogs_csv_plus(path: str = Query("/srv/app/data/thresholds.csv")):
    client_id = os.getenv("OZON_CLIENT_ID"); api_key = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "нет OZON_CLIENT_ID / OZON_API_KEY"}

    # читаем CSV (как в «умной» версии: ; или , и рус/англ заголовки)
    import csv
    def norm(s): return (s or "").strip().lower()
    with open(path, "rb") as fb:
        sample = fb.read(4096)
    try:
        dialect = csv.Sniffer().sniff(sample.decode("utf-8", errors="ignore"))
    except Exception:
        class dialect: delimiter = ';'

    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )

    offer_ids = []   # соберём сюда offer_id/sku из файла для запроса в Ozon
    rows_cache = {}  # offer_id -> (cogs, min_price)
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=getattr(dialect, "delimiter", ';'))
        fields_norm = {norm(k): k for k in (reader.fieldnames or [])}

        sku_keys  = {"sku","offer_id","артикул","seller_sku","seller sku","offer id"}
        cogs_keys = {"cogs","себестоимость","себестоимость, руб","cogs, rub","cogs_rub"}
        min_keys  = {"min_price","min price","минимальная цена","min_price_rub"}

        def pick(keys):
            for k in keys:
                if k in fields_norm: 
                    return fields_norm[k]
            for fk in fields_norm:
                for k in keys:
                    if k in fk:
                        return fields_norm[fk]
            return None

        col_sku  = pick(sku_keys)
        col_cogs = pick(cogs_keys)
        col_min  = pick(min_keys)

        if not col_sku:
            return {"ok": False, "error": "Не найдена колонка SKU/offer_id/Артикул", "fields": reader.fieldnames}

        def to_float(v):
            if v is None: return 0.0
            s = str(v).replace(" ", "").replace("\u00A0","").replace(",", ".")
            try: return float(s)
            except: return 0.0

        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sku_cogs (
                    platform text NOT NULL,
                    sku text NOT NULL,
                    cogs_per_unit numeric(12,2) DEFAULT 0,
                    min_price numeric(12,2) DEFAULT 0,
                    updated_at timestamptz DEFAULT now(),
                    PRIMARY KEY (platform, sku)
                );
            """)

            # 1) сразу сохраняем по offer_id/sku из файла (как есть)
            imported = 0
            for row in reader:
                offer = str(row.get(col_sku, "")).strip()
                if not offer:
                    continue
                cogs = to_float(row.get(col_cogs)) if col_cogs else 0.0
                minp = to_float(row.get(col_min)) if col_min else 0.0

                cur.execute("""
                    INSERT INTO sku_cogs(platform, sku, cogs_per_unit, min_price)
                    VALUES ('ozon', %s, %s, %s)
                    ON CONFLICT (platform, sku) DO UPDATE
                    SET cogs_per_unit = EXCLUDED.cogs_per_unit,
                        min_price = EXCLUDED.min_price,
                        updated_at = now();
                """, (offer, round(cogs,2), round(minp,2)))
                imported += 1

                offer_ids.append(offer)
                rows_cache[offer] = (round(cogs,2), round(minp,2))

    # 2) пробуем получить соответствие offer_id -> numeric sku разными телами/путями
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}
    paths = [
        f"{OZ_HOST}/v2/product/info/list",
        f"{OZ_HOST}/v2/product/info",            # иногда работает с "product_id"/"offer_id" в корне
    ]
    def bodies(batch):
        yield {"offer_id": batch}                                 # вариант A (docs v2)
        yield {"filter": {"offer_id": batch}}                      # вариант B
        yield {"offer_id": batch, "product_id": [], "sku": []}     # вариант C (расширенный)
    duplicated = 0

    def chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    for batch in chunks(list(set(offer_ids)), 100):
        got = False
        for url in paths:
            for body in bodies(batch):
                try:
                    r = requests.post(url, json=body, headers=headers, timeout=60)
                    j = r.json()
                except Exception:
                    continue
                items = (j.get("result") or {}).get("items") or []
                if not items and isinstance(j.get("result"), list):
                    items = j["result"]
                if not items:
                    continue  # пробуем следующий вариант

                # получили items → дублируем
                with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
                    for it in items:
                        offer = str(it.get("offer_id") or it.get("offerId") or "").strip()
                        num_sku = it.get("sku") or it.get("product_id") or it.get("id")
                        if not offer or not num_sku:
                            continue
                        cogs, minp = rows_cache.get(offer, (0.0, 0.0))
                        cur.execute("""
                            INSERT INTO sku_cogs(platform, sku, cogs_per_unit, min_price)
                            VALUES ('ozon', %s, %s, %s)
                            ON CONFLICT (platform, sku) DO UPDATE
                            SET cogs_per_unit = EXCLUDED.cogs_per_unit,
                                min_price = EXCLUDED.min_price,
                                updated_at = now();
                        """, (str(num_sku), cogs, minp))
                        duplicated += 1
                got = True
                break
            if got:
                break

    return {"ok": True, "imported_offer_id": imported, "duplicated_numeric": duplicated}


# --- DEBUG: показать доставленные вчера SKU и есть ли для них COGS ---
@app.get("/api/debug/yesterday_skus")
def debug_yesterday_skus():
    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )
    day = (date.today() - timedelta(days=1)).isoformat()
    out = []
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        # какие SKU вчера доставились
        cur.execute("""
            SELECT o.sku, COALESCE(SUM(o.qty_delivered),0) AS delivered
            FROM orders o
            WHERE o.platform='ozon' AND o.date=%s
            GROUP BY o.sku
            HAVING COALESCE(SUM(o.qty_delivered),0) > 0
            ORDER BY delivered DESC, o.sku
        """, (day,))
        rows = cur.fetchall()

        for sku, delivered in rows:
            # пробуем найти COGS по точному SKU
            cur.execute("SELECT cogs_per_unit, min_price FROM sku_cogs WHERE platform='ozon' AND sku=%s", (sku,))
            row = cur.fetchone()
            if row:
                out.append({"sku": sku, "delivered": int(delivered), "cogs_per_unit": float(row[0]), "min_price": float(row[1]), "match": "direct"})
                continue

            # если не нашли — попробуем «на всякий»: обрезать пробелы/регистр
            sku_norm = str(sku).strip()
            cur.execute("SELECT sku, cogs_per_unit, min_price FROM sku_cogs WHERE platform='ozon' AND lower(sku)=lower(%s)", (sku_norm,))
            row = cur.fetchone()
            if row:
                out.append({"sku": sku, "delivered": int(delivered), "cogs_per_unit": float(row[1]), "min_price": float(row[2]), "match": "lower()"})
            else:
                out.append({"sku": sku, "delivered": int(delivered), "cogs_per_unit": 0.0, "min_price": 0.0, "match": "no_cogs"})
    return {"ok": True, "date": day, "items": out}
# --- DEBUG: сравнение SKU из заказов vs sku_cogs ---
@app.get("/api/debug/compare_keys")
def debug_compare_keys(period: str = Query("7d")):
    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )

    today = date.today()
    if period == "yesterday":
        start, end = today - timedelta(days=1), today - timedelta(days=1)
    elif period.endswith("d") and period[:-1].isdigit():
        days = int(period[:-1])
        start, end = today - timedelta(days=days-1), today
    else:
        start, end = today - timedelta(days=6), today

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            WITH rng AS (
              SELECT %s::date AS d1, %s::date AS d2
            ),
            ord AS (
              SELECT o.sku, SUM(o.qty_delivered) AS delivered
              FROM orders o, rng
              WHERE o.platform='ozon' AND o.date BETWEEN rng.d1 AND rng.d2
              GROUP BY o.sku
            )
            SELECT
              o.sku,
              COALESCE(o.delivered,0)::int AS delivered,
              EXISTS (
                SELECT 1 FROM sku_cogs c WHERE c.platform='ozon' AND c.sku = o.sku
              ) AS has_cogs_exact,
              EXISTS (
                SELECT 1 FROM sku_cogs c WHERE c.platform='ozon' AND lower(c.sku) = lower(o.sku)
              ) AS has_cogs_case
            FROM ord o
            ORDER BY delivered DESC, o.sku
            LIMIT 50;
        """, (start.isoformat(), end.isoformat()))
        rows = cur.fetchall()

        # Сколько всего ключей в sku_cogs и примеры
        cur.execute("SELECT COUNT(*) FROM sku_cogs WHERE platform='ozon';")
        total_cogs = cur.fetchone()[0]
        cur.execute("SELECT sku, cogs_per_unit FROM sku_cogs WHERE platform='ozon' ORDER BY updated_at DESC LIMIT 10;")
        sample_cogs = [{"sku": r[0], "cogs": float(r[1] or 0)} for r in cur.fetchall()]

    items = []
    for sku, delivered, has_ex, has_case in rows:
        items.append({
            "sku": sku, "delivered": delivered,
            "match": "exact" if has_ex else ("lower()" if has_case else "no_cogs")
        })
    return {"ok": True, "period": period, "orders_skus": items, "sku_cogs_total": total_cogs, "sku_cogs_sample": sample_cogs}



# --- Импорт COGS + min_price из CSV с автоопределением разделителя и русских заголовков ---
@app.post("/api/cogs/import_csv")
def import_cogs_csv(path: str = Query("/srv/app/data/thresholds.csv")):
    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )
    # читаем файл и определяем разделитель/диалект
    with open(path, "rb") as fb:
        sample = fb.read(4096)
    try:
        dialect = csv.Sniffer().sniff(sample.decode("utf-8", errors="ignore"))
    except Exception:
        class dialect: delimiter = ';'

    def norm(s):
        return (s or "").strip().lower()

    # маппинг названий колонок
    sku_keys = {"sku","offer_id","артикул","seller_sku","seller sku","offer id"}
    cogs_keys = {"cogs","себестоимость","себестоимость, руб","cogs, rub","cogs_rub"}
    min_keys  = {"min_price","min price","минимальная цена","min_price_rub"}

    imported = 0
    with psycopg2.connect(dsn) as conn, open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter=getattr(dialect, "delimiter", ';'))
        # сопоставим фактические имена полей
        fields_norm = {norm(k): k for k in (reader.fieldnames or [])}
        def pick(keys):
            for k in keys:
                if k in fields_norm: 
                    return fields_norm[k]
            # поиск частичного совпадения
            for fk in fields_norm:
                for k in keys:
                    if k in fk:
                        return fields_norm[fk]
            return None

        col_sku  = pick(sku_keys)
        col_cogs = pick(cogs_keys)
        col_min  = pick(min_keys)

        if not col_sku:
            return {"ok": False, "error": "Не найдена колонка SKU/offer_id/Артикул", "fields": reader.fieldnames}
        if not col_cogs and not col_min:
            return {"ok": False, "error": "Не найдены колонки себестоимости/минимальной цены", "fields": reader.fieldnames}

        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sku_cogs (
                    platform text NOT NULL,
                    sku text NOT NULL,
                    cogs_per_unit numeric(12,2) DEFAULT 0,
                    min_price numeric(12,2) DEFAULT 0,
                    updated_at timestamptz DEFAULT now(),
                    PRIMARY KEY (platform, sku)
                );
            """)
            for row in reader:
                sku = str(row.get(col_sku, "")).strip()
                if not sku:
                    continue
                # парсим числа из строк с запятой/пробелами
                def to_float(v):
                    if v is None: return 0.0
                    s = str(v).replace(" ", "").replace("\u00A0","").replace(",", ".")
                    try: return float(s)
                    except: return 0.0
                cogs = to_float(row.get(col_cogs)) if col_cogs else 0.0
                min_price = to_float(row.get(col_min)) if col_min else 0.0

                cur.execute("""
                    INSERT INTO sku_cogs(platform, sku, cogs_per_unit, min_price)
                    VALUES ('ozon', %s, %s, %s)
                    ON CONFLICT (platform, sku) DO UPDATE
                    SET cogs_per_unit = EXCLUDED.cogs_per_unit,
                        min_price = EXCLUDED.min_price,
                        updated_at = now();
                """, (sku, round(cogs,2), round(min_price,2)))
                imported += 1

    return {"ok": True, "imported": imported}
# --- COGS: пересчёт дневной себестоимости по доставленным штучкам (вчера) ---
@app.post("/api/cogs/recalc_yesterday")
def cogs_recalc_yesterday():
    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )
    day = (date.today() - timedelta(days=1)).isoformat()
    total_cogs = 0.0
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT o.sku, COALESCE(SUM(o.qty_delivered),0) AS delivered
                FROM orders o
                WHERE o.platform='ozon' AND o.date=%s
                GROUP BY o.sku;
            """, (day,))
            by_sku = cur.fetchall()
            for sku, delivered in by_sku:
                if not delivered:
                    continue
                cur.execute("SELECT cogs_per_unit FROM sku_cogs WHERE platform='ozon' AND sku=%s", (sku,))
                row = cur.fetchone()
                cogs = float(row[0]) if row else 0.0
                total_cogs += cogs * float(delivered)

            cur.execute("""
                INSERT INTO costs (platform, sku, date, commission_fee, logistics_fee, storage_fee, cogs_per_unit)
                VALUES ('ozon','OZ-AGG', %s, 0,0,0, %s)
                ON CONFLICT (platform, sku, date) DO UPDATE
                SET cogs_per_unit = EXCLUDED.cogs_per_unit;
            """, (day, round(total_cogs,2)))
    return {"ok": True, "date": day, "cogs_total": round(total_cogs,2)}


# --- OZON: multi-probe finance transaction list (ищем рабочий путь и тело) ---
@app.get("/api/ozon/finance/probe_multi")
def ozon_finance_probe_multi(period: str = Query("yesterday")):
    client_id = os.getenv("OZON_CLIENT_ID"); api_key = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "нет OZON_CLIENT_ID / OZON_API_KEY"}

    today = date.today()
    if period == "yesterday":
        df = dt = (today - timedelta(days=1)).isoformat()
    elif period == "7d":
        df, dt = (today - timedelta(days=6)).isoformat(), today.isoformat()
    else:
        df = dt = (today - timedelta(days=1)).isoformat()

    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    paths = [
        "/v3/finance/transaction/list",
        "/v2/finance/transaction/list",
        "/v1/finance/transaction/list",
        "/v1/finance/transactions",
        "/v1/finance/transactions/list",
    ]
    bodies = [
        {"page": 1, "page_size": 1000, "date": {"from": f"{df} 00:00:00", "to": f"{dt} 23:59:59"}},
        {"page": 1, "page_size": 1000, "filter": {"date": {"from": f"{df} 00:00:00", "to": f"{dt} 23:59:59"}}},
        {"page": 1, "page_size": 1000, "transaction_type": "all",
         "date": {"from": f"{df} 00:00:00", "to": f"{dt} 23:59:59"}},
    ]

    tried = []
    for p in paths:
        for i, body in enumerate(bodies, 1):
            url = f"{OZ_HOST}{p}"
            try:
                r = requests.post(url, json=body, headers=headers, timeout=30)
                try:
                    j = r.json()
                except Exception:
                    j = {}
                info = {
                    "path": p, "body_variant": i, "status": r.status_code, "ok": r.ok,
                    "keys": list(j.keys())[:5],
                    "has_result": bool((j.get("result") or {}).get("operations") or (j.get("result") or {}).get("rows")),
                }
                tried.append(info)
                if info["ok"] and (info["has_result"] or "result" in j or "data" in j):
                    return {"ok": True, "working": info, "tried": tried}
            except Exception as e:
                tried.append({"path": p, "body_variant": i, "error": str(e)})

    return {"ok": False, "working": None, "tried": tried}
# --- OZON: подтянуть себестоимость (COGS) из /v3/product/info/prices и записать в БД ---
@app.post("/api/ozon/finance/save_cogs")
def ozon_finance_save_cogs():
    client_id = os.getenv("OZON_CLIENT_ID")
    api_key   = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "нет OZON_CLIENT_ID / OZON_API_KEY"}

    url = f"{OZ_HOST}/v3/product/info/prices"
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    body = {"limit": 50, "offset": 0}
    try:
        r = requests.post(url, json=body, headers=headers, timeout=60)
        status = r.status_code
        try:
            j = r.json()
        except Exception:
            return {"ok": False, "status": status, "text": r.text[:800]}
        # вернём только ключи, чтобы понять структуру
        return {"ok": True, "status": status, "keys": list(j.keys())[:5], "sample": str(j)[:800]}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# --- COGS: задать себестоимость для SKU (руб/шт) ---
@app.post("/api/cogs/set")
def cogs_set(sku: str = Query(...), cogs: float = Query(...)):
    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            # храним «прайс» в отдельной таблице
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sku_cogs (
                  platform text NOT NULL,
                  sku text NOT NULL,
                  cogs_per_unit numeric(12,2) NOT NULL,
                  updated_at timestamptz DEFAULT now(),
                  PRIMARY KEY (platform, sku)
                );
            """)
            cur.execute("""
                INSERT INTO sku_cogs(platform, sku, cogs_per_unit)
                VALUES ('ozon', %s, %s)
                ON CONFLICT (platform, sku) DO UPDATE
                SET cogs_per_unit = EXCLUDED.cogs_per_unit, updated_at = now();
            """, (sku, round(cogs,2)))
    return {"ok": True, "sku": sku, "cogs_per_unit": round(cogs,2)}


# --- OZON: сохранить реальные возвраты за вчера (units из analytics, ₽ из finance v3) ---
@app.post("/api/ozon/finance/save_returns")
def ozon_finance_save_returns(period: str = Query("yesterday")):
    if period != "yesterday":
        return {"ok": False, "error": "пока поддерживаем только period=yesterday"}

    client_id = os.getenv("OZON_CLIENT_ID"); api_key = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "нет OZON_CLIENT_ID / OZON_API_KEY"}

    d = date.today() - timedelta(days=1)
    day = d.isoformat()
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    # 1) returns_units — из analytics/day
    analytics_url = f"{OZ_HOST}/v1/analytics/data"
    body_a = {
        "date_from": day, "date_to": day,
        "metrics": ["returns"], "dimension": ["day"],
        "limit": 1000, "offset": 0
    }
    r_a = requests.post(analytics_url, json=body_a, headers=headers, timeout=60)
    j_a = r_a.json()
    rows_a = (j_a.get("result") or {}).get("data") or []
    returns_units = 0
    if rows_a:
        m = rows_a[0].get("metrics") or [0]
        try: returns_units = int(m[0] or 0)
        except: returns_units = 0

    # 2) returns_amount (₽) — из finance v3: всё, где в названии есть "возврат",
    #    НО исключаем логистику возврата ("доставка и обработка возврата" и т.п.)
    fin_url = f"{OZ_HOST}/v3/finance/transaction/list"
    def iso_z(dd, t): return f"{dd.isoformat()}T{t}Z"
    body_f = {
        "page": 1, "page_size": 1000,
        "filter": {"date": {"from": iso_z(d, "00:00:00"), "to": iso_z(d, "23:59:59")}, "transaction_type": "all"}
    }
    r_f = requests.post(fin_url, json=body_f, headers=headers, timeout=60)
    j_f = r_f.json()
    ops = (j_f.get("result") or {}).get("operations") or []

    returns_amount = 0.0
    for op in ops:
        name = (op.get("operation_type_name") or op.get("operation_name") or op.get("type") or "").lower()
        try:
            amount = float(op.get("amount") or 0)
        except:
            amount = 0.0
        if amount >= 0:
            continue
        if "возврат" in name:
            # исключаем логистику возврата
            if ("доставка и обработка возврата" in name) or ("обработка возврата" in name):
                continue
            returns_amount += abs(amount)

    # 3) пишем в daily_adjustments
    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO daily_adjustments (date, platform, returns_units, returns_amount)
                VALUES (%s,'ozon',%s,%s)
                ON CONFLICT (platform, date) DO UPDATE
                SET returns_units = EXCLUDED.returns_units,
                    returns_amount = EXCLUDED.returns_amount;
            """, (day, int(returns_units), round(returns_amount, 2)))

    return {"ok": True, "saved_for": day, "returns_units": int(returns_units), "returns_amount": round(returns_amount, 2)}

# --- OZON: finance v3 preview (агрегируем по типам, если ок; иначе покажем ошибку) ---
@app.get("/api/ozon/finance/preview_v3")
def ozon_finance_preview_v3(period: str = Query("yesterday")):
    client_id = os.getenv("OZON_CLIENT_ID"); api_key = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "нет OZON_CLIENT_ID / OZON_API_KEY"}

    today = date.today()
    if period == "yesterday":
        d0 = d1 = (today - timedelta(days=1))
    elif period == "7d":
        d0, d1 = (today - timedelta(days=6)), today
    else:
        d0 = d1 = (today - timedelta(days=1))

    # формат для v3: ISO с Z
    def iso_z(d, hhmmss):
        return f"{d.isoformat()}T{hhmmss}Z"

    body = {
        "page": 1,
        "page_size": 1000,
        "filter": {
            "date": {
                "from": iso_z(d0, "00:00:00"),
                "to":   iso_z(d1, "23:59:59")
            },
            "transaction_type": "all"  # ключевой параметр для v3
        }
    }

    url = f"{OZ_HOST}/v3/finance/transaction/list"
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    try:
        r = requests.post(url, json=body, headers=headers, timeout=60)
        try:
            j = r.json()
        except Exception:
            return {"ok": False, "status": r.status_code, "text": r.text[:800], "sent_body": body}

        if not r.ok:
            # вернём диагностическую ошибку от Ozon (message/details)
            return {"ok": False, "status": r.status_code, **j, "sent_body": body}

        # нормальный ответ v3: result.operations
        ops = (j.get("result") or {}).get("operations") or []
        by_type = {}
        for op in ops:
            name = (op.get("operation_type_name") or op.get("operation_name") or op.get("type") or "").strip()
            amount = float(op.get("amount") or 0)
            by_type[name] = by_type.get(name, 0.0) + amount

        top = sorted(
            [{"type": k or "(empty)", "amount": round(v, 2)} for k, v in by_type.items()],
            key=lambda x: abs(x["amount"]), reverse=True
        )
        return {
            "ok": True,
            "period": period,
            "date_from": d0.isoformat(),
            "date_to": d1.isoformat(),
            "rows": len(ops),
            "top_types": top[:25]
        }
    except Exception as e:
        return {"ok": False, "error": f"request_failed: {e}", "sent_body": body}
# --- Классификация типов операций в категории расходов ---
def _classify_fin_op(name: str) -> str | None:
    n = (name or "").lower()

    # реклама
    if "оплата за клик" in n or "продвижение с оплатой за заказ" in n:
        return "ads"

    # хранение
    if "размещени" in n or "услуга размещения" in n or "хранен" in n:
        return "storage"

    # логистика
    if ("доставка" in n or "возврат" in n or "кросс-док" in n or
        "обработка" in n or "вывоз" in n or "грузомест" in n):
        return "logistics"

    # комиссия, эквайринг, подписки, проценты, вознаграждения
    if ("комисс" in n or "эквайринг" in n or "premium" in n or
        "подписк" in n or "процент" in n or "вознагражд" in n or
        "услуг" in n or "serviceitemcommission" in n or "ozon рассрочка" in n):
        return "commission"

    return None



# --- OZON: сохранить реальные расходы за вчера из finance v3 ---
@app.post("/api/ozon/finance/save_costs_range")
def ozon_finance_save_costs_range(period: str = Query("7d")):
    import os, requests, psycopg2
    from datetime import date, timedelta

    client_id = os.getenv("OZON_CLIENT_ID"); api_key = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "нет OZON_CLIENT_ID / OZON_API_KEY"}

    today = date.today()
    if period == "yesterday":
        start = end = today - timedelta(days=1)
    elif period == "7d":
        end = today; start = end - timedelta(days=6)
    elif period == "14d":
        end = today; start = end - timedelta(days=13)
    elif period == "month":
        end = today; start = today.replace(day=1)
    elif ".." in period:
        s, e = period.split("..", 1); start, end = date.fromisoformat(s), date.fromisoformat(e)
    else:
        start = end = today - timedelta(days=1)

    def iso_z(d, t): return f"{d.isoformat()}T{t}Z"
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}
    url = f"{OZ_HOST}/v3/finance/transaction/list"

    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )

    saved = 0
    cur = start
    while cur <= end:
        body = {
            "page": 1, "page_size": 1000,
            "filter": {"date": {"from": iso_z(cur, "00:00:00"), "to": iso_z(cur, "23:59:59")}, "transaction_type": "all"}
        }
        try:
            r = requests.post(url, json=body, headers=headers, timeout=60)
            if not r.ok:
                try: err = r.json()
                except: err = {"text": r.text[:800]}
                return {"ok": False, "status": r.status_code, **err, "day": cur.isoformat()}
            j = r.json()
        except Exception as e:
            return {"ok": False, "error": f"request_failed: {e}", "day": cur.isoformat()}

        ops = (j.get("result") or {}).get("operations") or []
        sums = {"commission": 0.0, "logistics": 0.0, "storage": 0.0, "ads": 0.0}
        for op in ops:
            name = (op.get("operation_type_name") or op.get("operation_name") or op.get("type") or "").strip().lower()
            try:
                amount = float(op.get("amount") or 0)
            except Exception:
                amount = 0.0
            if amount >= 0:
                continue
            cat = _classify_fin_op(name)
            if cat:
                sums[cat] += abs(amount)

        with psycopg2.connect(dsn) as conn, conn.cursor() as c:
            c.execute("""
                INSERT INTO costs (platform, sku, date, commission_fee, logistics_fee, storage_fee, cogs_per_unit)
                VALUES ('ozon','OZ-AGG', %s, %s, %s, %s, 0)
                ON CONFLICT (platform, sku, date) DO UPDATE
                SET commission_fee = EXCLUDED.commission_fee,
                    logistics_fee  = EXCLUDED.logistics_fee,
                    storage_fee    = EXCLUDED.storage_fee;
            """, (cur.isoformat(), round(sums["commission"],2), round(sums["logistics"],2), round(sums["storage"],2)))
            c.execute("""
                INSERT INTO ads_costs (platform, sku, date, amount)
                VALUES ('ozon','OZ-AGG', %s, %s)
                ON CONFLICT (platform, sku, date) DO UPDATE
                SET amount = EXCLUDED.amount;
            """, (cur.isoformat(), round(sums["ads"],2)))
            conn.commit()

        saved += 1
        cur += timedelta(days=1)

    return {"ok": True, "saved_days": saved, "period": period}




# --- OZON: сохранить реальные расходы за диапазон (7d/14d/month/дата..дата) ---
@app.post("/api/ozon/finance/save_costs_range")
def ozon_finance_save_costs_range(period: str = Query("7d")):
    client_id = os.getenv("OZON_CLIENT_ID"); api_key = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "нет OZON_CLIENT_ID / OZON_API_KEY"}

    today = date.today()
    if period == "7d":
        start, end = today - timedelta(days=6), today
    elif period == "14d":
        start, end = today - timedelta(days=13), today
    elif period == "month":
        start, end = today.replace(day=1), today
    elif ".." in period:
        s, e = period.split("..", 1); start, end = date.fromisoformat(s), date.fromisoformat(e)
    else:
        start = end = today - timedelta(days=1)

    def iso_z(d, t): return f"{d.isoformat()}T{t}Z"
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}
    url = f"{OZ_HOST}/v3/finance/transaction/list"

    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )
    saved = 0
    cur = start
    while cur <= end:
        body = {
            "page": 1, "page_size": 1000,
            "filter": {"date": {"from": iso_z(cur, "00:00:00"), "to": iso_z(cur, "23:59:59")}, "transaction_type": "all"}
        }
        r = requests.post(url, json=body, headers=headers, timeout=60)
        try:
            j = r.json()
        except Exception:
            j = {}
        ops = (j.get("result") or {}).get("operations") or []
        sums = {"commission": 0.0, "logistics": 0.0, "storage": 0.0, "ads": 0.0}
        for op in ops:
            name = (op.get("operation_type_name") or op.get("operation_name") or op.get("type") or "").strip()
            try:
                amount = float(op.get("amount") or 0)
            except Exception:
                amount = 0.0
            if amount >= 0:
                continue
            cat = _classify_fin_op(name)
            if cat:
                sums[cat] += abs(amount)

        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as c:
                c.execute("""
                    INSERT INTO costs (platform, sku, date, commission_fee, logistics_fee, storage_fee, cogs_per_unit)
                    VALUES ('ozon','OZ-AGG', %s, %s, %s, %s, 0)
                    ON CONFLICT (platform, sku, date) DO UPDATE
                    SET commission_fee = EXCLUDED.commission_fee,
                        logistics_fee  = EXCLUDED.logistics_fee,
                        storage_fee    = EXCLUDED.storage_fee;
                """, (cur.isoformat(), round(sums["commission"],2), round(sums["logistics"],2), round(sums["storage"],2)))
                c.execute("""
                    INSERT INTO ads_costs (platform, sku, date, amount)
                    VALUES ('ozon','OZ-AGG', %s, %s)
                    ON CONFLICT (platform, sku, date) DO UPDATE
                    SET amount = EXCLUDED.amount;
                """, (cur.isoformat(), round(sums["ads"],2)))
        saved += 1
        cur += timedelta(days=1)
        return {"ok": True, "saved_days": saved, "period": period}



# Временная заглушка, чтобы / и /yesterday не отдавали 404
@app.get("/", include_in_schema=False)
def root():
    return {"ok": True, "hint": "UI придёт позже, пока проверьте /api/metrics и /health"}

@app.get("/yesterday", include_in_schema=False)
def yesterday_stub():
    return {"detail": "Нет данных (UI-шаблон подключим после ETL)"}

@app.on_event("startup")
async def on_startup():
    # init_db — синхронная функция, вызываем без await
    init_db()

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
# --- Simple daily metrics API (demo) ---
import os

def _period_to_range(p: str):
    today = date.today()
    if p == "yesterday":
        start = today - timedelta(days=1)
        end = today - timedelta(days=1)
    elif p == "7d":
        start = today - timedelta(days=6)
        end = today
    elif p == "14d":
        start = today - timedelta(days=13)
        end = today
    elif p == "month":
        start = (today.replace(day=1))
        end = today
    else:
        # формат YYYY-MM-DD..YYYY-MM-DD
        if ".." in p:
            s, e = p.split("..", 1)
            start = date.fromisoformat(s)
            end = date.fromisoformat(e)
        else:
            # fallback: yesterday
            start = today - timedelta(days=1)
            end = today - timedelta(days=1)
    return start, end

@app.get("/api/metrics")
def metrics(period: str = Query("7d")) -> Dict[str, object]:
    from datetime import date, timedelta
    start, end = _period_to_range(period)

    # как и в daily: не включаем текущий день
    today = date.today()
    end_adj = min(end, today - timedelta(days=1))

    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )

    import psycopg2
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""
            with o as (
              select
                sum(price * qty_delivered) as revenue_delivered,
                sum(qty_ordered)          as orders,
                sum(qty_delivered)        as delivered,
                sum(qty_returned)         as returns
              from orders
              where date between %s and %s
            ),
            c as (
              select
                coalesce(sum(commission_fee),0) as commission,
                coalesce(sum(logistics_fee),0)  as logistics,
                coalesce(sum(storage_fee),0)    as storage,
                coalesce(sum(cogs_per_unit),0)  as cogs
              from costs
              where date between %s and %s
                and sku <> 'OTHER'
            ),
            c_other as (
              select coalesce(sum(cogs_per_unit),0) as other_fees
              from costs
              where date between %s and %s
                and sku = 'OTHER'
            ),
            a as (
              select coalesce(sum(amount),0) as ads
              from ads_costs
              where date between %s and %s
            )
            select
              coalesce(o.revenue_delivered,0),
              coalesce(o.orders,0),
              coalesce(o.delivered,0),
              coalesce(o.returns,0),
              c.commission, c.logistics, c.storage, c.cogs,
              a.ads,
              c_other.other_fees
            from o, c, a, c_other;
        """, (start, end_adj, start, end_adj, start, end_adj, start, end_adj))
        (revenue, orders, delivered, returns,
         commission, logistics, storage, cogs,
         ads, other_fees) = cur.fetchone()

    revenue = float(revenue or 0)
    commission = float(commission or 0)
    logistics = float(logistics or 0)
    storage = float(storage or 0)
    cogs = float(cogs or 0)
    ads = float(ads or 0)
    other_fees = float(other_fees or 0)

    net_profit = revenue - (commission + logistics + storage + cogs + ads + other_fees)
    romi = (revenue / ads) if ads > 0 else None

    summary = {
        "revenue_delivered": revenue,
        "orders": int(orders or 0),
        "delivered": int(delivered or 0),
        "returns": int(returns or 0),
        "commission": commission,
        "logistics": logistics,
        "storage": storage,
        "cogs": cogs,
        "ads": ads,
        "other_fees": other_fees,   # 🆕
        "profit": net_profit,       # ← теперь это чистая прибыль
        "net_profit": net_profit,   # дублируем явно
        "romi": romi,
    }

    return {"ok": True, "period": period, "summary": summary, "by_platform": []}


# --- OZON: тест авторизации и выборки за период ---
from fastapi import Query

OZ_HOST = "https://api-seller.ozon.ru"
# --- ADJ: суммы отмен/возвратов из БД по периоду ---
@app.get("/api/adjustments")
def adjustments(period: str = Query("yesterday")):
    # диапазон дат
    if period == "yesterday":
        start = end = (date.today() - timedelta(days=1)).isoformat()
    elif period == "7d":
        start, end = (date.today() - timedelta(days=6)).isoformat(), date.today().isoformat()
    elif period == "14d":
        start, end = (date.today() - timedelta(days=13)).isoformat(), date.today().isoformat()
    elif period == "month":
        start, end = date.today().replace(day=1).isoformat(), date.today().isoformat()
    else:
        if ".." in period: start, end = period.split("..", 1)
        else: start = end = (date.today() - timedelta(days=1)).isoformat()

    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )
    tot = {"canceled_units": 0, "canceled_amount": 0.0, "returns_units": 0, "returns_amount": 0.0}
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                select
                  coalesce(sum(canceled_units),0),
                  coalesce(sum(canceled_amount),0),
                  coalesce(sum(returns_units),0),
                  coalesce(sum(returns_amount),0)
                from daily_adjustments
                where date between %s and %s and platform='ozon'
            """, (start, end))
            r = cur.fetchone()
            if r:
                tot = {
                    "canceled_units": int(r[0] or 0),
                    "canceled_amount": float(r[1] or 0.0),
                    "returns_units": int(r[2] or 0),
                    "returns_amount": float(r[3] or 0.0),
                }
    return {"ok": True, "period": period, **tot}
# --- OZON: превью финансовых транзакций за период (свод по типам) ---
@app.get("/api/ozon/finance/preview")
def ozon_finance_preview(period: str = Query("yesterday")):
    client_id = os.getenv("OZON_CLIENT_ID")
    api_key   = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "нет OZON_CLIENT_ID / OZON_API_KEY"}

    # период
    today = date.today()
    if period == "yesterday":
        df = dt = (today - timedelta(days=1)).isoformat()
    elif period == "7d":
        df, dt = (today - timedelta(days=6)).isoformat(), today.isoformat()
    elif period == "14d":
        df, dt = (today - timedelta(days=13)).isoformat(), today.isoformat()
    else:
        if ".." in period: df, dt = period.split("..", 1)
        else: df = dt = (today - timedelta(days=1)).isoformat()

    url = f"{OZ_HOST}/v1/finance/transaction/list"
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    # пагинация
    page = 1
    page_size = 1000
    by_type = {}   # operation_type_name -> сумма
    total_rows = 0

    while True:
        body = {
            "page": page,
            "page_size": page_size,
            "date": {"from": f"{df} 00:00:00", "to": f"{dt} 23:59:59"},
        }
        try:
            r = requests.post(url, json=body, headers=headers, timeout=60)
            status = r.status_code
            try:
                j = r.json()
            except Exception:
                # вернём диагностическую инфу, чтобы понять что пришло
                return {"ok": False, "status": status, "text": r.text[:500]}
        except Exception as e:
            return {"ok": False, "error": f"request_failed: {e}"}

        res = j.get("result") or {}
        rows = res.get("operations") or res.get("rows") or []
        if not rows:
            # если пусто, отдадим что насобирали (или сообщим что пусто)
            break

        for op in rows:
            name = (op.get("operation_type_name") or op.get("operation_name") or op.get("type") or "").strip()
            amount = float(op.get("amount") or op.get("accruals_for_sale") or 0)
            by_type[name] = by_type.get(name, 0.0) + amount
            total_rows += 1

        has_next = res.get("has_next")
        if has_next is None:
            has_next = len(rows) == page_size
        if not has_next:
            break
        page += 1


    # отсортируем по убыванию суммы
    top = sorted(
        [{"type": k or "(empty)", "amount": round(v, 2)} for k, v in by_type.items()],
        key=lambda x: abs(x["amount"]),
        reverse=True
    )

    return {
        "ok": True,
        "period": period,
        "date_from": df,
        "date_to": dt,
        "rows": total_rows,
        "types_count": len(top),
        "top_types": top[:25]  # первые 25 типов — чтобы было видно картину
    }


@app.get("/api/ozon/test")
# --- OZON: сводка доставлено / отмены / возвраты за период ---
@app.get("/api/ozon/cancel_return_summary")
# --- OZON: точные отмены по FBO за период (сумма и штуки) ---
@app.get("/api/ozon/cancellations_fbo_exact")
def ozon_cancellations_fbo_exact(period: str = Query("yesterday")):
    client_id = os.getenv("OZON_CLIENT_ID")
    api_key   = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "Нет OZON_CLIENT_ID / OZON_API_KEY в .env"}

    # период
    if period == "yesterday":
        df = dt = (date.today() - timedelta(days=1)).isoformat()
    elif period == "7d":
        df, dt = (date.today() - timedelta(days=6)).isoformat(), date.today().isoformat()
    elif period == "14d":
        df, dt = (date.today() - timedelta(days=13)).isoformat(), date.today().isoformat()
    elif period == "month":
        df, dt = date.today().replace(day=1).isoformat(), date.today().isoformat()
    else:
        if ".." in period:
            df, dt = period.split("..", 1)
        else:
            df = dt = (date.today() - timedelta(days=1)).isoformat()

    # берём FBO постинги за период и фильтруем отменённые
    url = f"{OZ_HOST}/v2/posting/fbo/list"
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    canceled_units = 0
    canceled_amount = 0.0
    limit = 1000
    offset = 0

    while True:
        body = {
            "limit": limit,
            "offset": offset,
            "filter": {
                "since": f"{df}T00:00:00.000Z",
                "to":    f"{dt}T23:59:59.999Z"
            },
            # многие продавцы отдают все, поэтому фильтруем по статусу в коде ниже
            "with": { "analytics_data": False, "financial_data": False }
        }
        r = requests.post(url, json=body, headers=headers, timeout=60)
        j = r.json()
        items = j.get("result", [])
        if not items:
            break

        for p in items:
            status = (p.get("status") or p.get("state") or "").lower()
            if "cancel" in status:  # cancelled / canceled
                for it in p.get("products", []):
                    qty = int(it.get("quantity", 0) or 0)
                    # цена может лежать в разных полях: price или offer_price
                    price_raw = it.get("price") or it.get("offer_price") or 0
                    try:
                        price = float(price_raw)
                    except Exception:
                        price = 0.0
                    canceled_units += qty
                    canceled_amount += price * qty

        if len(items) < limit:
            break
        offset += limit

    return {
        "ok": True,
        "period": period,
        "canceled_units_exact": canceled_units,
        "canceled_amount_exact": round(canceled_amount, 2)
    }
# --- OZON PREMIUM: multi-probe Realization by Day ---
@app.get("/api/ozon/premium_probe_multi")
def ozon_premium_probe_multi(period: str = Query("yesterday")):
    client_id = os.getenv("OZON_CLIENT_ID")
    api_key   = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "нет OZON_CLIENT_ID / OZON_API_KEY"}

    from datetime import date, timedelta
    today = date.today()
    if period == "yesterday":
        date_from = date_to = (today - timedelta(days=1)).isoformat()
    elif period == "7d":
        date_from, date_to = (today - timedelta(days=6)).isoformat(), today.isoformat()
    else:
        date_from = date_to = (today - timedelta(days=1)).isoformat()

    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    # самые частые варианты путей у этого отчёта в разных версиях/аккаунтах
    candidates = [
        "/v1/finance/realization/by_day",
        "/v1/finance/realization/by-day",
        "/v1/finance/realization/by_day_report",
        "/v2/finance/realization/by_day",
        "/v1/finance/realization-by-day",
        "/v1/finance/realization",  # иногда без суффикса, с типом в body
    ]

    # попробуем 2 варианта body
    bodies = [
        {"date_from": date_from, "date_to": date_to, "language": "RU", "page_size": 50, "page": 1},
        {"date": {"from": date_from, "to": date_to}, "language": "RU", "page_size": 50, "page": 1},
    ]

    results = []
    for path in candidates:
        for idx, body in enumerate(bodies, start=1):
            url = f"{OZ_HOST}{path}"
            try:
                r = requests.post(url, json=body, headers=headers, timeout=30)
                try:
                    j = r.json()
                except Exception:
                    j = {}
                results.append({
                    "path": path,
                    "body_variant": idx,
                    "status": r.status_code,
                    "ok": r.ok,
                    "has_result": "result" in j,
                    "top_keys": list(j.keys())[:5],
                })
                # если нашли рабочий — дальше можно остановиться
                if r.ok and ("result" in j or "data" in j):
                    return {"ok": True, "working": results[-1], "tried": results}
            except Exception as e:
                results.append({"path": path, "body_variant": idx, "error": str(e)})

    return {"ok": False, "working": None, "tried": results}

# --- OZON: точные отмены по FBS за период ---
@app.get("/api/ozon/cancellations_fbs_exact")
def ozon_cancellations_fbs_exact(period: str = Query("yesterday")):
    client_id = os.getenv("OZON_CLIENT_ID"); api_key = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "Нет OZON_CLIENT_ID / OZON_API_KEY в .env"}

    # период
    if period == "yesterday":
        df = dt = (date.today() - timedelta(days=1)).isoformat()
    elif period == "7d":
        df, dt = (date.today() - timedelta(days=6)).isoformat(), date.today().isoformat()
    elif period == "14d":
        df, dt = (date.today() - timedelta(days=13)).isoformat(), date.today().isoformat()
    elif period == "month":
        df, dt = date.today().replace(day=1).isoformat(), date.today().isoformat()
    else:
        if ".." in period: df, dt = period.split("..", 1)
        else: df = dt = (date.today() - timedelta(days=1)).isoformat()

    url = f"{OZ_HOST}/v3/posting/fbs/list"
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    canceled_units = 0; canceled_amount = 0.0
    limit = 1000; offset = 0
    while True:
        body = {
            "limit": limit,
            "offset": offset,
            "filter": {
                "date_from": f"{df}T00:00:00.000Z",
                "date_to":   f"{dt}T23:59:59.999Z",
                # статусы в ответе разные; фильтруем в коде
            },
            "with": {"analytics_data": False, "financial_data": False}
        }
        r = requests.post(url, json=body, headers=headers, timeout=60)
        j = r.json()
        items = j.get("result", {}).get("postings") or j.get("result") or []
        if not items: break

        for p in items:
            status = (p.get("status") or p.get("state") or "").lower()
            if "cancel" in status:  # cancelled / canceled
                for it in p.get("products", []):
                    qty = int(it.get("quantity", 0) or 0)
                    price_raw = it.get("price") or it.get("offer_price") or 0
                    try: price = float(price_raw)
                    except: price = 0.0
                    canceled_units += qty
                    canceled_amount += price * qty

        if len(items) < limit: break
        offset += limit

    return {"ok": True, "period": period,
            "canceled_units_exact": canceled_units,
            "canceled_amount_exact": round(canceled_amount, 2)}

# --- OZON: суммарные точные отмены (FBO+FBS) ---
@app.get("/api/ozon/cancellations_exact")
def ozon_cancellations_exact(period: str = Query("yesterday")):
    fbo = ozon_cancellations_fbo_exact(period)
    fbs = ozon_cancellations_fbs_exact(period)
    if not fbo.get("ok") and not fbs.get("ok"):
        return {"ok": False, "error": "no data"}
    return {
        "ok": True,
        "period": period,
        "fbo": fbo,
        "fbs": fbs,
        "total_units": int((fbo.get("canceled_units_exact") or 0) + (fbs.get("canceled_units_exact") or 0)),
        "total_amount": round((fbo.get("canceled_amount_exact") or 0.0) + (fbs.get("canceled_amount_exact") or 0.0), 2)
    }
# --- OZON: сохранить точные отмены за 'yesterday' в БД ---
@app.post("/api/ozon/save_cancellations")
def ozon_save_cancellations(period: str = Query("yesterday")):
    if period != "yesterday":
        return {"ok": False, "error": "Пока поддерживаем только period=yesterday для записи"}
    res = ozon_cancellations_exact(period)
    if not res.get("ok"):
        return {"ok": False, "error": "нет данных от Ozon"}
    total_units = int(res.get("total_units") or 0)
    total_amount = float(res.get("total_amount") or 0.0)
    dt = (date.today() - timedelta(days=1)).isoformat()

    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO daily_adjustments (date, platform, canceled_units, canceled_amount)
                VALUES (%s,'ozon',%s,%s)
                ON CONFLICT (platform, date) DO UPDATE
                SET canceled_units = EXCLUDED.canceled_units,
                    canceled_amount = EXCLUDED.canceled_amount;
            """, (dt, total_units, total_amount))
    return {"ok": True, "saved_for": dt, "canceled_units": total_units, "canceled_amount": round(total_amount,2)}
# --- OZON: сохранить отмены по каждому дню диапазона (yesterday/7d/14d/month) ---
@app.post("/api/ozon/save_cancellations_range")
def ozon_save_cancellations_range(period: str = Query("7d")):
    # диапазон дат
    if period == "yesterday":
        start = end = date.today() - timedelta(days=1)
    elif period == "7d":
        end = date.today()
        start = end - timedelta(days=6)
    elif period == "14d":
        end = date.today()
        start = end - timedelta(days=13)
    elif period == "month":
        end = date.today()
        start = date.today().replace(day=1)
    else:
        if ".." in period:
            s, e = period.split("..", 1)
            start, end = date.fromisoformat(s), date.fromisoformat(e)
        else:
            start = end = date.today() - timedelta(days=1)

    dsn = (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )
    saved = 0
    cur_dt = start
    while cur_dt <= end:
        day = cur_dt.isoformat()
        # считаем точные отмены за конкретный день
        fbo = ozon_cancellations_fbo_exact(f"{day}..{day}")
        fbs = ozon_cancellations_fbs_exact(f"{day}..{day}")
        units = int((fbo.get("canceled_units_exact") or 0) + (fbs.get("canceled_units_exact") or 0))
        amount = float((fbo.get("canceled_amount_exact") or 0.0) + (fbs.get("canceled_amount_exact") or 0.0))
        with psycopg2.connect(dsn) as conn:
            with conn.cursor() as c:
                c.execute("""
                    INSERT INTO daily_adjustments (date, platform, canceled_units, canceled_amount)
                    VALUES (%s,'ozon',%s,%s)
                    ON CONFLICT (platform, date) DO UPDATE
                    SET canceled_units = EXCLUDED.canceled_units,
                        canceled_amount = EXCLUDED.canceled_amount;
                """, (day, units, amount))
        saved += 1
        cur_dt += timedelta(days=1)
    return {"ok": True, "saved_days": saved, "period": period}

def ozon_cancel_return_summary(period: str = Query("yesterday")):
    client_id = os.getenv("OZON_CLIENT_ID")
    api_key   = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "Нет OZON_CLIENT_ID / OZON_API_KEY в .env"}

    # Период
    if period == "yesterday":
        df, dt = (date.today() - timedelta(days=1)).isoformat(), (date.today() - timedelta(days=1)).isoformat()
    elif period == "7d":
        df, dt = (date.today() - timedelta(days=6)).isoformat(), date.today().isoformat()
    elif period == "14d":
        df, dt = (date.today() - timedelta(days=13)).isoformat(), date.today().isoformat()
    elif period == "month":
        df, dt = date.today().replace(day=1).isoformat(), date.today().isoformat()
    else:
        if ".." in period:
            s, e = period.split("..", 1)
            df, dt = s, e
        else:
            df = dt = (date.today() - timedelta(days=1)).isoformat()

    # Тянем из analytics/day: revenue (доставленная выручка), ordered_units, delivered_units, returns
    url = f"{OZ_HOST}/v1/analytics/data"
    body = {
        "date_from": df,
        "date_to": dt,
        "metrics": ["revenue", "ordered_units", "delivered_units", "returns"],
        "dimension": ["day"],
        "limit": 1000,
        "offset": 0
    }
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    r = requests.post(url, json=body, headers=headers, timeout=60)
    j = r.json()
    rows = j.get("result", {}).get("data", [])

    # Агрегируем по периоду
    delivered_units = 0
    ordered_units   = 0
    returns_units   = 0
    delivered_amount = 0.0

    for row in rows:
        # metrics: [revenue, ordered_units, delivered_units, returns]
        m = row.get("metrics", [0,0,0,0])
        delivered_amount += float(m[0] or 0)
        ordered_units    += int(m[1] or 0)
        delivered_units  += int(m[2] or 0)
        returns_units    += int(m[3] or 0)

    canceled_units = max(ordered_units - delivered_units, 0)
    avg_price = (delivered_amount / delivered_units) if delivered_units else 0.0
    canceled_amount_est = round(avg_price * canceled_units, 2)
    returns_amount_est  = round(avg_price * returns_units, 2)

    return {
        "ok": True,
        "period": period,
        "delivered_amount": round(delivered_amount, 2),
        "delivered_units": delivered_units,
        "ordered_units": ordered_units,
        "canceled_units": canceled_units,
        "returns_units": returns_units,
        "avg_price_delivered": round(avg_price, 2),
        "canceled_amount_est": canceled_amount_est,
        "returns_amount_est": returns_amount_est
    }

# --- OZON: импорт "вчера" (или 7d/14d/month) в таблицу orders (+нулевые costs) ---


def _db_dsn():
    return (
        os.getenv("DATABASE_URL")
        or f"postgresql://{os.getenv('DB_USER','postgres')}:{os.getenv('DB_PASSWORD', os.getenv('POSTGRES_PASSWORD','postgres'))}@{os.getenv('DB_HOST','db')}:{os.getenv('DB_PORT','5432')}/{os.getenv('DB_NAME','mpdaily')}"
    )

@app.post("/api/ozon/import")
def ozon_import(period: str = Query("yesterday")):
    client_id = os.getenv("OZON_CLIENT_ID")
    api_key   = os.getenv("OZON_API_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "Нет OZON_CLIENT_ID / OZON_API_KEY в .env"}

    # Период
    from datetime import date, timedelta
    today = date.today()
    if period == "yesterday":
        date_from = date_to = (today - timedelta(days=1)).isoformat()
    elif period == "7d":
        date_from, date_to = (today - timedelta(days=6)).isoformat(), today.isoformat()
    elif period == "14d":
        date_from, date_to = (today - timedelta(days=13)).isoformat(), today.isoformat()
    elif period == "month":
        date_from, date_to = today.replace(day=1).isoformat(), today.isoformat()
    else:
        # формат YYYY-MM-DD..YYYY-MM-DD
        if ".." in period:
            s, e = period.split("..", 1)
            date_from, date_to = s, e
        else:
            date_from = date_to = (today - timedelta(days=1)).isoformat()

    # Запрос к Ozon Analytics API (агрегируем по дням)
    url = f"{OZ_HOST}/v1/analytics/data"
    body = {
        "date_from": date_from,
        "date_to": date_to,
        "metrics": ["revenue", "ordered_units", "delivered_units", "returns"],
        "dimension": ["day"],
        "limit": 1000,
        "offset": 0
    }
    headers = {"Client-Id": client_id, "Api-Key": api_key, "Content-Type": "application/json"}

    r = requests.post(url, json=body, headers=headers, timeout=60)
    j = r.json()
    data = j.get("result", {}).get("data", [])

    if not data:
        return {"ok": True, "imported": 0, "note": "Ozon вернул пусто"}

    # Преобразуем и пишем в БД (upsert)
    dsn = _db_dsn()
    inserted = 0
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            for row in data:
                # row пример: {"dimensions":[{"id":"2025-10-22","name":"2025-10-22"}],"metrics":[revenue, ordered, delivered, returns]}
                dt = row["dimensions"][0]["id"]
                revenue, ordered, delivered, returned = row["metrics"]
                delivered = int(delivered or 0)
                ordered   = int(ordered or 0)
                returned  = int(returned or 0)
                revenue   = float(revenue or 0.0)

                # Цена = revenue / delivered (если есть доставка), иначе 0
                price = (revenue / delivered) if delivered else 0.0
                if not isfinite(price):
                    price = 0.0

                # orders: агрегированная запись за день, sku-заглушка
                oid = f"oz-agg-{dt}"
                cur.execute(
                    """
                    INSERT INTO orders (id, platform, date, sku, price, qty_ordered, qty_delivered, qty_returned)
                    VALUES (%s,'ozon',%s,'OZ-AGG',%s,%s,%s,%s)
                    ON CONFLICT (id) DO UPDATE
                    SET date=EXCLUDED.date, price=EXCLUDED.price, qty_ordered=EXCLUDED.qty_ordered,
                        qty_delivered=EXCLUDED.qty_delivered, qty_returned=EXCLUDED.qty_returned;
                    """,
                    (oid, dt, price, ordered, delivered, returned)
                )

                # costs: пока нули (комиссия/логистика/хранение/себестоимость/реклама подключим следующим шагом)
                cur.execute(
                    """
                    INSERT INTO costs (platform, sku, date, commission_fee, logistics_fee, storage_fee, cogs_per_unit)
                    VALUES ('ozon','OZ-AGG',%s,0,0,0,0)
                    ON CONFLICT DO NOTHING;
                    """,
                    (dt,)
                )
                inserted += 1
    return {"ok": True, "imported": inserted, "period": period}

def ozon_test(period: str = Query("yesterday")):
    client_id = os.getenv("OZON_CLIENT_ID") or os.getenv("OZON_CLIENTID") or os.getenv("OZON_CLIENT")
    api_key   = os.getenv("OZON_API_KEY") or os.getenv("OZON_APIKEY") or os.getenv("OZON_KEY")
    if not client_id or not api_key:
        return {"ok": False, "error": "Нет OZON_CLIENT_ID / OZON_API_KEY в .env"}

    # очень простой период
    from datetime import date, timedelta
    today = date.today()
    if period == "yesterday":
        date_from = date_to = (today - timedelta(days=1)).isoformat()
    elif period == "7d":
        date_from, date_to = (today - timedelta(days=6)).isoformat(), today.isoformat()
    else:
        date_from = date_to = (today - timedelta(days=1)).isoformat()

    url = f"{OZ_HOST}/v1/analytics/data"
    body = {
        "date_from": date_from,
        "date_to": date_to,
        "metrics": ["revenue", "ordered_units", "delivered_units", "returns"],
        "dimension": ["day"],
        "limit": 1000,
        "offset": 0
    }
    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json"
    }

    try:
        r = requests.post(url, json=body, headers=headers, timeout=30)
        j = r.json()
        return {"ok": r.ok, "status": r.status_code, "count": len(j.get("result", {}).get("data", [])), "raw_hint": list(j)[:1]}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    
# --- DASHBOARD (минимальная версия) ---
from fastapi.responses import HTMLResponse

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
<!doctype html>
<html lang="ru">
<meta charset="utf-8">
<title>MP Daily — Дашборд</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
  body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial,sans-serif;margin:24px;background:#0f1115;color:#e6e6e6}
  .wrap{max-width:1100px;margin:0 auto}
  .row{display:flex;gap:12px;flex-wrap:wrap;margin:12px 0}
  .btn{padding:10px 14px;border:1px solid #2a2f3a;border-radius:10px;background:#151922;cursor:pointer}
  .btn.active{background:#1e2633}
  .cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px}
  .card{background:#141821;border:1px solid #2a2f3a;border-radius:14px;padding:14px}
  .k{font-size:13px;color:#9aa4b2}
  .v{font-size:20px;font-weight:700;margin-top:6px}
  canvas{background:#0f1115;border:1px solid #2a2f3a;border-radius:12px;padding:8px}
  a{color:#7cc1ff}
</style>
<div class="wrap">
  <h2>Дашборд</h2>
  <div class="row" id="periods">
    <button class="btn" data-p="yesterday">Вчера</button>
    <button class="btn active" data-p="7d">7 дней</button>
    <button class="btn" data-p="14d">14 дней</button>
    <button class="btn" data-p="month">Месяц</button>
  </div>

  <div class="cards" id="cards"></div>
  <h3 style="margin-top:18px">Динамика выручки и прибыли по дням</h3>
  <canvas id="trendChart" height="120"></canvas>
  <h3 style="margin-top:18px">Структура выручки/затрат</h3>
  <canvas id="costChart" height="120"></canvas>

  <p style="margin-top:12px;font-size:13px;color:#9aa4b2">
    Источник данных: <code>/api/metrics?period=...</code>. Для детализации по дням добавим позже.
  </p>
</div>

<script>
const fmt = n => n.toLocaleString('ru-RU', {style:'currency', currency:'RUB'});
const btns = [...document.querySelectorAll('#periods .btn')];
let chart;
window.trendChart = null;

async function load(p='7d'){
  btns.forEach(b=>b.classList.toggle('active', b.dataset.p===p));
  const r = await fetch(`/api/metrics?period=${p}`);
  const data = await r.json();

  const s = data.summary || {};
  // --- корректировки: факт из БД + резервная оценка ---
let adj = null;
try {
  const rAdj = await fetch(`/api/adjustments?period=${p}`);
  adj = await rAdj.json();
} catch(e){ adj = null; }

const deliveredUnits = s.delivered || 0;
const orders        = s.orders || 0;
const avgPrice      = deliveredUnits ? (s.revenue_delivered || 0) / deliveredUnits : 0;

// резервная оценка
const canceledUnitsEst  = Math.max(orders - deliveredUnits, 0);
const returnsUnitsEst   = s.returns || 0;
const canceledAmountEst = Math.round(avgPrice * canceledUnitsEst);
const returnsAmountEst  = Math.round(avgPrice * returnsUnitsEst);

// если в БД есть факт — используем его, иначе оценку
const canceledUnits  = (adj && adj.ok && typeof adj.canceled_units  === 'number') ? adj.canceled_units  : canceledUnitsEst;
const canceledAmount = (adj && adj.ok && typeof adj.canceled_amount === 'number') ? adj.canceled_amount : canceledAmountEst;
const returnsUnits   = (adj && adj.ok && typeof adj.returns_units   === 'number') ? adj.returns_units   : returnsUnitsEst;
const returnsAmount  = (adj && adj.ok && typeof adj.returns_amount  === 'number') ? adj.returns_amount  : returnsAmountEst;


    const cards = [
    ['Выручка', s.revenue_delivered||0],
    ['Прибыль', s.profit||0],
    ['ROMI', (s.romi??0).toFixed(2)],
    ['Заказы', s.orders||0],
    ['Доставлено', deliveredUnits],
    ['Отмены',   `${canceledUnits} шт · ${fmt(canceledAmount)} ${adj&&adj.ok?'(факт)':'(оценка)'}`],
    ['Возвраты', `${returnsUnits} шт · ${fmt(returnsAmount)} ${adj&&adj.ok?'(факт)':'(оценка)'}`],
    ['Комиссия', s.commission||0],
    ['Логистика', s.logistics||0],
    ['Хранение', s.storage||0],
    ['Себестоимость', s.cogs||0],
    ['Реклама', s.ads||0],
  ];


  document.querySelector('#cards').innerHTML = cards.map(([k,v])=>`
    <div class="card"><div class="k">${k}</div><div class="v">${typeof v==='number'?fmt(v):v}</div></div>
  `).join('');
 
   // Динамика по дням — теперь берём из отдельного эндпоинта
  let byDay = [];
  try {
    const r2 = await fetch(`/api/metrics_daily?period=${p}`);
    const d2 = await r2.json();
    byDay = d2.by_day || [];
  } catch (e) { byDay = []; }

  if (!byDay.length) {
    byDay = [{
      date: (new Date()).toISOString().slice(0,10),
      revenue_delivered: s.revenue_delivered || 0,
      profit: s.profit || 0
    }];
  }

  const labels2 = byDay.map(x => (x.date || '').slice(0,10));
  const revenue = byDay.map(x => x.revenue_delivered || 0);
  const profit  = byDay.map(x => x.profit || 0);

  const ctx2 = document.getElementById('trendChart').getContext('2d');
  if (window.trendChart && typeof window.trendChart.destroy === 'function') {
  window.trendChart.destroy();
}
  window.trendChart = new Chart(ctx2, {
    type: 'line',
    data: {
      labels: labels2,
      datasets: [
        { label: 'Выручка', data: revenue, borderWidth: 2, tension: 0.3 },
        { label: 'Прибыль', data: profit,  borderWidth: 2, tension: 0.3 }
      ]
    },
    options: {
      responsive: true,
      plugins: { legend: { position: 'bottom' } },
      scales: { y: { beginAtZero: true } }
    }
  });

  // Диаграмма затрат/прибыли
  const labels = ['Комиссия','Логистика','Хранение','Себестоимость','Реклама','Прибыль'];
  const values = [s.commission||0, s.logistics||0, s.storage||0, s.cogs||0, s.ads||0, s.profit||0];

  const ctx = document.getElementById('costChart').getContext('2d');
  if (chart) chart.destroy();
  chart = new Chart(ctx, {
    type: 'bar',
    data: { labels, datasets: [{ label: '₽', data: values }] },
    options: {
      responsive: true,
      scales: { y: { beginAtZero: true } },
      plugins: { legend: { display: false } }
    }
  });
}

btns.forEach(b=>b.addEventListener('click',()=>load(b.dataset.p)));
load(); // 7d по умолчанию
</script>
</html>
    """
