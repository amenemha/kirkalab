"""qr telegram login

Revision ID: 0002_qr_login
Revises: 0001_initial
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0002_qr_login"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  op.add_column("users", sa.Column("telegram_user_id", sa.BigInteger(), nullable=True))
  op.create_index(
    op.f("ix_users_telegram_user_id"), "users", ["telegram_user_id"], unique=True
  )

  op.create_table(
    "qr_login_sessions",
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("session_id", sa.String(), nullable=False),
    sa.Column("status", sa.String(), nullable=False),
    sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
    sa.Column("user_id", sa.Integer(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
    sa.PrimaryKeyConstraint("id"),
  )
  op.create_index(op.f("ix_qr_login_sessions_id"), "qr_login_sessions", ["id"], unique=False)
  op.create_index(
    op.f("ix_qr_login_sessions_session_id"), "qr_login_sessions", ["session_id"], unique=True
  )


def downgrade() -> None:
  op.drop_index(op.f("ix_qr_login_sessions_session_id"), table_name="qr_login_sessions")
  op.drop_index(op.f("ix_qr_login_sessions_id"), table_name="qr_login_sessions")
  op.drop_table("qr_login_sessions")
  op.drop_index(op.f("ix_users_telegram_user_id"), table_name="users")
  op.drop_column("users", "telegram_user_id")
