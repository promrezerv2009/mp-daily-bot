from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, Iterable, Optional


@dataclass
class MetricAccumulator:
    orders_count: int = 0
    delivered: int = 0
    returns: int = 0
    revenue_delivered: float = 0.0
    cogs: float = 0.0
    commission: float = 0.0
    logistics: float = 0.0
    storage: float = 0.0
    ads: float = 0.0

    def register_order(
        self,
        qty_ordered: int,
        qty_delivered: int,
        qty_returned: int,
        price: float,
        cogs_per_unit: float,
        commission_fee: float,
        logistics_fee: float,
        storage_fee: float,
        ads_amount: float,
    ) -> None:
        self.orders_count += qty_ordered
        self.delivered += qty_delivered
        self.returns += qty_returned
        self.revenue_delivered += price * qty_delivered
        self.cogs += cogs_per_unit * qty_delivered
        self.commission += commission_fee
        self.logistics += logistics_fee
        self.storage += storage_fee
        self.ads += ads_amount

    @property
    def gross_profit(self) -> float:
        return (
            self.revenue_delivered
            - self.cogs
            - self.commission
            - self.logistics
            - self.storage
        )

    @property
    def profit(self) -> float:
        return self.gross_profit - self.ads

    @property
    def romi(self) -> Optional[float]:
        return self.profit / self.ads if self.ads else None


def calculate_daily_metrics(
    orders: Iterable[Dict],
    costs: Iterable[Dict],
    ads_costs: Iterable[Dict],
) -> Dict[str, Dict[date, MetricAccumulator]]:
    cost_index: Dict[tuple, Dict] = {}
    for entry in costs:
        key = (
            entry["platform"],
            entry["sku"],
            entry["date"],
        )
        cost_index[key] = entry

    ads_index: Dict[tuple, float] = defaultdict(float)
    for entry in ads_costs:
        key = (
            entry["platform"],
            entry.get("sku"),
            entry["date"],
        )
        ads_index[key] += entry["amount"]

    metrics: Dict[str, Dict[date, MetricAccumulator]] = defaultdict(lambda: defaultdict(MetricAccumulator))  # type: ignore

    for order in orders:
        platform = order["platform"]
        order_date = order["date"]
        sku = order["sku"]
        qty_delivered = int(order.get("qty_delivered", 0))
        qty_ordered = int(order.get("qty_ordered", qty_delivered))
        qty_returned = int(order.get("qty_returned", 0))
        price = float(order.get("price", 0.0))
        cost = cost_index.get((platform, sku, order_date), {})
        cogs_per_unit = float(cost.get("cogs_per_unit") or 0.0)
        commission_fee = float(cost.get("commission_fee") or 0.0)
        logistics_fee = float(cost.get("logistics_fee") or 0.0)
        storage_fee = float(cost.get("storage_fee") or 0.0)
        ads_amount = ads_index.get((platform, sku, order_date), 0.0)
        accumulator = metrics[platform][order_date]
        accumulator.register_order(
            qty_ordered=qty_ordered,
            qty_delivered=qty_delivered,
            qty_returned=qty_returned,
            price=price,
            cogs_per_unit=cogs_per_unit,
            commission_fee=commission_fee,
            logistics_fee=logistics_fee,
            storage_fee=storage_fee,
            ads_amount=ads_amount,
        )
    for entry in ads_costs:
        if entry.get("sku") in (None, "", "None"):
            platform = entry["platform"]
            ad_date = entry["date"]
            metrics[platform][ad_date].ads += entry["amount"]

    return metrics


def summarize(metrics: Dict[str, Dict[date, MetricAccumulator]]) -> Dict[str, Dict]:
    summary: Dict[str, Dict] = {}
    totals = MetricAccumulator()
    for platform, by_date in metrics.items():
        platform_acc = MetricAccumulator()
        for acc in by_date.values():
            platform_acc.orders_count += acc.orders_count
            platform_acc.delivered += acc.delivered
            platform_acc.returns += acc.returns
            platform_acc.revenue_delivered += acc.revenue_delivered
            platform_acc.cogs += acc.cogs
            platform_acc.commission += acc.commission
            platform_acc.logistics += acc.logistics
            platform_acc.storage += acc.storage
            platform_acc.ads += acc.ads
        summary[platform] = {
            "orders_count": platform_acc.orders_count,
            "delivered": platform_acc.delivered,
            "returns": platform_acc.returns,
            "revenue_delivered": platform_acc.revenue_delivered,
            "cogs": platform_acc.cogs,
            "commission": platform_acc.commission,
            "logistics": platform_acc.logistics,
            "storage": platform_acc.storage,
            "ads": platform_acc.ads,
            "gross_profit": platform_acc.gross_profit,
            "profit": platform_acc.profit,
            "romi": platform_acc.romi,
        }
        totals.orders_count += platform_acc.orders_count
        totals.delivered += platform_acc.delivered
        totals.returns += platform_acc.returns
        totals.revenue_delivered += platform_acc.revenue_delivered
        totals.cogs += platform_acc.cogs
        totals.commission += platform_acc.commission
        totals.logistics += platform_acc.logistics
        totals.storage += platform_acc.storage
        totals.ads += platform_acc.ads
    summary["total"] = {
        "orders_count": totals.orders_count,
        "delivered": totals.delivered,
        "returns": totals.returns,
        "revenue_delivered": totals.revenue_delivered,
        "cogs": totals.cogs,
        "commission": totals.commission,
        "logistics": totals.logistics,
        "storage": totals.storage,
        "ads": totals.ads,
        "gross_profit": totals.gross_profit,
        "profit": totals.profit,
        "romi": totals.romi,
    }
    return summary
