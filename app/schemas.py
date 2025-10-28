from datetime import date
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field, validator


class PeriodEnum(str, Enum):
    yesterday = "yesterday"
    last_7 = "7"
    last_14 = "14"
    month = "month"
    custom = "custom"


class DateRange(BaseModel):
    period: Optional[PeriodEnum] = None
    date_from: Optional[date] = Field(default=None, alias="from")
    date_to: Optional[date] = Field(default=None, alias="to")

    @validator("date_to")
    def validate_range(cls, v: Optional[date], values: dict) -> Optional[date]:
        start = values.get("date_from")
        if start and v and v < start:
            raise ValueError("date_to must be greater than or equal to date_from")
        return v


class MetricBreakdown(BaseModel):
    platform: str
    revenue_delivered: float = 0.0
    cogs: float = 0.0
    commission: float = 0.0
    logistics: float = 0.0
    storage: float = 0.0
    ads: float = 0.0
    gross_profit: float = 0.0
    profit: float = 0.0
    romi: Optional[float] = None
    orders_count: int = 0
    delivered: int = 0
    returns: int = 0


class MetricsResponse(BaseModel):
    period: DateRange
    totals: MetricBreakdown
    platforms: List[MetricBreakdown]


class OrdersResponse(BaseModel):
    period: DateRange
    totals: MetricBreakdown
    platforms: List[MetricBreakdown]


class AdsResponse(BaseModel):
    period: DateRange
    totals: MetricBreakdown
    platforms: List[MetricBreakdown]


class TopSkuItem(BaseModel):
    sku: str
    platform: str
    revenue: float
    profit: float
    qty_delivered: int


class TopSkuResponse(BaseModel):
    period: DateRange
    items: List[TopSkuItem]


class ExportResponse(BaseModel):
    detail: str
