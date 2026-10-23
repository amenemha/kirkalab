"""container layout override

Revision ID: 0020_container_layout
Revises: 0019_pool_note
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0020_container_layout"
down_revision = "0019_pool_note"
branch_labels = None
depends_on = None


def upgrade():
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("farm_containers")}
    if "layout" in cols:
        return
    op.add_column("farm_containers", sa.Column("layout", JSONB().with_variant(sa.Text(), "sqlite"), nullable=True))


def downgrade():
    op.drop_column("farm_containers", "layout")
