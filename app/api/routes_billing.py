from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.models import Subscription, Trial

router = APIRouter(prefix="/billing", tags=["billing"])


class ActivateBody(BaseModel):
    user_id: str = Field(..., min_length=1)
    tenant_id: Optional[str] = None
    plan: str = Field("trial", min_length=1)
    days: int = Field(ge=1, default=None)


@router.post("/activate")
def activate_subscription(payload: ActivateBody):
    settings = get_settings()
    activation_days = payload.days or settings.free_trial_days
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=activation_days)

    with session_scope() as session:
        subscription = session.execute(
            select(Subscription).where(Subscription.user_id == payload.user_id)
        ).scalar_one_or_none()

        if subscription:
            subscription.tenant_id = payload.tenant_id
            subscription.plan = payload.plan
            subscription.status = "active"
            subscription.expires_at = expires_at
            if not subscription.started_at:
                subscription.started_at = now
        else:
            subscription = Subscription(
                user_id=payload.user_id,
                tenant_id=payload.tenant_id,
                plan=payload.plan,
                status="active",
                started_at=now,
                expires_at=expires_at,
            )
            session.add(subscription)

        if payload.plan == "trial":
            trial = session.get(Trial, payload.user_id)
            if trial:
                trial.tenant_id = payload.tenant_id
                trial.expires_at = expires_at
            else:
                session.add(
                    Trial(
                        user_id=payload.user_id,
                        tenant_id=payload.tenant_id,
                        started_at=now,
                        expires_at=expires_at,
                    )
                )

    return {"ok": True, "status": "active", "expires_at": expires_at.isoformat()}
