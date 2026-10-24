"""Add tenant-scoped research documents, evidence and discovery graph."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = "0010"
down_revision = "0009"
branch_labels = depends_on = None
J = JSONB().with_variant(sa.JSON(), "sqlite")

def upgrade():
    op.add_column("candidates", sa.Column("rejection_reason", sa.Text(), nullable=False, server_default=""))
    op.create_table("research_documents", sa.Column("business_id", sa.String(128), primary_key=True), sa.Column("kind", sa.String(40), primary_key=True), sa.Column("id", sa.String(128), primary_key=True), sa.Column("payload", J, nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("research_sources", sa.Column("business_id", sa.String(128), primary_key=True), sa.Column("id", sa.String(128), primary_key=True), sa.Column("payload", J, nullable=False))
    op.create_table("research_claims", sa.Column("business_id", sa.String(128), primary_key=True), sa.Column("id", sa.String(128), primary_key=True), sa.Column("subject", sa.Text(), nullable=False), sa.Column("payload", J, nullable=False))
    op.create_table("research_claim_sources", sa.Column("business_id", sa.String(128), primary_key=True), sa.Column("claim_id", sa.String(128), primary_key=True), sa.Column("source_id", sa.String(128), primary_key=True), sa.Column("relationship", sa.String(16), primary_key=True), sa.Column("quote", sa.Text(), nullable=False), sa.ForeignKeyConstraint(["business_id", "claim_id"], ["research_claims.business_id", "research_claims.id"]), sa.ForeignKeyConstraint(["business_id", "source_id"], ["research_sources.business_id", "research_sources.id"]))
    op.create_table("research_companies", sa.Column("business_id", sa.String(128), primary_key=True), sa.Column("domain", sa.String(255), primary_key=True), sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False))
    op.create_table("research_discoveries", sa.Column("business_id", sa.String(128), primary_key=True), sa.Column("domain", sa.String(255), primary_key=True), sa.Column("hypothesis_key", sa.String(128), primary_key=True), sa.ForeignKeyConstraint(["business_id", "domain"], ["research_companies.business_id", "research_companies.domain"]))

def downgrade():
    op.drop_column("candidates", "rejection_reason")
    for name in ("research_discoveries", "research_companies", "research_claim_sources", "research_claims", "research_sources", "research_documents"):
        op.drop_table(name)
