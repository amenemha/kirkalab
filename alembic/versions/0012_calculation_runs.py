"""calculation_runs: per-user calc log backing the FREE funnel/limits

Revision ID: 0012_calculation_runs
Revises: 0011_fix_efficiency_non_ths
Create Date: 2026-06-20 12:00:00.000000

Records each profitability calculation a user performs in the bot. The row
count (and how many fall on the current UTC day) drives the FREE funnel: the
5 intro calculations, the 3/day cap thereafter, and the currency-blur stages.

NUMERIC precision/scale are deliberately modest and correct by construction so
PostgreSQL's NUMERIC enforcement never overflows. (The SQLite test DB does not
enforce, so the bounds cannot be validated there — see the prod efficiency
overflow incident fixed in 0011.)

A high, unique revision number is used so this applies cleanly on top of the
current head even alongside a parallel PR.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0012_calculation_runs"
down_revision: Union[str, None] = "0011_fix_efficiency_non_ths"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "calculation_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("device_model_id", sa.Integer(), nullable=True),
        sa.Column("hashrate_ths", sa.Numeric(12, 2), nullable=False),
        sa.Column("power_w", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("power_price", sa.Numeric(12, 4), nullable=False),
        sa.Column(
            "currency", sa.String(length=8), server_default="USDT", nullable=False
        ),
        sa.Column("net_profit_day_usdt", sa.Numeric(18, 8), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["device_model_id"], ["device_models.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_calculation_runs_id"), "calculation_runs", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_calculation_runs_user_id"),
        "calculation_runs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_calculation_runs_created_at"),
        "calculation_runs",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "ix_calculation_runs_user_created",
        "calculation_runs",
        ["user_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_calculation_runs_user_created", table_name="calculation_runs")
    op.drop_index(
        op.f("ix_calculation_runs_created_at"), table_name="calculation_runs"
    )
    op.drop_index(
        op.f("ix_calculation_runs_user_id"), table_name="calculation_runs"
    )
    op.drop_index(op.f("ix_calculation_runs_id"), table_name="calculation_runs")
    op.drop_table("calculation_runs")
