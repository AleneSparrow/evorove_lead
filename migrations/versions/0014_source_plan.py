"""Source catalog id on each signal, and a cache for one model source plan."""
from alembic import op
import sqlalchemy as sa
revision = '0014'
down_revision = '0013'
branch_labels = depends_on = None

def upgrade():
    op.add_column('signals', sa.Column('source_id', sa.String(64), nullable=False, server_default=''))
    op.create_table('source_plans',
        sa.Column('business_id', sa.String(128), primary_key=True),
        sa.Column('brief_hash', sa.String(64), primary_key=True),
        sa.Column('plan_json', sa.Text(), nullable=False),
        sa.Column('model', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False))

def downgrade():
    op.drop_table('source_plans')
    op.drop_column('signals', 'source_id')
