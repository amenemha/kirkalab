"""Кошельки: уход денег и последняя удачная проверка; ссылки пулов: состояние оповещений.

Revision ID: 0027_outgoing
Revises: 0026_wallets
"""
import sqlalchemy as sa
from alembic import op

revision = "0027_outgoing"
down_revision = "0026_wallets"
branch_labels = None
depends_on = None


def _add(table: str, col: sa.Column) -> None:
    # 0026 создаёт таблицы кошельков по текущей модели — на новой базе колонки уже есть
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}
    if col.name not in have:
        op.add_column(table, col)


def upgrade():
    _add("wallet_watch", sa.Column("last_ok", sa.DateTime(timezone=True)))
    _add("wallet_watch", sa.Column("outgoing", sa.Boolean(), server_default="false", nullable=False))
    _add("wallet_tx", sa.Column("to_addr", sa.String(128)))
    _add("wallet_tx", sa.Column("direction", sa.String(3), server_default="in", nullable=False))
    op.execute("UPDATE wallet_watch SET last_ok = last_check WHERE last_error IS NULL AND last_ok IS NULL")
    _add("pool_accounts", sa.Column("alert", sa.JSON()))


def downgrade():
    op.drop_column("pool_accounts", "alert")
    op.drop_column("wallet_tx", "direction")
    op.drop_column("wallet_tx", "to_addr")
    op.drop_column("wallet_watch", "outgoing")
    op.drop_column("wallet_watch", "last_ok")
