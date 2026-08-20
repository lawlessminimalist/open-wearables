"""fix garmin_connect data_source rows mislabelled as provider 'garmin'

ProviderName.from_source_string substring-matched enum values in declaration
order, and GARMIN ("garmin") is declared before GARMIN_CONNECT
("garmin_connect"). Since "garmin" is a substring of "garmin_connect", every
lookup returned GARMIN and GARMIN_CONNECT was unreachable.

That value is persisted: infer_provider_from_source() delegates to it and runs on
the write path in event_record_repository (x2) and data_point_series_repository
(x2), so every data_source row ever created for the garmin_connect provider
stored provider='garmin'.

Consequences of leaving the historical rows alone: the summaries endpoints report
provider "garmin" against source "garmin_connect", and provider-priority
resolution ranks garmin_connect in GARMIN's slot — so the wrong source can win a
de-duplication against another provider covering the same night.

The code path is fixed by the ow-patch fix-provider-prefix-shadowing; this
migration repairs the rows already written.

Scoping: only rows whose `source` actually identifies garmin_connect are
touched. Rows from the official `garmin` provider (OAuth/webhook) legitimately
have provider='garmin' and are left alone. The separator normalisation mirrors
the patched matcher so "Garmin Connect" and "garmin-connect" are caught too.

Revision ID: c4d5e6f7a8b9
Revises: 10c8021d19e2
Create Date: 2026-08-20 06:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "10c8021d19e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Mirrors the normalisation in ow-patches/local/fix-provider-prefix-shadowing.py
# strpos() rather than LIKE: op.execute passes the string to the driver, where a
# literal '%' is ambiguous with paramstyle placeholders and needs doubling. Using
# strpos avoids wildcards entirely, so there is nothing to escape.
_SOURCE_IS_GARMIN_CONNECT = (
    "strpos(lower(replace(replace(coalesce(source, ''), ' ', '_'), '-', '_')), 'garmin_connect') > 0"
)

# uq_data_source_identity is UNIQUE on (user_id, provider, COALESCE(device_model,''),
# COALESCE(source,'')). Flipping provider could collide with a row already carrying
# the corrected value — impossible before the code fix, possible if this migration
# runs after the fixed code has written new rows. Skip those; the correct row
# already exists and the stale duplicate is left for manual review rather than
# risking an IntegrityError that aborts the whole migration.
_NO_CONFLICT = """
    NOT EXISTS (
        SELECT 1 FROM data_source d2
        WHERE d2.user_id = data_source.user_id
          AND d2.provider = '{target}'
          AND COALESCE(d2.device_model, '') = COALESCE(data_source.device_model, '')
          AND COALESCE(d2.source, '') = COALESCE(data_source.source, '')
    )
"""


def upgrade() -> None:
    op.execute(
        f"""
        UPDATE data_source
        SET provider = 'garmin_connect'
        WHERE provider = 'garmin'
          AND {_SOURCE_IS_GARMIN_CONNECT}
          AND {_NO_CONFLICT.format(target="garmin_connect")}
        """
    )


def downgrade() -> None:
    op.execute(
        f"""
        UPDATE data_source
        SET provider = 'garmin'
        WHERE provider = 'garmin_connect'
          AND {_SOURCE_IS_GARMIN_CONNECT}
          AND {_NO_CONFLICT.format(target="garmin")}
        """
    )
