"""add subscriptions and trials tables

Revision ID: 20241030_1201
Revises: 20241022_0001
Create Date: 2024-10-30 12:01:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20241030_1201"
down_revision = "20241022_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("plan", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_subscriptions_user", "subscriptions", ["user_id"])
    op.create_index("ix_subscriptions_tenant", "subscriptions", ["tenant_id"])

    op.create_table(
        "trials",
        sa.Column("user_id", sa.String(length=128), primary_key=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_trials_tenant", "trials", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_trials_tenant", table_name="trials")
    op.drop_table("trials")

    op.drop_index("ix_subscriptions_tenant", table_name="subscriptions")
    op.drop_index("ix_subscriptions_user", table_name="subscriptions")
    op.drop_table("subscriptions")
