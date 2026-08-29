"""merge fork provider-mislabel head with upstream health_score fk rename

Revision ID: e7f8a9b0c1d2
Revises: c4d5e6f7a8b9, dc5ac28c4b94

Unifies the two diverged heads left by the 2026-08-29 upstream reconcile:
  - c4d5e6f7a8b9 : fork data repair for fix-provider-prefix-shadowing
                   (rewrites data_source rows that stored provider='garmin'
                   for garmin_connect sources)
  - dc5ac28c4b94 : upstream #1462, renames health_score.sleep_record_id to
                   event_record_id and reindexes uq_health_score_sleep_record

The two touch different tables and neither depends on the other, so this is an
empty merge revision — same shape as 7ac1c330f1b1 and 10c8021d19e2 before it.
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, None] = ("c4d5e6f7a8b9", "dc5ac28c4b94")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
