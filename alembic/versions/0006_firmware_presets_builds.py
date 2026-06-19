"""firmware presets, user firmware builds, users.is_pro

Revision ID: 0006_firmware_presets_builds
Revises: 0005_device_catalog_full_import
Create Date: 2026-06-19 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0006_firmware_presets_builds"
down_revision: Union[str, None] = "0005_device_catalog_full_import"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. PRO flag on users.
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "is_pro",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )

    # 2. firmware_presets (system-wide tuning points).
    op.create_table(
        "firmware_presets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_model_id", sa.Integer(), nullable=False),
        sa.Column("firmware", sa.String(), nullable=False),
        sa.Column("preset_name", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("hashrate", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "hashrate_unit",
            sa.String(),
            server_default="TH/s",
            nullable=False,
        ),
        sa.Column("power_w", sa.Numeric(12, 2), nullable=False),
        sa.Column("efficiency_j_per_th", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "is_system", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["device_model_id"], ["device_models.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "device_model_id",
            "firmware",
            "preset_name",
            name="uq_firmware_presets_model_fw_name",
        ),
    )
    op.create_index(
        op.f("ix_firmware_presets_id"), "firmware_presets", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_firmware_presets_device_model_id"),
        "firmware_presets",
        ["device_model_id"],
        unique=False,
    )

    # 3. user_firmware_builds (PRO: user-named custom builds).
    op.create_table(
        "user_firmware_builds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("device_model_id", sa.Integer(), nullable=False),
        sa.Column("build_name", sa.String(), nullable=False),
        sa.Column("firmware", sa.String(), nullable=True),
        sa.Column("mode", sa.String(), nullable=True),
        sa.Column("hashrate", sa.Numeric(12, 2), nullable=False),
        sa.Column(
            "hashrate_unit",
            sa.String(),
            server_default="TH/s",
            nullable=False,
        ),
        sa.Column("power_w", sa.Numeric(12, 2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["device_model_id"], ["device_models.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_user_firmware_builds_id"),
        "user_firmware_builds",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_firmware_builds_user_id"),
        "user_firmware_builds",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_firmware_builds_device_model_id"),
        "user_firmware_builds",
        ["device_model_id"],
        unique=False,
    )

    # 4. Idempotent seed of system firmware presets for popular BTC models.
    from app.db.seed_firmware import seed_firmware_presets

    seed_firmware_presets(op.get_bind())


def downgrade() -> None:
    op.drop_index(
        op.f("ix_user_firmware_builds_device_model_id"),
        table_name="user_firmware_builds",
    )
    op.drop_index(
        op.f("ix_user_firmware_builds_user_id"),
        table_name="user_firmware_builds",
    )
    op.drop_index(
        op.f("ix_user_firmware_builds_id"), table_name="user_firmware_builds"
    )
    op.drop_table("user_firmware_builds")

    op.drop_index(
        op.f("ix_firmware_presets_device_model_id"),
        table_name="firmware_presets",
    )
    op.drop_index(
        op.f("ix_firmware_presets_id"), table_name="firmware_presets"
    )
    op.drop_table("firmware_presets")

    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_pro")
