"""rename the stored `energy` series definition to `active_energy`

Upstream b6c0e2b2 (#1643) renamed SeriesType.energy to active_energy and moved
the seed list to the new code, but shipped no migration for the
series_type_definition row that existing databases already hold. The enum keeps
a lookup alias (SeriesType._missing_) so id 81 still resolves, but the stored
`code` column is returned verbatim by get_user_series_type_counts and the
archival counts, so deployed databases reported "energy" where the enum, the
seed data and the frontend now say "active_energy". Upstream says the alias
goes at 1.0.

Scoped to the one row and guarded on the old value, so it is a no-op on a fresh
database seeded with the new code and safe to re-run.

Revision ID: f3a9c1d2e4b5
Revises: e2c0dda6e505
Create Date: 2026-09-20 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f3a9c1d2e4b5"
down_revision: str | None = "e2c0dda6e505"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENERGY_SERIES_ID = 81


def upgrade() -> None:
    op.execute(
        f"UPDATE series_type_definition SET code = 'active_energy' WHERE id = {ENERGY_SERIES_ID} AND code = 'energy'"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE series_type_definition SET code = 'energy' WHERE id = {ENERGY_SERIES_ID} AND code = 'active_energy'"
    )
