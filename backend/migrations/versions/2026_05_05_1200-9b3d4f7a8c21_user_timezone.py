"""user timezone

Revision ID: 9b3d4f7a8c21
Revises: 4bd01c907050

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b3d4f7a8c21"
down_revision: Union[str, None] = "4bd01c907050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user", sa.Column("timezone", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "timezone")
