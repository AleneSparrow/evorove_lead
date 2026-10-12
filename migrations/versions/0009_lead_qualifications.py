"""Qualifications, feedback, and LLM usage for cycle 1.

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
    op.create_table(
        "lead_qualifications",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("business_id", sa.String(128), nullable=False),
        sa.Column("hypothesis_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("trace_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("query_template", sa.Text(), nullable=False, server_default=""),
        sa.Column("segment", sa.String(16), nullable=False),
        sa.Column("intent_class", sa.String(32), nullable=False),
        sa.Column("entity_type", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("problem", sa.Text(), nullable=True),
        sa.Column("icp_match", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("evidence_quote", sa.Text(), nullable=True),
        sa.Column("evidence_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("publication_date", sa.String(64), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("commercial_relevance", sa.String(16), nullable=False, server_default="unknown"),
        sa.Column("recommended_action", sa.Text(), nullable=False, server_default=""),
        sa.Column("reasoning_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(64), nullable=False, server_default=""),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd_micros", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("blocked_reason", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("business_id", "source_url", "content_sha256", name="uq_lead_qualifications_business_url_sha"),
        sa.CheckConstraint(
            "segment IN ('opportunity','prospect','review','rejected')",
            name="ck_lead_qualifications_segment",
        ),
        sa.CheckConstraint(
            "intent_class IN ('EXPLICIT_INTENT','INFERRED_OPPORTUNITY','ICP_MATCH','INSUFFICIENT_EVIDENCE','IRRELEVANT')",
            name="ck_lead_qualifications_intent",
        ),
    )
    op.create_table(
        "lead_feedback",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("business_id", sa.String(128), nullable=False),
        sa.Column("qualification_id", sa.String(128), nullable=False),
        sa.Column("hypothesis_id", sa.String(128), nullable=False, server_default=""),
        sa.Column("query_template", sa.Text(), nullable=False, server_default=""),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("budget_delta", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "kind IN ('relevant','irrelevant','incorrect_classification','duplicate','outdated','incorrect_icp','insufficient_evidence')",
            name="ck_lead_feedback_kind",
        ),
        sa.CheckConstraint("budget_delta IN (-1, 0, 1)", name="ck_lead_feedback_delta"),
    )
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("business_id", sa.String(128), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_usd_micros", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("blocked_reason", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("llm_usage")
    op.drop_table("lead_feedback")
    op.drop_table("lead_qualifications")
