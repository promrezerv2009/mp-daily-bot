"""initial schema (no ENUM, with CHECKs)

Revision ID: 20241022_0001
Revises:
Create Date: 2024-10-22 00:01:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20241022_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- raw_events ---
    op.create_table(
        "raw_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(length=8), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("platform in ('ozon','wb')", name="ck_raw_events_platform"),
    )
    op.create_index(
        "ix_raw_events_platform_date", "raw_events", ["platform", "event_date"]
    )

    # --- orders ---
    op.create_table(
        "orders",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("platform", sa.String(length=8), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("sku", sa.String(length=100), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("qty_ordered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("qty_delivered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("qty_returned", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("platform in ('ozon','wb')", name="ck_orders_platform"),
    )
    op.create_index("ix_orders_platform_date", "orders", ["platform", "date"])
    op.create_index("ix_orders_date_sku", "orders", ["date", "sku"])

    # --- costs ---
    op.create_table(
        "costs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(length=8), nullable=False),
        sa.Column("sku", sa.String(length=100), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("commission_fee", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("logistics_fee", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("storage_fee", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("cogs_per_unit", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.CheckConstraint("platform in ('ozon','wb')", name="ck_costs_platform"),
    )
    op.create_index("ix_costs_platform_date", "costs", ["platform", "date"])
    op.create_index("ix_costs_date_sku", "costs", ["date", "sku"])
    op.create_unique_constraint(
        "uq_costs_platform_date_sku", "costs", ["platform", "date", "sku"]
    )

    # --- ads_costs ---
    op.create_table(
        "ads_costs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(length=8), nullable=False),
        sa.Column("sku", sa.String(length=100), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.CheckConstraint("platform in ('ozon','wb')", name="ck_ads_costs_platform"),
    )
    op.create_index("ix_ads_costs_platform_date", "ads_costs", ["platform", "date"])
    op.create_unique_constraint(
        "uq_ads_platform_date_sku", "ads_costs", ["platform", "date", "sku"]
    )

    # --- aggregates_daily ---
    op.create_table(
        "aggregates_daily",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("platform", sa.String(length=8), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("orders_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("returns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("revenue_delivered", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("cogs", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("commission", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("logistics", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("storage", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("ads", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("gross_profit", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("profit", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("romi", sa.Float(), nullable=True),
        sa.CheckConstraint("platform in ('ozon','wb')", name="ck_aggregates_platform"),
    )
    op.create_unique_constraint(
        "uq_aggregates_platform_date", "aggregates_daily", ["platform", "date"]
    )
    op.create_index(
        "ix_aggregates_platform_date", "aggregates_daily", ["platform", "date"]
    )


def downgrade() -> None:
    op.drop_index("ix_aggregates_platform_date", table_name="aggregates_daily")
    op.drop_constraint("uq_aggregates_platform_date", "aggregates_daily", type_="unique")
    op.drop_table("aggregates_daily")

    op.drop_constraint("uq_ads_platform_date_sku", "ads_costs", type_="unique")
    op.drop_index("ix_ads_costs_platform_date", table_name="ads_costs")
    op.drop_table("ads_costs")

    op.drop_constraint("uq_costs_platform_date_sku", "costs", type_="unique")
    op.drop_index("ix_costs_date_sku", table_name="costs")
    op.drop_index("ix_costs_platform_date", table_name="costs")
    op.drop_table("costs")

    op.drop_index("ix_orders_date_sku", table_name="orders")
    op.drop_index("ix_orders_platform_date", table_name="orders")
    op.drop_table("orders")

    op.drop_index("ix_raw_events_platform_date", table_name="raw_events")
    op.drop_table("raw_events")
