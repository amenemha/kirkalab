"""refresh token revocation and versioning

Revision ID: 0003_token_revocation
Revises: 0002_qr_login
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0003_token_revocation"
down_revision: Union[str, None] = "0002_qr_login"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  op.add_column(
    "users",
    sa.Column(
      "token_version", sa.Integer(), server_default="0", nullable=False
    ),
  )

  op.create_table(
    "revoked_tokens",
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("jti", sa.String(), nullable=False),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint("id"),
  )
  op.create_index(op.f("ix_revoked_tokens_id"), "revoked_tokens", ["id"], unique=False)
  op.create_index(op.f("ix_revoked_tokens_jti"), "revoked_tokens", ["jti"], unique=True)


def downgrade() -> None:
  op.drop_index(op.f("ix_revoked_tokens_jti"), table_name="revoked_tokens")
  op.drop_index(op.f("ix_revoked_tokens_id"), table_name="revoked_tokens")
  op.drop_table("revoked_tokens")
  op.drop_column("users", "token_version")
