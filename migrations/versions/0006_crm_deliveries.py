"""Durable outbox for CRM lead-touch delivery: failed POSTs are queued and retried, not dropped.

Revision ID: 0006
Revises: 0005
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "crm_deliveries",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("business_id", sa.String(128), nullable=False),
        sa.Column("touch_id", sa.String(128), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(none_as_null=True).with_variant(sa.JSON(none_as_null=True), "sqlite"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("business_id", "touch_id", name="uq_crm_deliveries_business_touch"),
    )


def downgrade() -> None:
    op.drop_table("crm_deliveries")
