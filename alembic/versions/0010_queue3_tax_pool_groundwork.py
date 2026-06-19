"""queue 3 groundwork: pool / wallet / tax schema (no business logic)

Revision ID: 0010_queue3_tax_pool_groundwork
Revises: 0006_firmware_presets_builds
Create Date: 2026-06-19 16:00:00.000000

Neutral database groundwork for "Queue 3" (RU tax module + mining-pool
integration). Schema and foreign keys only -- no services, endpoints, pool
parsers or report generation. Every logical column is nullable; only PKs, FKs
and technical columns are required.

A high, unique revision number is used on purpose so this migration applies
cleanly on top of the current head even if a parallel PR introduces its own
0007/0008/0009 revision.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


# revision identifiers, used by Alembic.
revision: str = "0010_queue3_tax_pool_groundwork"
down_revision: Union[str, None] = "0006_firmware_presets_builds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# JSONB on PostgreSQL, plain JSON elsewhere (e.g. SQLite in tests).
def _json_type() -> sa.types.TypeEngine:
    return sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    # 1. pool_connections -- read-only observer links to mining pools.
    op.create_table(
        "pool_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("pool_code", sa.String(), nullable=False),
        sa.Column("observer_url", sa.Text(), nullable=True),
        sa.Column("access_key_encrypted", sa.Text(), nullable=True),
        sa.Column("coin", sa.String(), nullable=True),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pool_connections_id"), "pool_connections", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_pool_connections_user_id"),
        "pool_connections",
        ["user_id"],
        unique=False,
    )

    # 2. pool_earnings -- normalized daily earnings from a pool connection.
    op.create_table(
        "pool_earnings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("pool_connection_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("coin", sa.String(), nullable=True),
        sa.Column("amount_crypto", sa.Numeric(30, 12), nullable=True),
        sa.Column(
            "source", sa.String(), server_default="pool", nullable=False
        ),
        sa.Column("raw_json", _json_type(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["pool_connection_id"], ["pool_connections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_pool_earnings_id"), "pool_earnings", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_pool_earnings_pool_connection_id"),
        "pool_earnings",
        ["pool_connection_id"],
        unique=False,
    )

    # 3. wallet_sources -- on-chain wallets for the tax report.
    op.create_table(
        "wallet_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("chain", sa.String(), nullable=True),
        sa.Column("address", sa.String(), nullable=True),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_wallet_sources_id"), "wallet_sources", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_wallet_sources_user_id"),
        "wallet_sources",
        ["user_id"],
        unique=False,
    )

    # 4. wallet_earnings -- incoming on-chain credits per wallet.
    op.create_table(
        "wallet_earnings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("wallet_source_id", sa.Integer(), nullable=False),
        sa.Column("tx_hash", sa.String(), nullable=True),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("coin", sa.String(), nullable=True),
        sa.Column("amount_crypto", sa.Numeric(30, 12), nullable=True),
        sa.Column("raw_json", _json_type(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["wallet_source_id"], ["wallet_sources.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_wallet_earnings_id"), "wallet_earnings", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_wallet_earnings_wallet_source_id"),
        "wallet_earnings",
        ["wallet_source_id"],
        unique=False,
    )

    # 5. tax_rates -- FX / asset rate on the crediting date.
    op.create_table(
        "tax_rates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=True),
        sa.Column("coin", sa.String(), nullable=True),
        sa.Column("currency", sa.String(), nullable=True),
        sa.Column("rate", sa.Numeric(30, 12), nullable=True),
        sa.Column("source", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tax_rates_id"), "tax_rates", ["id"], unique=False)
    op.create_index(
        "ix_tax_rates_date_coin_currency_source",
        "tax_rates",
        ["date", "coin", "currency", "source"],
        unique=False,
    )

    # 6. tax_reports -- generated reports.
    op.create_table(
        "tax_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("period_type", sa.String(), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column(
            "jurisdiction", sa.String(), server_default="RU", nullable=False
        ),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("file_path", sa.Text(), nullable=True),
        sa.Column("totals_json", _json_type(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_tax_reports_id"), "tax_reports", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_tax_reports_user_id"),
        "tax_reports",
        ["user_id"],
        unique=False,
    )

    # 7. tax_deductions -- deductible expenses.
    op.create_table(
        "tax_deductions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tax_report_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["tax_report_id"], ["tax_reports.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_tax_deductions_id"), "tax_deductions", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_tax_deductions_user_id"),
        "tax_deductions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tax_deductions_tax_report_id"),
        "tax_deductions",
        ["tax_report_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_tax_deductions_tax_report_id"), table_name="tax_deductions"
    )
    op.drop_index(
        op.f("ix_tax_deductions_user_id"), table_name="tax_deductions"
    )
    op.drop_index(op.f("ix_tax_deductions_id"), table_name="tax_deductions")
    op.drop_table("tax_deductions")

    op.drop_index(op.f("ix_tax_reports_user_id"), table_name="tax_reports")
    op.drop_index(op.f("ix_tax_reports_id"), table_name="tax_reports")
    op.drop_table("tax_reports")

    op.drop_index(
        "ix_tax_rates_date_coin_currency_source", table_name="tax_rates"
    )
    op.drop_index(op.f("ix_tax_rates_id"), table_name="tax_rates")
    op.drop_table("tax_rates")

    op.drop_index(
        op.f("ix_wallet_earnings_wallet_source_id"),
        table_name="wallet_earnings",
    )
    op.drop_index(
        op.f("ix_wallet_earnings_id"), table_name="wallet_earnings"
    )
    op.drop_table("wallet_earnings")

    op.drop_index(
        op.f("ix_wallet_sources_user_id"), table_name="wallet_sources"
    )
    op.drop_index(
        op.f("ix_wallet_sources_id"), table_name="wallet_sources"
    )
    op.drop_table("wallet_sources")

    op.drop_index(
        op.f("ix_pool_earnings_pool_connection_id"),
        table_name="pool_earnings",
    )
    op.drop_index(op.f("ix_pool_earnings_id"), table_name="pool_earnings")
    op.drop_table("pool_earnings")

    op.drop_index(
        op.f("ix_pool_connections_user_id"), table_name="pool_connections"
    )
    op.drop_index(
        op.f("ix_pool_connections_id"), table_name="pool_connections"
    )
    op.drop_table("pool_connections")
