from datetime import date
from typing import Dict, Iterable

from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import DailyAggregate


class AggregatesRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_aggregate(self, payload: Dict) -> None:
        stmt = insert(DailyAggregate).values(**payload)
        update_columns = {
            "orders_count": stmt.excluded.orders_count,
            "delivered": stmt.excluded.delivered,
            "returns": stmt.excluded.returns,
            "revenue_delivered": stmt.excluded.revenue_delivered,
            "cogs": stmt.excluded.cogs,
            "commission": stmt.excluded.commission,
            "logistics": stmt.excluded.logistics,
            "storage": stmt.excluded.storage,
            "ads": stmt.excluded.ads,
            "gross_profit": stmt.excluded.gross_profit,
            "profit": stmt.excluded.profit,
            "romi": stmt.excluded.romi,
        }
        stmt = stmt.on_conflict_do_update(
            constraint="uq_aggregates_platform_date",
            set_=update_columns,
        )
        self.session.execute(stmt)

    def remove_range(self, platform: str, date_from: date, date_to: date) -> None:
        self.session.query(DailyAggregate).filter(
            and_(
                DailyAggregate.platform == platform,
                DailyAggregate.aggregate_date >= date_from,
                DailyAggregate.aggregate_date <= date_to,
            )
        ).delete(synchronize_session=False)

    def list_range(self, date_from: date, date_to: date) -> Iterable[DailyAggregate]:
        stmt = (
            select(DailyAggregate)
            .where(
                and_(
                    DailyAggregate.aggregate_date >= date_from,
                    DailyAggregate.aggregate_date <= date_to,
                )
            )
            .order_by(DailyAggregate.aggregate_date)
        )
        return (row[0] for row in self.session.execute(stmt))
