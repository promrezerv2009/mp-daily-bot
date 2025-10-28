from datetime import date
from typing import Optional

from sqlalchemy import (
    Column,
    Date,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


PLATFORM_ENUM = Enum("ozon", "wb", name="platform_enum")


class RawEvent(Base):
    __tablename__ = "raw_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(PLATFORM_ENUM, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_raw_events_platform_date", "platform", "event_date"),
    )


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    platform: Mapped[str] = mapped_column(PLATFORM_ENUM, nullable=False)
    order_date: Mapped[date] = mapped_column("date", Date, nullable=False)
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    qty_ordered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qty_delivered: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    qty_returned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        Index("ix_orders_platform_date", "platform", "date"),
        Index("ix_orders_date_sku", "date", "sku"),
    )


class Cost(Base):
    __tablename__ = "costs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(PLATFORM_ENUM, nullable=False)
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    cost_date: Mapped[date] = mapped_column("date", Date, nullable=False)
    commission_fee: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    logistics_fee: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    storage_fee: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    cogs_per_unit: Mapped[float] = mapped_column(Numeric(12, 2), default=0)

    __table_args__ = (
        UniqueConstraint("platform", "date", "sku", name="uq_costs_platform_date_sku"),
        Index("ix_costs_platform_date", "platform", "date"),
        Index("ix_costs_date_sku", "date", "sku"),
    )


class AdsCost(Base):
    __tablename__ = "ads_costs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(PLATFORM_ENUM, nullable=False)
    sku: Mapped[str] = mapped_column(String(100), nullable=True)
    ads_date: Mapped[date] = mapped_column("date", Date, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("platform", "date", "sku", name="uq_ads_platform_date_sku"),
        Index("ix_ads_costs_platform_date", "platform", "date"),
    )


class DailyAggregate(Base):
    __tablename__ = "aggregates_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(PLATFORM_ENUM, nullable=False)
    aggregate_date: Mapped[date] = mapped_column("date", Date, nullable=False)
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    delivered: Mapped[int] = mapped_column(Integer, default=0)
    returns: Mapped[int] = mapped_column(Integer, default=0)
    revenue_delivered: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    cogs: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    commission: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    logistics: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    storage: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    ads: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    gross_profit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    profit: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    romi: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("platform", "date", name="uq_aggregates_platform_date"),
        Index("ix_aggregates_platform_date", "platform", "date"),
    )
