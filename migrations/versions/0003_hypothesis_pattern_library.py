"""Phase 4: system-wide hypothesis_pattern_library. Not tenant-scoped -- no business_id here.

Revision ID: 0003
Revises: 0002
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hypothesis_pattern_library",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("business_archetype", sa.String(128), nullable=False),
        sa.Column("channel_family", sa.String(64), nullable=False),
        sa.Column("query_pattern", sa.String(64), nullable=False),
        sa.Column("observed_close_rate_band", sa.String(16), nullable=False),
        sa.Column("sample_size", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "business_archetype", "channel_family", "query_pattern",
            name="uq_hypothesis_pattern_library_key",
        ),
        sa.CheckConstraint(
            "observed_close_rate_band IN ('low','medium','high')",
            name="ck_hypothesis_pattern_library_known_band",
        ),
        sa.CheckConstraint("sample_size >= 0", name="ck_hypothesis_pattern_library_sample_nonnegative"),
    )


def downgrade() -> None:
    op.drop_table("hypothesis_pattern_library")
