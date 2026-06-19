"""device passport card, user_settings, full catalog import

Revision ID: 0005_device_catalog_full_import
Revises: 0004_calc_core_asic_data
Create Date: 2026-06-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0005_device_catalog_full_import"
down_revision: Union[str, None] = "0004_calc_core_asic_data"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_COLUMNS = (
    ("series", sa.String()),
    ("variant", sa.String()),
    ("hashrate_unit", sa.String()),
    ("efficiency_j_per_th", sa.Numeric(12, 4)),
    ("cooling_type", sa.String()),
    ("release_year", sa.Integer()),
    ("voltage_input", sa.String()),
    ("noise_db", sa.Numeric(6, 2)),
    ("operating_temp", sa.String()),
    ("dimensions_mm", sa.String()),
    ("weight_kg", sa.Numeric(8, 3)),
    ("chip", sa.String()),
    ("network", sa.String()),
    ("max_hashrate_note", sa.Text()),
    ("source_url", sa.String()),
    ("notes", sa.Text()),
)


def upgrade() -> None:
    bind = op.get_bind()
    existing_uniques = {
        uc["name"]
        for uc in sa.inspect(bind).get_unique_constraints("device_models")
    }

    # 1. Passport columns on device_models (all nullable).
    with op.batch_alter_table("device_models") as batch:
        for name, col_type in _NEW_COLUMNS:
            batch.add_column(sa.Column(name, col_type, nullable=True))
        # Swap the uniqueness key brand+model_name -> brand+model_name+variant.
        # The old constraint is normally present (created in 0004); guard the
        # drop so a re-upgrade after a downgrade (which does not recreate it)
        # still succeeds.
        if "uq_device_models_brand_model" in existing_uniques:
            batch.drop_constraint(
                "uq_device_models_brand_model", type_="unique"
            )
        batch.create_unique_constraint(
            "uq_device_models_brand_model_variant",
            ["brand", "model_name", "variant"],
        )

    # 2. user_settings table.
    op.create_table(
        "user_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=5), nullable=True),
        sa.Column("default_power_price", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "currency", sa.String(length=8), server_default="USDT", nullable=True
        ),
        sa.Column("timezone", sa.String(), nullable=True),
        sa.Column(
            "hide_small_assets",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", name="uq_user_settings_user_id"),
    )
    op.create_index(
        op.f("ix_user_settings_id"), "user_settings", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_user_settings_user_id"), "user_settings", ["user_id"], unique=False
    )

    # 3. Idempotent import of the full catalog (184 models). Reconciles the 13
    #    starter rows in place so they are not duplicated.
    from app.db.seed_catalog import seed_catalog

    bind = op.get_bind()
    seed_catalog(bind)


def downgrade() -> None:
    op.drop_index(op.f("ix_user_settings_user_id"), table_name="user_settings")
    op.drop_index(op.f("ix_user_settings_id"), table_name="user_settings")
    op.drop_table("user_settings")

    with op.batch_alter_table("device_models") as batch:
        batch.drop_constraint(
            "uq_device_models_brand_model_variant", type_="unique"
        )
        # The old (brand, model_name) unique constraint is intentionally NOT
        # recreated: the imported catalog contains several models with multiple
        # variants sharing a model_name (e.g. Antminer T21 180 TH / 190 TH),
        # which the narrower constraint would reject. Re-add it manually after
        # de-duplicating if a full revert is ever required.
        for name, _ in reversed(_NEW_COLUMNS):
            batch.drop_column(name)
