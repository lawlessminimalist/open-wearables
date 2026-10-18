"""merge fork head with upstream 0.10.0

Revision ID: 8f7f993001f6
Revises: f3a9c1d2e4b5, c4e7a1d93b52

Unifies the two heads left by the 2026-10-19 upstream reconcile (6c2f28a3..d18304f8):
  - f3a9c1d2e4b5 : the fork's rename of the stored `energy` series definition
                   (2026-09-20), itself on top of merge head e2c0dda6e505
  - c4e7a1d93b52 : upstream #1886, user.is_seed (its chain runs through the
                   telemetry_state, extended device types, meal details,
                   refresh-token origin, data-source stable identity,
                   data_migration and data_type_coverage revisions)

Neither side depends on the other, so this is an empty merge revision, same shape
as e2c0dda6e505 before it. A merge revision (rather than re-pointing f3a9c1d2e4b5)
is required because f3a9c1d2e4b5 is already recorded on every fork database.
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "8f7f993001f6"
down_revision: Union[str, None] = ("f3a9c1d2e4b5", "c4e7a1d93b52")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
