from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, Iterable, List


def _to_float(value) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def transform_ozon_orders(raw_orders: Iterable[Dict]) -> List[Dict]:
    records: List[Dict] = []
    for posting in raw_orders:
        posting_id = posting.get("posting_number") or posting.get("order_id")
        analytics = posting.get("analytics_data", {}) or {}
        financial = posting.get("financial_data", {}) or {}
        delivered_date = analytics.get("shipment_date") or posting.get("created_at")
        if delivered_date:
            delivered_date = datetime.fromisoformat(delivered_date[:19]).date()
        products = posting.get("products", [])
        for product in products:
            sku = str(product.get("sku") or product.get("offer_id"))
            qty = int(product.get("quantity", 0))
            records.append(
                {
                    "id": f"ozon-{posting_id}-{sku}",
                    "platform": "ozon",
                    "date": delivered_date or datetime.fromisoformat(posting["created_at"][:19]).date(),
                    "sku": sku,
                    "price": _to_float(product.get("price")),
                    "qty_ordered": qty,
                    "qty_delivered": int(product.get("quantity", 0)),
                    "qty_returned": int(product.get("quantity_returned", 0)),
                    "financial": financial.get("products", []),
                }
            )
    return records


def transform_wb_sales(raw_sales: Iterable[Dict]) -> List[Dict]:
    records: List[Dict] = []
    for sale in raw_sales:
        doc_id = sale.get("saleID") or sale.get("srid")
        sale_date = datetime.fromisoformat(sale["date"][:19]).date()
        sku = str(sale.get("supplierArticle") or sale.get("article"))
        price = _to_float(sale.get("forPay") or sale.get("priceWithDisc"))
        qty = int(sale.get("quantity", 1))
        records.append(
            {
                "id": f"wb-{doc_id}-{sku}",
                "platform": "wb",
                "date": sale_date,
                "sku": sku,
                "price": price / max(qty, 1),
                "qty_ordered": qty,
                "qty_delivered": qty if sale.get("isCancel") in (False, 0) else 0,
                "qty_returned": qty if sale.get("isCancel") in (True, 1) else 0,
            }
        )
    return records


def build_costs_from_finance(finance_records: Iterable[Dict]) -> List[Dict]:
    grouped: Dict[tuple, Dict[str, float]] = defaultdict(lambda: defaultdict(float))  # type: ignore
    for record in finance_records:
        posting = record.get("posting")
        sku = str(record.get("sku") or record.get("offer_id") or record.get("product_id") or "")
        if not sku:
            continue
        operation_date = datetime.fromisoformat(record["operation_date"][:19]).date()
        key = (record.get("platform", "ozon"), sku, operation_date)
        amount = _to_float(record.get("amount"))
        operation_type = record.get("operation_type") or record.get("type")
        bucket = grouped[key]
        if operation_type in {"MarketplaceServiceItemDelivery", "Delivery"}:
            bucket["logistics_fee"] += amount
        elif operation_type in {"MarketplaceServiceItemReturnFlow", "Return"}:
            bucket["logistics_fee"] += amount
        elif operation_type in {"MarketplaceServiceItemStorageFee", "Storage"}:
            bucket["storage_fee"] += amount
        elif operation_type in {"MarketplaceServiceItemCommission", "Commission"}:
            bucket["commission_fee"] += amount
        elif operation_type in {"MarketplaceServiceItemDirectFlowLogistics"}:
            bucket["logistics_fee"] += amount
        elif operation_type in {"MarketplaceServiceItemFulfillment"}:
            bucket["cogs_per_unit"] += amount
        else:
            bucket["other"] += amount
    records: List[Dict] = []
    for (platform, sku, operation_date), values in grouped.items():
        qty = values.pop("qty", 1)
        records.append(
            {
                "platform": platform or "ozon",
                "sku": sku,
                "date": operation_date,
                "commission_fee": values.get("commission_fee", 0.0),
                "logistics_fee": values.get("logistics_fee", 0.0),
                "storage_fee": values.get("storage_fee", 0.0),
                "cogs_per_unit": values.get("cogs_per_unit", 0.0),
            }
        )
    return records


def transform_ads(records: Iterable[Dict], platform: str) -> List[Dict]:
    result: List[Dict] = []
    for row in records:
        if platform == "ozon":
            day = row.get("date") or row.get("day")
            sku = row.get("sku") or row.get("sku_id")
            amount = row.get("spend") or row.get("expense") or 0
        else:
            day = row.get("date")
            sku = row.get("nmId") or row.get("subject")
            amount = row.get("sum") or row.get("advertCost") or 0
        if not day:
            continue
        parsed_date = datetime.fromisoformat(str(day)) if "T" in str(day) else datetime.strptime(str(day), "%Y-%m-%d")
        result.append(
            {
                "platform": platform,
                "sku": str(sku) if sku else None,
                "date": parsed_date.date(),
                "amount": _to_float(amount),
            }
        )
    return result
