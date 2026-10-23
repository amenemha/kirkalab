"""Kirka farm: CROFT sites, containers, clients, devices, history, incidents.

Revision ID: 0017_farm_croft
Revises: 0016_currency_layer
"""
from alembic import op

revision = "0017_farm_croft"
down_revision = "0016_currency_layer"
branch_labels = None
depends_on = None

TABLES = [
    "farm_sites", "farm_containers", "farm_clients", "farm_devices", "farm_device_events",
    "farm_device_hourly", "farm_container_samples", "farm_incidents", "farm_sync_runs",
]


def upgrade() -> None:
    from app.db import farm_models as fm  # noqa: F401
    from app.db.session import Base

    bind = op.get_bind()
    for name in TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)


def downgrade() -> None:
    from app.db.session import Base
    from app.db import farm_models as fm  # noqa: F401

    bind = op.get_bind()
    for name in reversed(TABLES):
        Base.metadata.tables[name].drop(bind, checkfirst=True)
