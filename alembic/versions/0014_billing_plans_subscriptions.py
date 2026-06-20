"""billing: plans + subscriptions (Telegram Stars PRO), users.premium_until

Revision ID: 0014_billing_plans_subscriptions
Revises: 0013_manual_import_files
Create Date: 2026-06-20 16:00:00.000000

Freemium billing layer (CALC_SPEC §4/§5). Adds:

* ``plans`` — billable tariffs (FREE + PRO monthly/yearly). Prices live here,
  not in code; seeded idempotently at the end of ``upgrade``.
* ``subscriptions`` — one PRO purchase/renewal each. Idempotency is structural:
  ``telegram_payment_charge_id`` is UNIQUE, so a re-delivered ``successful_payment``
  can never double-renew.
* ``users.premium_until`` — authoritative entitlement expiry; ``users.is_pro``
  stays as the fast flag and is the materialized "premium_until in the future".

Stars are whole-number XTR, so every money column is Integer — there is no
NUMERIC here for PostgreSQL precision to overflow (cf. the 0011 efficiency
incident; this migration sidesteps it by construction).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0014_billing_plans_subscriptions"
down_revision: Union[str, None] = "0013_manual_import_files"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# JSONB on PostgreSQL, plain JSON elsewhere (SQLite in tests).
_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("premium_until", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "plans",
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("period_days", sa.Integer(), nullable=True),
        sa.Column(
            "price_stars", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "currency", sa.String(length=8), server_default="XTR", nullable=False
        ),
        sa.Column("features_json", _JSONB, nullable=True),
        sa.Column("limits_json", _JSONB, nullable=True),
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
        "subscriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("plan_code", sa.String(length=32), nullable=False),
        sa.Column(
            "status", sa.String(length=16), server_default="active", nullable=False
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "telegram_payment_charge_id", sa.String(), nullable=False
        ),
        sa.Column(
            "total_amount", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_code"], ["plans.code"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_subscriptions_id"), "subscriptions", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_subscriptions_user_id"),
        "subscriptions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_subscriptions_telegram_payment_charge_id"),
        "subscriptions",
        ["telegram_payment_charge_id"],
        unique=True,
    )
    op.create_index(
        "ix_subscriptions_user_status",
        "subscriptions",
        ["user_id", "status"],
        unique=False,
    )

    # Idempotent seed of the default tariffs (prices live in the table).
    from app.db.seed_plans import seed_plans

    seed_plans(op.get_bind())


def downgrade() -> None:
    op.drop_index(
        "ix_subscriptions_user_status", table_name="subscriptions"
    )
    op.drop_index(
        op.f("ix_subscriptions_telegram_payment_charge_id"),
        table_name="subscriptions",
    )
    op.drop_index(
        op.f("ix_subscriptions_user_id"), table_name="subscriptions"
    )
    op.drop_index(op.f("ix_subscriptions_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.drop_table("plans")
    op.drop_column("users", "premium_until")
