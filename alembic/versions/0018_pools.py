"""Kirka pools: observer accounts, hourly archive, daily accruals, workers.

Revision ID: 0018_pools
Revises: 0017_farm_croft
"""
from alembic import op

revision = "0018_pools"
down_revision = "0017_farm_croft"
branch_labels = None
depends_on = None

TABLES = ["pool_accounts", "pool_hourly", "pool_daily", "pool_workers"]


def upgrade() -> None:
    from app.db import models  # noqa: F401
    from app.db.session import Base

    bind = op.get_bind()
    for name in TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    from app.db import models  # noqa: F401
    from app.db.session import Base

    bind = op.get_bind()
    for name in reversed(TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
