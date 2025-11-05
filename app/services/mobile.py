from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.config import get_settings
from app.repos.orders_repo import OrdersRepository
from app.schemas_mobile import (
    MobileBreakevenResponse,
    MobileProduct,
    MobileStockForecastItem,
    MobileStockForecastResponse,
    MobileSummary,
)

PERIOD_TO_DAYS = {"1d": 1, "7d": 7, "30d": 30}
settings = get_settings()
tz = ZoneInfo(settings.tz)


@lru_cache(maxsize=1)
def _load_catalog() -> Dict[str, Dict[str, str]]:
    path = Path(__file__).resolve().parents[1] / "data" / "thresholds.csv"
    if not path.exists():
        return {}
    catalog: Dict[str, Dict[str, str]] = {}
    with path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            sku = (row.get("sku") or "").strip()
            if not sku:
                continue
            catalog[sku] = {
                "name": (row.get("name") or "").strip(),
                "min_price": row.get("min_price") or "",
                "cogs": row.get("cogs") or "",
            }
    return catalog


@dataclass
class MobileProductsResult:
    items: List[MobileProduct]
    next_cursor: Optional[int]


class MobileService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.orders_repo = OrdersRepository(session)

    def _resolve_period(self, period: str) -> Tuple[date, date]:
        normalized = (period or "7d").lower()
        if normalized not in PERIOD_TO_DAYS:
            raise ValueError("Unsupported period. Use one of 1d, 7d, 30d.")
        days = PERIOD_TO_DAYS[normalized]
        end = datetime.now(tz).date() - timedelta(days=1)
        start = end - timedelta(days=days - 1)
        return start, end

    def _aggregate_totals(self, metrics: Dict[str, Dict[str, float]]) -> Dict[str, float]:
        totals = {
            "revenue_delivered": 0.0,
            "profit": 0.0,
            "orders_count": 0,
            "ads": 0.0,
            "commission": 0.0,
            "logistics": 0.0,
            "storage": 0.0,
            "cogs": 0.0,
        }
        for data in metrics.values():
            totals["revenue_delivered"] += float(data.get("revenue_delivered") or 0)
            totals["profit"] += float(data.get("profit") or 0)
            totals["orders_count"] += int(data.get("orders_count") or 0)
            totals["ads"] += float(data.get("ads") or 0)
            totals["commission"] += float(data.get("commission") or 0)
            totals["logistics"] += float(data.get("logistics") or 0)
            totals["storage"] += float(data.get("storage") or 0)
            totals["cogs"] += float(data.get("cogs") or 0)
        return totals

    def get_summary(self, period: str, tenant_id: Optional[str] = None) -> MobileSummary:
        start, end = self._resolve_period(period)
        metrics = self.orders_repo.get_metrics(start, end)
        # TODO: filter metrics by tenant_id once schema supports it.
        if not metrics:
            return MobileSummary()

        totals = self._aggregate_totals(metrics)
        revenue = totals["revenue_delivered"]
        profit = totals["profit"]
        orders = totals["orders_count"]
        roi = profit / revenue if revenue else None
        return MobileSummary(revenue=revenue, profit=profit, orders=orders, roi=roi)

    def get_products(
        self,
        limit: int,
        cursor: int,
        sort: str,
        tenant_id: Optional[str] = None,
    ) -> MobileProductsResult:
        start, end = self._resolve_period("7d")
        fetch_limit = limit + cursor + 1
        raw_items = self.orders_repo.get_top_sku(start, end, limit=fetch_limit)
        catalog = _load_catalog()
        products: List[MobileProduct] = []

        for entry in raw_items:
            revenue = float(entry.get("revenue") or 0)
            profit = float(entry.get("profit") or 0)
            delivered = int(entry.get("qty_delivered") or 0)
            margin = profit / revenue if revenue else 0.0
            avg_price = revenue / delivered if delivered else revenue
            sku = entry.get("sku") or ""
            meta = catalog.get(sku, {})
            name = meta.get("name") or sku
            # TODO: fetch real stock levels from inventory service once available.
            stock = 0
            products.append(
                MobileProduct(
                    sku=sku,
                    name=name,
                    margin=margin,
                    price=avg_price,
                    orders=delivered,
                    stock=stock,
                )
            )

        if sort == "margin":
            products.sort(key=lambda item: item.margin, reverse=True)

        window = products[cursor : cursor + limit]
        has_more = len(products) > cursor + limit
        next_cursor = cursor + len(window) if has_more else None
        return MobileProductsResult(items=window, next_cursor=next_cursor)

    def get_stock_forecast(
        self,
        days: int,
        limit: int,
        tenant_id: Optional[str] = None,
    ) -> MobileStockForecastResponse:
        start, end = self._resolve_period("7d")
        span_days = max((end - start).days + 1, 1)
        raw_items = self.orders_repo.get_top_sku(start, end, limit=limit)
        forecasts: List[MobileStockForecastItem] = []
        for entry in raw_items:
            delivered = int(entry.get("qty_delivered") or 0)
            average_daily = delivered / span_days
            stock = 0  # TODO: replace stub once stock data is available.
            days_left = (stock / average_daily) if average_daily and stock else None
            qty_reco = 0
            forecasts.append(
                MobileStockForecastItem(
                    sku=entry.get("sku") or "",
                    days_left=days_left,
                    stock=stock,
                    qty_reco=qty_reco,
                )
            )
        return MobileStockForecastResponse(items=forecasts)

    def get_finance_breakeven(
        self,
        period: str,
        tenant_id: Optional[str] = None,
    ) -> MobileBreakevenResponse:
        start, end = self._resolve_period(period)
        metrics = self.orders_repo.get_metrics(start, end)
        totals = self._aggregate_totals(metrics)
        fixed_costs = totals["logistics"] + totals["storage"]
        variable_costs = totals["cogs"] + totals["commission"] + totals["ads"]
        point = fixed_costs + variable_costs
        # TODO: incorporate tenant-specific cost adjustments once available.
        return MobileBreakevenResponse(
            point=point,
            fixed_costs=fixed_costs,
            variable_costs=variable_costs,
        )
