"""Candidate pain and its local evidence; no evidence goes to cycle 2."""
from alembic import op
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = depends_on = None


def upgrade():
    for name in ('observed_pain', 'pain_evidence_excerpt', 'pain_evidence_source'):
        op.add_column('candidates', sa.Column(name, sa.Text(), nullable=False, server_default=''))


def downgrade():
    for name in ('pain_evidence_source', 'pain_evidence_excerpt', 'observed_pain'):
        op.drop_column('candidates', name)
