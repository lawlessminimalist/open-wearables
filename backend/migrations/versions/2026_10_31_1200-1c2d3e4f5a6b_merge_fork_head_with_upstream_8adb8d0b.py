"""merge fork head with upstream 8adb8d0b

Revision ID: 1c2d3e4f5a6b
Revises: 8f7f993001f6, 9ffc050235bd

Unifies the two heads left by the 2026-10-31 upstream reconcile (d18304f8..8adb8d0b):
  - 8f7f993001f6 : the fork's merge head from the 2026-10-19 reconcile
  - 9ffc050235bd : upstream #1877, health_score unique key per source (its chain
                   runs through the aggregates series_sample/rollup revisions,
                   #1930..#1941)

Neither side depends on the other, so this is an empty merge revision, same shape
as 8f7f993001f6 before it. A merge revision (rather than re-pointing either head)
is required because 8f7f993001f6 is already recorded on every fork database.
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "1c2d3e4f5a6b"
down_revision: Union[str, None] = ("8f7f993001f6", "9ffc050235bd")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
