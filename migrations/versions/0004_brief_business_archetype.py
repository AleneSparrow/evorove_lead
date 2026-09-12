"""Store the owner-set business_archetype on each brief snapshot.

Revision ID: 0004
Revises: 0003
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "briefs",
        sa.Column("business_archetype", sa.String(128), nullable=False, server_default=""),
    )
    with op.batch_alter_table("briefs") as batch:
        batch.alter_column("business_archetype", server_default=None)


def downgrade() -> None:
    op.drop_column("briefs", "business_archetype")
