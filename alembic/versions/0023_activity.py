"""activity_events: журнал действий бота/сайта и отправленных уведомлений

Revision ID: 0023_activity
Revises: 0022_energy
"""
from alembic import op

revision = "0023_activity"
down_revision = "0022_energy"
branch_labels = None
depends_on = None


def upgrade():
    from app.db import activity_models as am
    from app.db.session import Base
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables["activity_events"]], checkfirst=True)
    assert am


def downgrade():
    op.drop_table("activity_events")
