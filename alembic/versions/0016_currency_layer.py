"""currency layer: currencies + fx_rates (Queue 3 currency layer)

Revision ID: 0016_currency_layer
Revises: 0015_calc_run_history_snapshot
Create Date: 2026-06-20 18:00:00.000000

Adds the FX/currency layer so calc results can be presented in local fiat
(USD/RUB/KZT/UAH/EUR) on top of the existing USDT economics, which are left
untouched (backwards compatible — ``calculation_runs.*_usdt`` columns are not
changed).

* ``currencies`` — the supported currency catalog (anchor USDT + fiats). Prices
  /symbols live here, not in code; seeded idempotently at the end of ``upgrade``
  the same way ``plans`` are seeded in 0014.
* ``fx_rates``   — append-only observed rates ``1 base = rate quote``. The
  conversion service reads the latest row per (base, quote) pair; keeping the
  history doubles as the durable fallback cache (last good rate when the source
  is down) and an audit trail.

``rate`` is ``NUMERIC(18, 8)`` — ample range for both strong (≈0.92) and weak
(≈41) USDT→fiat rates, so PostgreSQL NUMERIC never overflows. USDT is always the
stored base; fiat↔fiat is derived in the service via the USDT cross-rate, never
persisted, so no large intermediate is written. (The SQLite test DB does not
enforce precision — see the 0011 efficiency overflow incident — so the bounds
must be correct by construction.)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0016_currency_layer"
down_revision: Union[str, None] = "0015_calc_run_history_snapshot"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "currencies",
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("symbol", sa.String(length=8), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("decimals", sa.Integer(), server_default="2", nullable=False),
        sa.Column(
            "is_fiat", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column(
            "is_active", sa.Boolean(), server_default="true", nullable=False
        ),
        sa.Column(
            "sort_order", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("code"),
    )

    op.create_table(
        "fx_rates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("base_currency", sa.String(length=8), nullable=False),
        sa.Column("quote_currency", sa.String(length=8), nullable=False),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
        sa.Column(
            "source",
            sa.String(length=32),
            server_default="coingecko",
            nullable=False,
        ),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "base_currency",
            "quote_currency",
            "fetched_at",
            name="uq_fx_rates_base_quote_fetched",
        ),
    )
    op.create_index(op.f("ix_fx_rates_id"), "fx_rates", ["id"], unique=False)
    op.create_index(
        op.f("ix_fx_rates_fetched_at"), "fx_rates", ["fetched_at"], unique=False
    )
    op.create_index(
        "ix_fx_rates_pair_fetched",
        "fx_rates",
        ["base_currency", "quote_currency", "fetched_at"],
        unique=False,
    )

    # Idempotent seed of the supported currencies (catalog lives in the table).
    from app.db.seed_currencies import seed_currencies

    seed_currencies(op.get_bind())


def downgrade() -> None:
    op.drop_index("ix_fx_rates_pair_fetched", table_name="fx_rates")
    op.drop_index(op.f("ix_fx_rates_fetched_at"), table_name="fx_rates")
    op.drop_index(op.f("ix_fx_rates_id"), table_name="fx_rates")
    op.drop_table("fx_rates")
    op.drop_table("currencies")
