from datetime import date
from typing import Dict, List

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import AdsCost, Cost, DailyAggregate, Order


class OrdersRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_metrics(self, date_from: date, date_to: date) -> Dict[str, Dict[str, float]]:
        stmt = (
            select(
                DailyAggregate.platform,
                func.sum(DailyAggregate.orders_count).label("orders_count"),
                func.sum(DailyAggregate.delivered).label("delivered"),
                func.sum(DailyAggregate.returns).label("returns"),
                func.sum(DailyAggregate.revenue_delivered).label("revenue_delivered"),
                func.sum(DailyAggregate.cogs).label("cogs"),
                func.sum(DailyAggregate.commission).label("commission"),
                func.sum(DailyAggregate.logistics).label("logistics"),
                func.sum(DailyAggregate.storage).label("storage"),
                func.sum(DailyAggregate.ads).label("ads"),
                func.sum(DailyAggregate.gross_profit).label("gross_profit"),
                func.sum(DailyAggregate.profit).label("profit"),
            )
            .where(
                and_(
                    DailyAggregate.aggregate_date >= date_from,
                    DailyAggregate.aggregate_date <= date_to,
                )
            )
            .group_by(DailyAggregate.platform)
        )
        result = self.session.execute(stmt).all()
        aggregates: Dict[str, Dict[str, float]] = {}
        for row in result:
            romi = float(row.profit) / float(row.ads) if row.ads and float(row.ads) != 0 else None
            aggregates[row.platform] = {
                "orders_count": int(row.orders_count or 0),
                "delivered": int(row.delivered or 0),
                "returns": int(row.returns or 0),
                "revenue_delivered": float(row.revenue_delivered or 0),
                "cogs": float(row.cogs or 0),
                "commission": float(row.commission or 0),
                "logistics": float(row.logistics or 0),
                "storage": float(row.storage or 0),
                "ads": float(row.ads or 0),
                "gross_profit": float(row.gross_profit or 0),
                "profit": float(row.profit or 0),
                "romi": romi,
            }
        return aggregates

    def get_top_sku(self, date_from: date, date_to: date, limit: int = 10) -> List[dict]:
        cost_fields = (
            func.coalesce(Cost.cogs_per_unit, 0) * func.coalesce(Order.qty_delivered, 0)
        )
        stmt = (
            select(
                Order.sku,
                Order.platform,
                func.sum(Order.qty_delivered * Order.price).label("revenue"),
                func.sum(Order.qty_delivered).label("qty_delivered"),
                func.sum(cost_fields).label("cogs"),
                func.sum(func.coalesce(Cost.commission_fee, 0)).label("commission"),
                func.sum(func.coalesce(Cost.logistics_fee, 0)).label("logistics"),
                func.sum(func.coalesce(Cost.storage_fee, 0)).label("storage"),
                func.sum(func.coalesce(AdsCost.amount, 0)).label("ads"),
            )
            .outerjoin(
                Cost,
                and_(
                    Cost.platform == Order.platform,
                    Cost.sku == Order.sku,
                    Cost.cost_date == Order.order_date,
                ),
            )
            .outerjoin(
                AdsCost,
                and_(
                    AdsCost.platform == Order.platform,
                    AdsCost.sku == Order.sku,
                    AdsCost.ads_date == Order.order_date,
                ),
            )
            .where(
                and_(
                    Order.order_date >= date_from,
                    Order.order_date <= date_to,
                )
            )
            .group_by(Order.sku, Order.platform)
            .order_by(func.sum(Order.qty_delivered * Order.price).desc())
            .limit(limit)
        )

        result = self.session.execute(stmt).all()
        items: List[dict] = []
        for row in result:
            revenue = float(row.revenue or 0)
            cogs = float(row.cogs or 0)
            commission = float(row.commission or 0)
            logistics = float(row.logistics or 0)
            storage = float(row.storage or 0)
            ads = float(row.ads or 0)
            profit = revenue - (cogs + commission + logistics + storage + ads)

            items.append(
                {
                    "sku": row.sku,
                    "platform": row.platform,
                    "revenue": revenue,
                    "profit": profit,
                    "qty_delivered": int(row.qty_delivered or 0),
                }
            )
        return items
