"""calculation_runs: human-readable snapshot columns for the history screen

Revision ID: 0015_calc_run_history_snapshot
Revises: 0014_billing_plans_subscriptions
Create Date: 2026-06-20 16:00:00.000000

Backs the "Мои отчёты / История" screen (Queue 2.3). Two nullable columns are
added to ``calculation_runs`` so the history list/detail can be rendered from a
stored snapshot without re-running the calc (market data changes over time) and
without depending on the catalog model still existing:

* ``device_name``           — human-readable equipment name at calc time, e.g.
                              "Antminer S19 Pro" or "Своё оборудование". Survives
                              catalog renames/removals and covers manual entries
                              (device_model_id is NULL).
* ``net_profit_month_usdt`` — monthly headline result, USDT (Decimal money).

Both columns are NULLABLE so the migration applies cleanly to a table that
already has rows (existing rows keep NULL; the bot renders a graceful fallback).

NUMERIC precision/scale mirror the existing daily figure (18,8): the monthly
profit is ~30× the daily one and stays well within range, so PostgreSQL's
NUMERIC enforcement never overflows. (The SQLite test DB does not enforce
precision — see the prod efficiency overflow incident fixed in 0011 — so the
bounds must be correct by construction.)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0015_calc_run_history_snapshot"
down_revision: Union[str, None] = "0014_billing_plans_subscriptions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "calculation_runs",
        sa.Column("device_name", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "calculation_runs",
        sa.Column("net_profit_month_usdt", sa.Numeric(18, 8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("calculation_runs", "net_profit_month_usdt")
    op.drop_column("calculation_runs", "device_name")
