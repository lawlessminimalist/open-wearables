"""merge user_timezone fork head with upstream

Revision ID: 7ac1c330f1b1
Revises: 9b3d4f7a8c21, 264b79d7c541

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "7ac1c330f1b1"
down_revision: Union[str, None] = ("9b3d4f7a8c21", "264b79d7c541")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
