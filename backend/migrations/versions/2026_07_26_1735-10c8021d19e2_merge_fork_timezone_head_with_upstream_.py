"""merge fork timezone head with upstream event_record_detail head

Revision ID: 10c8021d19e2
Revises: 7ac1c330f1b1, b2c3d4e5f6a1

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '10c8021d19e2'
down_revision: Union[str, None] = ('7ac1c330f1b1', 'b2c3d4e5f6a1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
