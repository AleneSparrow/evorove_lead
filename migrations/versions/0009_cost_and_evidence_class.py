"""Evidence class, Cold identity claims, lifetime spend, search-run cost.

Revision ID: 0009
Revises: 0008
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column("evidence_class", sa.String(32), nullable=False, server_default=""),
    )
    with op.batch_alter_table("candidates") as batch:
        batch.alter_column("evidence_class", server_default=None)
    op.add_column(
        "search_targets",
        sa.Column("last_cost", sa.Text(), nullable=False, server_default=""),
    )
    with op.batch_alter_table("search_targets") as batch:
        batch.alter_column("last_cost", server_default=None)
    op.create_table(
        "cold_identity_claims",
        sa.Column("business_id", sa.String(128), primary_key=True),
        sa.Column("identity_key", sa.String(320), primary_key=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "pipeline_spend",
        sa.Column("business_id", sa.String(128), primary_key=True),
        sa.Column("llm_calls", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("search_requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unknown_price", sa.Boolean(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("pipeline_spend")
    op.drop_table("cold_identity_claims")
    op.drop_column("search_targets", "last_cost")
    op.drop_column("candidates", "evidence_class")
