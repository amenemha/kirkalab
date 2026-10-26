"""farm settings (key/value)

Revision ID: 0021_farm_settings
Revises: 0020_container_layout
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0021_farm_settings"
down_revision = "0020_container_layout"
branch_labels = None
depends_on = None


def upgrade():
    if "farm_settings" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "farm_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", JSONB().with_variant(sa.Text(), "sqlite"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table("farm_settings")
