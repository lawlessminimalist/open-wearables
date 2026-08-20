"""merge branches

Revision ID: d383e29e29c4
Revises: 9b3d4f7a8c21, d15dee848b33

"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "d383e29e29c4"
down_revision: Union[str, None] = ("9b3d4f7a8c21", "d15dee848b33")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
