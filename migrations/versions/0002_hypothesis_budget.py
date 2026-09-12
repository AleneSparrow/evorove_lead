"""Phase 4: accept_rate/close_rate/query_budget on hypotheses.

Revision ID: 0002
Revises: 0001
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("hypotheses", sa.Column("accept_rate", sa.Float(), nullable=False, server_default="0"))
    op.add_column("hypotheses", sa.Column("close_rate", sa.Float(), nullable=False, server_default="0"))
    op.add_column("hypotheses", sa.Column("query_budget", sa.Integer(), nullable=False, server_default="1"))
    op.create_check_constraint(
        "ck_hypotheses_budget_nonnegative", "hypotheses", "query_budget >= 0"
    )
    with op.batch_alter_table("hypotheses") as batch:
        batch.alter_column("accept_rate", server_default=None)
        batch.alter_column("close_rate", server_default=None)
        batch.alter_column("query_budget", server_default=None)


def downgrade() -> None:
    op.drop_constraint("ck_hypotheses_budget_nonnegative", "hypotheses", type_="check")
    op.drop_column("hypotheses", "query_budget")
    op.drop_column("hypotheses", "close_rate")
    op.drop_column("hypotheses", "accept_rate")
