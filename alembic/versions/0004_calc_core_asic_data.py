"""calc core: asic data, device profiles, market snapshots

Revision ID: 0004_calc_core_asic_data
Revises: 0003_token_revocation
Create Date: 2026-06-19 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0004_calc_core_asic_data"
down_revision: Union[str, None] = "0003_token_revocation"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
  op.create_table(
    "device_models",
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("brand", sa.String(), nullable=False),
    sa.Column("model_name", sa.String(), nullable=False),
    sa.Column("algorithm", sa.String(), nullable=False),
    sa.Column("coin_family", sa.String(), nullable=False),
    sa.Column("default_hashrate_ths", sa.Numeric(12, 2), nullable=False),
    sa.Column("default_power_w", sa.Integer(), nullable=False),
    sa.Column("released_at", sa.Date(), nullable=True),
    sa.Column("is_active", sa.Boolean(), nullable=False),
    sa.Column("data_quality", sa.String(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint("id"),
    sa.UniqueConstraint("brand", "model_name", name="uq_device_models_brand_model"),
  )
  op.create_index(op.f("ix_device_models_id"), "device_models", ["id"], unique=False)
  op.create_index(op.f("ix_device_models_brand"), "device_models", ["brand"], unique=False)
  op.create_index(op.f("ix_device_models_model_name"), "device_models", ["model_name"], unique=False)

  op.create_table(
    "device_profiles",
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("owner_user_id", sa.Integer(), nullable=True),
    sa.Column("base_model_id", sa.Integer(), nullable=True),
    sa.Column("profile_type", sa.String(), nullable=False),
    sa.Column("name", sa.String(), nullable=False),
    sa.Column("hashrate_ths", sa.Numeric(12, 2), nullable=False),
    sa.Column("power_w", sa.Integer(), nullable=False),
    sa.Column("cooling_type", sa.String(), nullable=True),
    sa.Column("is_public", sa.Boolean(), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="CASCADE"),
    sa.ForeignKeyConstraint(["base_model_id"], ["device_models.id"], ondelete="SET NULL"),
    sa.PrimaryKeyConstraint("id"),
  )
  op.create_index(op.f("ix_device_profiles_id"), "device_profiles", ["id"], unique=False)
  op.create_index(op.f("ix_device_profiles_owner_user_id"), "device_profiles", ["owner_user_id"], unique=False)

  op.create_table(
    "market_snapshots",
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("source", sa.String(), nullable=False),
    sa.Column("coin_code", sa.String(), nullable=False),
    sa.Column("network_difficulty", sa.Numeric(30, 2), nullable=False),
    sa.Column("block_reward_btc", sa.Numeric(12, 8), nullable=False),
    sa.Column("price_usdt", sa.Numeric(18, 8), nullable=False),
    sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    sa.PrimaryKeyConstraint("id"),
  )
  op.create_index(op.f("ix_market_snapshots_id"), "market_snapshots", ["id"], unique=False)
  op.create_index(op.f("ix_market_snapshots_coin_code"), "market_snapshots", ["coin_code"], unique=False)
  op.create_index(op.f("ix_market_snapshots_captured_at"), "market_snapshots", ["captured_at"], unique=False)

  # Seed the starter ASIC catalog. Kept idempotent (skip rows that already
  # exist by brand + model_name) so re-running the data step is safe.
  from app.db.seed_asic import seed_device_models

  bind = op.get_bind()
  seed_device_models(bind)


def downgrade() -> None:
  op.drop_index(op.f("ix_market_snapshots_captured_at"), table_name="market_snapshots")
  op.drop_index(op.f("ix_market_snapshots_coin_code"), table_name="market_snapshots")
  op.drop_index(op.f("ix_market_snapshots_id"), table_name="market_snapshots")
  op.drop_table("market_snapshots")

  op.drop_index(op.f("ix_device_profiles_owner_user_id"), table_name="device_profiles")
  op.drop_index(op.f("ix_device_profiles_id"), table_name="device_profiles")
  op.drop_table("device_profiles")

  op.drop_index(op.f("ix_device_models_model_name"), table_name="device_models")
  op.drop_index(op.f("ix_device_models_brand"), table_name="device_models")
  op.drop_index(op.f("ix_device_models_id"), table_name="device_models")
  op.drop_table("device_models")
