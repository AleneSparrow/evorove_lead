"""The site each business asked cycle 1 to search for (roadmap step 20).

Revision ID: 0005
Revises: 0004
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "search_targets",
        sa.Column("business_id", sa.String(128), primary_key=True),
        sa.Column("site_url", sa.Text(), nullable=False),
        sa.Column("business_archetype", sa.String(128), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("last_cold", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("search_targets")
