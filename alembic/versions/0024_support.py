"""support_messages: переписка через бота и рассылки из админки

Revision ID: 0024_support
Revises: 0023_activity
"""
from alembic import op

revision = "0024_support"
down_revision = "0023_activity"
branch_labels = None
depends_on = None


def upgrade():
    from app.db import activity_models as am
    from app.db.session import Base
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables["support_messages"]], checkfirst=True)
    assert am


def downgrade():
    op.drop_table("support_messages")
