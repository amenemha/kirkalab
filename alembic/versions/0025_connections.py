"""croft_accounts + pool_accounts.owner_user_id: подключения людей

Revision ID: 0025_connections
Revises: 0024_support
"""
import sqlalchemy as sa
from alembic import op

revision = "0025_connections"
down_revision = "0024_support"
branch_labels = None
depends_on = None


def upgrade():
    from app.db import activity_models as am
    from app.db.session import Base
    Base.metadata.create_all(bind=op.get_bind(), tables=[Base.metadata.tables["croft_accounts"]], checkfirst=True)
    assert am
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("pool_accounts")}
    if "owner_user_id" not in cols:
        op.add_column("pool_accounts", sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True))
        op.create_index("ix_pool_accounts_owner_user_id", "pool_accounts", ["owner_user_id"])


def downgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "owner_user_id" in {c["name"] for c in insp.get_columns("pool_accounts")}:
        idx = {i["name"] for i in insp.get_indexes("pool_accounts")}
        with op.batch_alter_table("pool_accounts", recreate="always" if bind.dialect.name == "sqlite" else "auto") as b:
            if "ix_pool_accounts_owner_user_id" in idx:
                b.drop_index("ix_pool_accounts_owner_user_id")
            b.drop_column("owner_user_id")
    op.drop_table("croft_accounts")
