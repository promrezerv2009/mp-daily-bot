from typing import List, Optional

from pydantic import BaseModel, Field


class MobileSummary(BaseModel):
    revenue: float = 0.0
    profit: float = 0.0
    orders: int = 0
    roi: Optional[float] = Field(default=None, description="Profit to revenue ratio.")


class MobileProduct(BaseModel):
    sku: str
    name: str
    margin: float
    price: float
    orders: int
    stock: int


class MobileProductsResponse(BaseModel):
    items: List[MobileProduct]
    next_cursor: Optional[int] = None


class MobileStockForecastItem(BaseModel):
    sku: str
    days_left: Optional[float] = None
    stock: int
    qty_reco: int


class MobileStockForecastResponse(BaseModel):
    items: List[MobileStockForecastItem]


class MobileBreakevenResponse(BaseModel):
    point: float
    fixed_costs: float
    variable_costs: float
