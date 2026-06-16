"""initial

Revision ID: 0001_initial
Revises: 
Create Date: 2026-06-16 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  op.create_table(
    "users",
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("email", sa.String(), nullable=False),
    sa.Column("handle", sa.String(), nullable=False),
    sa.Column("hashed_password", sa.String(), nullable=False),
    sa.Column("is_active", sa.Boolean(), nullable=False),
    sa.Column("is_admin", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint("id"),
  )
  op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
  op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
  op.create_index(op.f("ix_users_handle"), "users", ["handle"], unique=True)


def downgrade() -> None:
  op.drop_index(op.f("ix_users_handle"), table_name="users")
  op.drop_index(op.f("ix_users_email"), table_name="users")
  op.drop_index(op.f("ix_users_id"), table_name="users")
  op.drop_table("users")
