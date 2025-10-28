from datetime import date
from typing import Dict

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models import AdsCost


class AdsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_ads_spend(self, date_from: date, date_to: date) -> Dict[str, float]:
        stmt = (
            select(
                AdsCost.platform,
                func.sum(AdsCost.amount).label("ads"),
            )
            .where(
                and_(
                    AdsCost.ads_date >= date_from,
                    AdsCost.ads_date <= date_to,
                )
            )
            .group_by(AdsCost.platform)
        )
        result = self.session.execute(stmt).all()
        return {row.platform: float(row.ads or 0) for row in result}
