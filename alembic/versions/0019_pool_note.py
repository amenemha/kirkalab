"""pool_accounts.note

Revision ID: 0019_pool_note
Revises: 0018_pools
"""
import sqlalchemy as sa
from alembic import op

revision = "0019_pool_note"
down_revision = "0018_pools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("pool_accounts")}
    if "note" not in cols:
        op.add_column("pool_accounts", sa.Column("note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("pool_accounts", "note")
