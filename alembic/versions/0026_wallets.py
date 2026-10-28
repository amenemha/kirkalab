"""wallet_watch + wallet_tx: наблюдение кошельков

Revision ID: 0026_wallets
Revises: 0025_connections
"""
from alembic import op

revision = "0026_wallets"
down_revision = "0025_connections"
branch_labels = None
depends_on = None


def upgrade():
    from app.db import wallet_models as wm
    from app.db.session import Base
    assert wm
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables["wallet_watch"], Base.metadata.tables["wallet_tx"]], checkfirst=True)


def downgrade():
    op.drop_table("wallet_tx")
    op.drop_table("wallet_watch")
