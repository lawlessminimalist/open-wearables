"""merge user_timezone fork head with upstream

Revision ID: 7ac1c330f1b1
Revises: 264b79d7c541, d383e29e29c4

Unifies the two diverged heads into one:
  - 264b79d7c541 : tip of upstream's migration chain (widen data_source.source)
  - d383e29e29c4 : the prior fork↔upstream merge (user_timezone + d15dee848b33),
                   which already carries 9b3d4f7a8c21 in its ancestry.
"""

from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "7ac1c330f1b1"
down_revision: Union[str, None] = ("264b79d7c541", "d383e29e29c4")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
