"""add ab testing tables

Revision ID: 20241030_1202
Revises: 20241030_1201
Create Date: 2024-10-30 12:02:00.000000
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20241030_1202"
down_revision = "20241030_1201"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ab_tests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(length=128), nullable=True),
        sa.Column("product_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_ab_tests_tenant", "ab_tests", ["tenant_id"])
    op.create_index("ix_ab_tests_product_status", "ab_tests", ["product_id", "status"])

    op.create_table(
        "ab_variants",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("test_id", sa.Integer(), sa.ForeignKey("ab_tests.id", ondelete="CASCADE"), nullable=False),
        sa.Column("image_url", sa.String(length=512), nullable=False),
        sa.Column("shows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("orders", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ctr", sa.Float(), nullable=False, server_default="0"),
        sa.Column("cr", sa.Float(), nullable=False, server_default="0"),
    )

    op.create_table(
        "ab_results",
        sa.Column("test_id", sa.Integer(), sa.ForeignKey("ab_tests.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("winner_variant_id", sa.Integer(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("ab_results")
    op.drop_table("ab_variants")
    op.drop_index("ix_ab_tests_product_status", table_name="ab_tests")
    op.drop_index("ix_ab_tests_tenant", table_name="ab_tests")
    op.drop_table("ab_tests")
