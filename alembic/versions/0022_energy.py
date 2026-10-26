"""energy: nodes, meters, hourly, readings, imports, asic plugs

Revision ID: 0022_energy
Revises: 0021_farm_settings
"""
from alembic import op

revision = "0022_energy"
down_revision = "0021_farm_settings"
branch_labels = None
depends_on = None

TABLES = ["energy_nodes", "energy_meters", "energy_imports", "energy_hourly", "energy_readings", "asic_plugs"]


def upgrade():
    from app.db import energy_models as em
    from app.db.session import Base
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, tables=[Base.metadata.tables[t] for t in TABLES], checkfirst=True)
    from app.services.energy.plugs import seed_plugs
    seed_plugs(bind)
    assert em


def downgrade():
    for t in reversed(TABLES):
        op.drop_table(t)
