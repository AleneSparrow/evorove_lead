"""Initial analysis warehouse schema: briefs, hypotheses, traces, rejections, candidates, outcomes.

Revision ID: 0001
Revises: None
"""

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_VALUE = postgresql.JSONB(none_as_null=True).with_variant(sa.JSON(none_as_null=True), "sqlite")


def upgrade() -> None:
    op.create_table(
        "briefs",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("business_id", sa.String(128), nullable=False),
        sa.Column("what_we_sell", JSON_VALUE, nullable=False),
        sa.Column("who_may_fit", JSON_VALUE, nullable=False),
        sa.Column("commercial_claims", JSON_VALUE, nullable=False),
        sa.Column("must_not_promise", JSON_VALUE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("business_id", "id", name="uq_briefs_business_id_id"),
    )
    op.create_index("ix_briefs_business", "briefs", ["business_id", "created_at"])

    op.create_table(
        "hypotheses",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("business_id", sa.String(128), nullable=False),
        sa.Column("brief_id", sa.String(128), nullable=False),
        sa.Column("audience_segment", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(64), nullable=False),
        sa.Column("query_template", sa.Text(), nullable=False),
        sa.Column("intent_trigger", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("fit_score", sa.Float(), nullable=False),
        sa.Column("evidence_score", sa.Float(), nullable=False),
        sa.Column("reach_estimate", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("business_id", "id", name="uq_hypotheses_business_id_id"),
        sa.ForeignKeyConstraint(
            ["business_id", "brief_id"], ["briefs.business_id", "briefs.id"],
            name="fk_hypotheses_tenant_brief", ondelete="CASCADE",
        ),
        sa.CheckConstraint("status IN ('live','dead','paused')", name="ck_hypotheses_known_status"),
    )
    op.create_index("ix_hypotheses_business_status", "hypotheses", ["business_id", "status"])

    op.create_table(
        "traces",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("business_id", sa.String(128), nullable=False),
        sa.Column("hypothesis_id", sa.String(128), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("query_used", sa.Text(), nullable=False),
        sa.Column("language", sa.String(16)),
        sa.Column("geo_hint", sa.String(255)),
        sa.Column("source_channel", sa.String(64), nullable=False),
        sa.UniqueConstraint("business_id", "id", name="uq_traces_business_id_id"),
        sa.ForeignKeyConstraint(
            ["business_id", "hypothesis_id"], ["hypotheses.business_id", "hypotheses.id"],
            name="fk_traces_tenant_hypothesis", ondelete="CASCADE",
        ),
    )
    op.create_index("ix_traces_business_hypothesis", "traces", ["business_id", "hypothesis_id"])

    op.create_table(
        "rejected_traces",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("business_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128)),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("business_id", "id", name="uq_rejected_traces_business_id_id"),
    )
    op.create_index("ix_rejected_traces_business", "rejected_traces", ["business_id", "rejected_at"])

    op.create_table(
        "candidates",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("business_id", sa.String(128), nullable=False),
        sa.Column("hypothesis_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128)),
        sa.Column("identity", sa.Text(), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(64)),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reason_source", sa.Text(), nullable=False),
        sa.Column("fit", sa.Float(), nullable=False),
        sa.Column("evidence", sa.Float(), nullable=False),
        sa.Column("addressable", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("business_id", "id", name="uq_candidates_business_id_id"),
        sa.ForeignKeyConstraint(
            ["business_id", "hypothesis_id"], ["hypotheses.business_id", "hypotheses.id"],
            name="fk_candidates_tenant_hypothesis", ondelete="CASCADE",
        ),
        sa.CheckConstraint("decision IN ('cold','rejected')", name="ck_candidates_known_decision"),
    )
    op.create_index("ix_candidates_business_hypothesis", "candidates", ["business_id", "hypothesis_id"])

    op.create_table(
        "hypothesis_outcomes",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("business_id", sa.String(128), nullable=False),
        sa.Column("hypothesis_id", sa.String(128), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["business_id", "hypothesis_id"], ["hypotheses.business_id", "hypotheses.id"],
            name="fk_hypothesis_outcomes_tenant_hypothesis", ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "outcome IN ('done','dropped','offer_made','in_progress')",
            name="ck_hypothesis_outcomes_known_outcome",
        ),
    )
    op.create_index(
        "ix_hypothesis_outcomes_business_hypothesis",
        "hypothesis_outcomes",
        ["business_id", "hypothesis_id"],
    )


def downgrade() -> None:
    op.drop_table("hypothesis_outcomes")
    op.drop_table("candidates")
    op.drop_table("rejected_traces")
    op.drop_table("traces")
    op.drop_table("hypotheses")
    op.drop_table("briefs")
