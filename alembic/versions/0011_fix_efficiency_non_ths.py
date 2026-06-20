"""hotfix: null efficiency_j_per_th for non-TH/s device_models rows

Revision ID: 0011_fix_efficiency_non_ths
Revises: 0010_queue3_tax_pool_groundwork
Create Date: 2026-06-20 10:00:00.000000

``efficiency_j_per_th`` (J per TH/s) is only meaningful for TH/s devices.
For other hashrate units the figure is garbage and frequently astronomical
(e.g. Innosilicon A8 CryptoMaster = 2.19e9), overflowing NUMERIC(12,4) and
aborting the catalog import on PostgreSQL (502 in prod, app restart loop).

The catalog data file and ``seed_catalog`` are fixed at source, so a clean
``alembic upgrade head`` no longer inserts such rows. This migration is a
safety net for environments where rows were already (partially) persisted
before the fix: it NULLs ``efficiency_j_per_th`` wherever ``hashrate_unit``
is not TH/s. It is idempotent and safe to re-run.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0011_fix_efficiency_non_ths"
down_revision: Union[str, None] = "0010_queue3_tax_pool_groundwork"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Idempotent: NULL efficiency only for non-TH/s rows that still carry a
    # value. Case-insensitive on the unit; rows with NULL unit are treated as
    # TH/s (the column default) and left untouched.
    op.execute(
        sa.text(
            """
            UPDATE device_models
               SET efficiency_j_per_th = NULL
             WHERE efficiency_j_per_th IS NOT NULL
               AND hashrate_unit IS NOT NULL
               AND lower(trim(hashrate_unit)) <> 'th/s'
            """
        )
    )


def downgrade() -> None:
    # Data-only correction; the discarded values were invalid by definition,
    # so there is nothing meaningful to restore.
    pass
