"""Track 4: news items already checked against each business (dedup for the LLM filter).

Revision ID: 0008
Revises: 0007
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_signals",
        sa.Column("business_id", sa.String(128), primary_key=True),
        sa.Column("source_url", sa.Text(), primary_key=True),
        sa.Column("snippet", sa.Text(), nullable=False, server_default=""),
        sa.Column("relevant", sa.Boolean(), nullable=False),
        sa.Column("segment_label", sa.Text(), nullable=False, server_default=""),
        sa.Column("channel", sa.String(32), nullable=False, server_default=""),
        sa.Column("evidence_quote", sa.Text(), nullable=False, server_default=""),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("market_signals")
