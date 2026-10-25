"""Collect-once source warehouse beside the existing research pipeline."""
from alembic import op
import sqlalchemy as sa
revision = '0013'
down_revision = '0012'
branch_labels = depends_on = None

def upgrade():
    op.create_table('companies', sa.Column('business_id',sa.String(128),primary_key=True),sa.Column('domain',sa.String(255),primary_key=True),sa.Column('name',sa.String(255),nullable=False),sa.Column('site_url',sa.Text,nullable=False),sa.Column('suppressed',sa.Boolean,nullable=False,server_default=sa.false()),sa.Column('collected_at',sa.DateTime(timezone=True)),sa.Column('expires_at',sa.DateTime(timezone=True)))
    op.create_table('people',sa.Column('business_id',sa.String(128),primary_key=True),sa.Column('domain',sa.String(255),primary_key=True),sa.Column('name_key',sa.String(255),primary_key=True),sa.Column('name',sa.String(255),nullable=False),sa.Column('role',sa.String(128),nullable=False,server_default=''),sa.Column('suppressed',sa.Boolean,nullable=False,server_default=sa.false()),sa.ForeignKeyConstraint(['business_id','domain'],['companies.business_id','companies.domain']))
    op.create_table('contacts',sa.Column('business_id',sa.String(128),primary_key=True),sa.Column('domain',sa.String(255),primary_key=True),sa.Column('email',sa.String(320),primary_key=True),sa.Column('name_key',sa.String(255),nullable=False),sa.Column('mx_verified',sa.Boolean,nullable=False),sa.Column('evidence_url',sa.Text,nullable=False),sa.Column('suppressed',sa.Boolean,nullable=False,server_default=sa.false()),sa.ForeignKeyConstraint(['business_id','domain','name_key'],['people.business_id','people.domain','people.name_key']))
    op.create_table('raw_pages',sa.Column('business_id',sa.String(128),primary_key=True),sa.Column('domain',sa.String(255),primary_key=True),sa.Column('url',sa.Text,primary_key=True),sa.Column('text',sa.Text,nullable=False),sa.Column('fetched_at',sa.DateTime(timezone=True),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),sa.Column('sha256',sa.String(64),nullable=False),sa.ForeignKeyConstraint(['business_id','domain'],['companies.business_id','companies.domain']))
    op.create_table('signals',sa.Column('business_id',sa.String(128),primary_key=True),sa.Column('source_url',sa.Text,primary_key=True),sa.Column('domain',sa.String(255),nullable=False),sa.Column('source',sa.String(32),nullable=False),sa.Column('signal_type',sa.String(64),nullable=False),sa.Column('signal_date',sa.DateTime(timezone=True),nullable=False),sa.Column('quote',sa.Text,nullable=False),sa.Column('source_text',sa.Text,nullable=False),sa.Column('fetched_at',sa.DateTime(timezone=True),nullable=False),sa.Column('context_only',sa.Boolean,nullable=False,server_default=sa.false()),sa.Column('is_advertisement',sa.Boolean,nullable=False,server_default=sa.false()),sa.ForeignKeyConstraint(['business_id','domain'],['companies.business_id','companies.domain']))

def downgrade():
    for name in ('signals','raw_pages','contacts','people','companies'):
        op.drop_table(name)
