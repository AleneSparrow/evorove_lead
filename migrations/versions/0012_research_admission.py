"""Durable admission attribution and tenant run leases; retain existing outbox."""
from alembic import op
import sqlalchemy as sa
revision = "0012"
down_revision = "0011"
branch_labels = depends_on = None

def upgrade():
    op.create_table("research_admissions", sa.Column("business_id", sa.String(128), primary_key=True), sa.Column("contact_key", sa.String(64), primary_key=True), sa.Column("candidate_id", sa.String(128), nullable=False), sa.Column("hypothesis_key", sa.String(128), nullable=False), sa.Column("cohort_id", sa.String(128), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.ForeignKeyConstraint(["business_id", "candidate_id"], ["candidates.business_id", "candidates.id"]))
    op.create_table("research_run_leases", sa.Column("business_id", sa.String(128), primary_key=True), sa.Column("run_id", sa.String(128), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))

    op.create_table("research_candidate_claims", sa.Column("business_id", sa.String(128), primary_key=True), sa.Column("candidate_id", sa.String(128), primary_key=True), sa.Column("claim_id", sa.String(128), primary_key=True), sa.ForeignKeyConstraint(["business_id", "candidate_id"], ["candidates.business_id", "candidates.id"]), sa.ForeignKeyConstraint(["business_id", "claim_id"], ["research_claims.business_id", "research_claims.id"]))

def downgrade():
    op.drop_table("research_candidate_claims")
    op.drop_table("research_run_leases")
    op.drop_table("research_admissions")
