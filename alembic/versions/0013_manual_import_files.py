"""manual_import_files: neutral groundwork for Excel earnings import (Queue 4)

Revision ID: 0013_manual_import_files
Revises: 0012_calculation_runs
Create Date: 2026-06-20 14:00:00.000000

Records files a user uploads for the future manual Excel/CSV earnings import
(see CALC_SPEC §8.4). Schema + FK only -- no parsing/normalization logic yet.

There are no NUMERIC columns here, so there is nothing for PostgreSQL's NUMERIC
precision enforcement to overflow (the SQLite test DB does not enforce; see the
prod efficiency overflow incident fixed in 0011). The normalized destination
``pool_earnings.source`` is a plain String with no CHECK/enum, so the future
value ``'manual_xlsx'`` is already permitted -- no constraint change required.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0013_manual_import_files"
down_revision: Union[str, None] = "0012_calculation_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "manual_import_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=True),
        sa.Column(
            "status", sa.String(), server_default="pending", nullable=False
        ),
        sa.Column("rows_parsed", sa.Integer(), nullable=True),
        sa.Column("error_log", sa.Text(), nullable=True),
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
        op.f("ix_manual_import_files_id"),
        "manual_import_files",
        ["id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_manual_import_files_user_id"),
        "manual_import_files",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_manual_import_files_user_id"), table_name="manual_import_files"
    )
    op.drop_index(
        op.f("ix_manual_import_files_id"), table_name="manual_import_files"
    )
    op.drop_table("manual_import_files")
