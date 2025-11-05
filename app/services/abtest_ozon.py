from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from app.config import get_settings
from app.db import session_scope
from app.models import AbResult, AbTest, AbVariant


class AbTestService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._ozon_client_id = self.settings.ozon_client_id
        self._ozon_api_key = self.settings.ozon_api_key

    def start(self, tenant_id: Optional[str], product_id: str, images: List[str]) -> int:
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            test = AbTest(
                tenant_id=tenant_id,
                product_id=product_id,
                status="running",
                started_at=now,
            )
            session.add(test)
            session.flush()

            for image_url in images:
                session.add(
                    AbVariant(
                        test_id=test.id,
                        image_url=image_url,
                    )
                )
            session.flush()
            # TODO: call Ozon Product API to set the first variant as active.
            return test.id

    def next_variant(self, test_id: int) -> Optional[AbVariant]:
        # TODO: rotate active image via Ozon API.
        with session_scope() as session:
            variant = (
                session.query(AbVariant)
                .filter(AbVariant.test_id == test_id)
                .order_by(AbVariant.id)
                .first()
            )
            return variant

    def collect_metrics(self, test_id: int) -> dict:
        # TODO: fetch real analytics from Ozon Analytics API.
        with session_scope() as session:
            variants = (
                session.query(AbVariant)
                .filter(AbVariant.test_id == test_id)
                .order_by(AbVariant.id)
                .all()
            )
            return {
                "test_id": test_id,
                "variants": [
                    {
                        "id": variant.id,
                        "image_url": variant.image_url,
                        "shows": variant.shows,
                        "clicks": variant.clicks,
                        "orders": variant.orders,
                        "ctr": variant.ctr,
                        "cr": variant.cr,
                    }
                    for variant in variants
                ],
            }

    def status(self, test_id: int) -> Optional[dict]:
        with session_scope() as session:
            test = session.get(AbTest, test_id)
            if not test:
                return None
            variants = (
                session.query(AbVariant)
                .filter(AbVariant.test_id == test_id)
                .order_by(AbVariant.id)
                .all()
            )
            result = session.get(AbResult, test_id)
            return {
                "id": test.id,
                "status": test.status,
                "started_at": test.started_at.isoformat(),
                "finished_at": test.finished_at.isoformat() if test.finished_at else None,
                "variants": [
                    {
                        "id": variant.id,
                        "image_url": variant.image_url,
                        "shows": variant.shows,
                        "clicks": variant.clicks,
                        "orders": variant.orders,
                        "ctr": variant.ctr,
                        "cr": variant.cr,
                    }
                    for variant in variants
                ],
                "result": {
                    "winner_variant_id": result.winner_variant_id,
                    "finished_at": result.finished_at.isoformat(),
                }
                if result
                else None,
            }

    def finalize(self, test_id: int) -> dict:
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            test = session.get(AbTest, test_id)
            if not test:
                return {"ok": False, "reason": "not_found"}

            test.status = "finished"
            test.finished_at = now
            winner = (
                session.query(AbVariant)
                .filter(AbVariant.test_id == test_id)
                .order_by(AbVariant.orders.desc(), AbVariant.clicks.desc())
                .first()
            )
            winner_id = winner.id if winner else 0
            session.merge(
                AbResult(
                    test_id=test_id,
                    winner_variant_id=winner_id,
                    finished_at=now,
                )
            )
            # TODO: reset product image to winning variant via Ozon API.
            return {
                "ok": True,
                "test_id": test_id,
                "status": "finished",
                "winner_variant_id": winner_id,
            }
