from typing import Optional

import pytest
from fastapi.testclient import TestClient

from app.api.routes_mobile import get_mobile_service
from app.main import app
from app.schemas_mobile import (
    MobileBreakevenResponse,
    MobileProduct,
    MobileStockForecastItem,
    MobileStockForecastResponse,
    MobileSummary,
)
from app.services.mobile import MobileProductsResult


class StubMobileService:
    def get_summary(self, period: str, tenant_id: Optional[str] = None) -> MobileSummary:
        return MobileSummary(revenue=1000.0, profit=250.0, orders=42, roi=0.25)

    def get_products(
        self,
        limit: int,
        cursor: int,
        sort: str,
        tenant_id: Optional[str] = None,
    ) -> MobileProductsResult:
        items = [
            MobileProduct(sku="SKU1", name="Product 1", margin=0.4, price=500.0, orders=10, stock=5),
            MobileProduct(sku="SKU2", name="Product 2", margin=0.3, price=750.0, orders=8, stock=2),
        ]
        return MobileProductsResult(items=items[:limit], next_cursor=None)

    def get_stock_forecast(
        self,
        days: int,
        limit: int,
        tenant_id: Optional[str] = None,
    ) -> MobileStockForecastResponse:
        return MobileStockForecastResponse(
            items=[
                MobileStockForecastItem(sku="SKU1", days_left=5.0, stock=10, qty_reco=20),
            ]
        )

    def get_finance_breakeven(
        self,
        period: str,
        tenant_id: Optional[str] = None,
    ) -> MobileBreakevenResponse:
        return MobileBreakevenResponse(point=1500.0, fixed_costs=600.0, variable_costs=400.0)


client = TestClient(app)


@pytest.fixture(autouse=True)
def override_mobile_service():
    app.dependency_overrides[get_mobile_service] = lambda: StubMobileService()
    yield
    app.dependency_overrides.pop(get_mobile_service, None)


def test_mobile_summary_endpoint():
    response = client.get("/api/mobile/summary?period=7d")
    assert response.status_code == 200
    payload = response.json()
    for key in {"revenue", "profit", "orders", "roi"}:
        assert key in payload


def test_mobile_products_endpoint():
    response = client.get("/api/mobile/products?limit=5&sort=margin")
    assert response.status_code == 200
    payload = response.json()
    assert "items" in payload
    assert isinstance(payload["items"], list)
    if payload["items"]:
        first = payload["items"][0]
        for key in {"sku", "name", "margin", "price", "orders", "stock"}:
            assert key in first


def test_mobile_stock_forecast_endpoint():
    response = client.get("/api/mobile/stocks/forecast?days=7&limit=5")
    assert response.status_code == 200
    payload = response.json()
    assert "items" in payload
    if payload["items"]:
        first = payload["items"][0]
        for key in {"sku", "days_left", "stock", "qty_reco"}:
            assert key in first


def test_mobile_breakeven_endpoint():
    response = client.get("/api/mobile/finance/breakeven")
    assert response.status_code == 200
    payload = response.json()
    for key in {"point", "fixed_costs", "variable_costs"}:
        assert key in payload
