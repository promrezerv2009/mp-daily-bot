import pytest
from fastapi.testclient import TestClient

from app.api import routes_abtest
from app.main import app


class StubAbTestService:
    def __init__(self):
        self.started = []

    def start(self, tenant_id, product_id, images):
        self.started.append((tenant_id, product_id, images))
        return 42

    def status(self, test_id: int):
        if test_id != 42:
            return None
        return {"id": 42, "status": "running", "variants": [], "result": None}

    def finalize(self, test_id: int):
        if test_id != 42:
            return {"ok": False, "reason": "not_found"}
        return {"ok": True, "test_id": 42, "status": "finished", "winner_variant_id": 7}


@pytest.fixture(autouse=True)
def override_abtest_service():
    original = routes_abtest.service
    routes_abtest.service = StubAbTestService()
    yield
    routes_abtest.service = original


client = TestClient(app)


def test_abtest_start_endpoint():
    response = client.post(
        "/api/abtest/start",
        json={"tenant_id": "t1", "product_id": "p1", "images": ["http://img/1.jpg"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["id"] == 42


def test_abtest_status_endpoint():
    response = client.get("/api/abtest/status", params={"id": 42})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["data"]["id"] == 42


def test_abtest_stop_endpoint():
    response = client.post("/api/abtest/stop", params={"id": 42})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["result"]["winner_variant_id"] == 7
