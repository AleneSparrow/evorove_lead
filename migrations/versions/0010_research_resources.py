"""Persistent tenant budget, usage accounting and cache."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = "0010"
down_revision = "0009"
branch_labels = depends_on = None
J = JSONB().with_variant(sa.JSON(), "sqlite")

def upgrade():
    op.create_table("research_budget_days", sa.Column("business_id", sa.String(128), primary_key=True), sa.Column("day", sa.String(10), primary_key=True), sa.Column("spent", sa.BigInteger(), nullable=False), sa.CheckConstraint("spent >= 0"))
    op.create_table("research_cache", sa.Column("business_id", sa.String(128), primary_key=True), sa.Column("key", sa.String(64), primary_key=True), sa.Column("payload", J, nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("research_usage", sa.Column("business_id", sa.String(128), primary_key=True), sa.Column("id", sa.String(128), primary_key=True), sa.Column("run_id", sa.String(128), nullable=False), sa.Column("payload", J, nullable=False), sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False))

def downgrade():
    for name in ("research_usage", "research_cache", "research_budget_days"): op.drop_table(name)
