from contextlib import contextmanager
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import routes_billing
from app.main import app
from app.models import Base, Subscription, Trial


@pytest.fixture
def in_memory_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [Subscription.__table__, Trial.__table__]
    Base.metadata.create_all(engine, tables=tables)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    @contextmanager
    def _session_scope():
        session: Session = SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    monkeypatch.setattr(routes_billing, "session_scope", _session_scope)
    monkeypatch.setattr(routes_billing, "Subscription", Subscription)
    monkeypatch.setattr(routes_billing, "Trial", Trial)
    yield SessionLocal
    Base.metadata.drop_all(engine, tables=tables)


def test_activate_subscription_creates_record(in_memory_db):
    client = TestClient(app)
    response = client.post(
        "/api/billing/activate",
        json={"user_id": "u1", "tenant_id": "t1", "plan": "trial", "days": 5},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["status"] == "active"
    expires = datetime.fromisoformat(payload["expires_at"])
    assert expires.tzinfo is not None

    SessionLocal = in_memory_db
    with SessionLocal() as session:
        subscription = session.execute(
            select(Subscription).where(Subscription.user_id == "u1")
        ).scalar_one()
        trial = session.get(Trial, "u1")
    assert subscription.plan == "trial"
    assert trial is not None
    assert subscription.expires_at.date() == trial.expires_at.date()
