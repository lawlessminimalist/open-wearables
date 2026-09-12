"""merge fork head with upstream 0.8.0 (hashed API keys)

Revision ID: e2c0dda6e505
Revises: e7f8a9b0c1d2, a7c3e9f1b2d4

Unifies the two heads left by the 2026-09-13 upstream reconcile (f766b5a0..53de57ca):
  - e7f8a9b0c1d2 : the fork's previous merge head (2026-08-29 reconcile)
  - a7c3e9f1b2d4 : upstream #1592, stores api_key.key_hash / key_prefix and
                   drops the raw key (its own chain runs through cf76dead11f5,
                   the SyncRun table from #1448)

Neither side depends on the other, so this is an empty merge revision — same
shape as 7ac1c330f1b1, 10c8021d19e2 and e7f8a9b0c1d2 before it. A merge
revision (rather than re-pointing e7f8a9b0c1d2) is required because e7f8a9b0c1d2
is already recorded on every fork database; scripts/check_migrations.py rejects
re-pointing a migration that exists on the base branch for exactly that reason.
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "e2c0dda6e505"
down_revision: Union[str, None] = ("e7f8a9b0c1d2", "a7c3e9f1b2d4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
