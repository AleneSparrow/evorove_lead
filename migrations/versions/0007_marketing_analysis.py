"""Marketing reading on the brief, and audience source on each hypothesis.

Revision ID: 0007
Revises: 0006
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_VALUE = postgresql.JSONB(none_as_null=True).with_variant(sa.JSON(none_as_null=True), "sqlite")


def upgrade() -> None:
    op.add_column("briefs", sa.Column("marketing_analysis", JSON_VALUE, nullable=True))
    op.add_column(
        "hypotheses",
        sa.Column("audience_source", sa.String(16), nullable=False, server_default="literal"),
    )
    op.add_column(
        "hypotheses",
        sa.Column("evidence_quote", sa.Text(), nullable=False, server_default=""),
    )
    op.create_check_constraint(
        "ck_hypotheses_audience_source",
        "hypotheses",
        "audience_source IN ('literal','ai_inferred')",
    )
    with op.batch_alter_table("hypotheses") as batch:
        batch.alter_column("audience_source", server_default=None)
        batch.alter_column("evidence_quote", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_hypotheses_audience_source", "hypotheses", type_="check")
    op.drop_column("hypotheses", "evidence_quote")
    op.drop_column("hypotheses", "audience_source")
    op.drop_column("briefs", "marketing_analysis")
