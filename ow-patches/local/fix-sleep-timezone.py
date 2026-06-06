# patch_id:        fix-sleep-timezone
# upstream_file:   backend/app/models/user.py, backend/app/schemas/responses/activity/summaries.py, backend/app/services/summaries_service.py, backend/migrations/versions/2026_05_05_1200-9b3d4f7a8c21_user_timezone.py
# upstream_symbol: User.timezone (column) + SleepSummary (timezone/start_time_local/end_time_local fields) + SummariesService.get_sleep_summaries (population)
# retire_when:     UserRead response includes a timezone field AND sleep summaries surface a per-record local datetime or a top-level user.timezone the consumer can apply.

"""User timezone + per-record local datetimes on sleep summaries.

This patch is split between toggleable runtime behavior and structural
("not toggleable from apply.py") changes.

Toggleable (this file):
  - Population of timezone, start_time_local, end_time_local on SleepSummary.
    Since upstream (commit 09b7b0a) now owns the body of
    SummariesService.get_sleep_summaries (it populates the HRV/RR/SpO2 metrics
    that fix-hrv-nightly-aggregate used to add), this patch no longer replaces
    that method. Instead apply.py wraps upstream's method and calls
    apply_timezone_fields() on each summary in the response — see
    _compose_sleep_summaries() in apply.py.

Structural (left in source files):
  - User.timezone column (backend/app/models/user.py)
  - 9b3d4f7a8c21_user_timezone migration
  - timezone field on UserRead/UserCreate/UserUpdate
  - timezone, start_time_local, end_time_local fields on SleepSummary

Disabling this patch via PATCHES_ENABLED leaves the columns/fields defined
but causes their values to come back as None in API responses.
"""

from datetime import datetime

from app.schemas.responses.activity import SleepSummary


def _to_local(dt: datetime | None, tz_name: str | None) -> datetime | None:
    """Render a UTC datetime in the given IANA timezone, or None on bad input."""
    if dt is None or not tz_name:
        return None
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        return dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        return None


def apply_timezone_fields(summary: SleepSummary, user_tz: str | None) -> None:
    """Decorate one SleepSummary with the user's timezone + local datetimes.

    Operates in place on the summary returned by upstream's get_sleep_summaries.
    """
    summary.timezone = user_tz
    summary.start_time_local = _to_local(summary.start_time, user_tz)
    summary.end_time_local = _to_local(summary.end_time, user_tz)


def install() -> None:
    """No direct monkey-patch — apply.py's _compose_sleep_summaries() wraps
    upstream's get_sleep_summaries and calls apply_timezone_fields() when this
    patch is enabled.
    """
    return
