# patch_id:        fix-sleep-timezone
# upstream_file:   backend/app/models/user.py, backend/app/schemas/responses/activity/summaries.py, backend/app/services/summaries_service.py, backend/migrations/versions/2026_05_05_1200-9b3d4f7a8c21_user_timezone.py
# upstream_symbol: User.timezone (column) + SleepSummary (timezone/start_time_local/end_time_local fields) + SummariesService.get_sleep_summaries (population)
# retire_when:     UserRead response includes a timezone field AND sleep summaries surface a per-record local datetime or a top-level user.timezone the consumer can apply.

"""User timezone + per-record local datetimes on sleep summaries.

This patch is split between toggleable runtime behavior and structural
("not toggleable from apply.py") changes.

Toggleable (this file):
  - Population of timezone, start_time_local, end_time_local on SleepSummary
    happens inside SummariesService.get_sleep_summaries, which is replaced by
    fix-hrv-nightly-aggregate. apply.py composes them so disabling
    fix-sleep-timezone alone keeps the other patch's behavior but suppresses
    timezone population (see compose() in apply.py).

Structural (left in source files):
  - User.timezone column (backend/app/models/user.py)
  - 9b3d4f7a8c21_user_timezone migration
  - timezone field on UserRead/UserCreate/UserUpdate
  - timezone, start_time_local, end_time_local fields on SleepSummary

Disabling this patch via PATCHES_ENABLED leaves the columns/fields defined
but causes their values to come back as None in API responses.
"""


def get_sleep_summaries_without_timezone(self, *args, **kwargs):  # noqa: ANN001, ANN201
    """Replacement that runs the (already-patched) sleep summaries function with
    the user's timezone forcibly hidden — used when only fix-sleep-timezone is
    disabled but fix-hrv-nightly-aggregate is enabled.

    Implemented by calling the composed implementation with a context flag.
    apply.py wires this in only when needed.
    """
    raise NotImplementedError(
        "fix-sleep-timezone is composed into fix-hrv-nightly-aggregate's "
        "get_sleep_summaries. Disabling timezone alone is handled by apply.py "
        "via the _SUPPRESS_TIMEZONE module flag — no override required here."
    )


# Sentinel checked by the composed get_sleep_summaries (in
# fix-hrv-nightly-aggregate.py + apply.py composer). When True, the patched
# function returns None for timezone/start_time_local/end_time_local even when
# the user has a timezone set.
SUPPRESS_TIMEZONE = False


def install() -> None:
    """No direct monkey-patch — the timezone-population logic lives inside the
    sleep-summaries replacement installed by fix-hrv-nightly-aggregate. apply.py
    handles the on/off toggle by mutating SUPPRESS_TIMEZONE on this module.
    """
    return
