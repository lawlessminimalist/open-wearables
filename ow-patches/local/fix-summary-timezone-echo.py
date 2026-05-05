# patch_id:        fix-summary-timezone-echo
# upstream_file:   backend/app/services/summaries_service.py
# upstream_symbol: SummariesService.get_activity_summaries
# retire_when:     ActivitySummary response includes the user's IANA timezone (or upstream provides an equivalent way for the frontend to know what timezone the daily-bucket dates are anchored to).

"""Echo the user's IANA timezone on ActivitySummary responses.

Sleep summaries already carry `timezone`/`start_time_local`/`end_time_local`
(via fix-sleep-timezone). Activity summaries don't, but the frontend needs the
same hint so its display-timezone selector knows which IANA zone the daily
buckets are anchored to (after fix-activity-summary-utc-bucketing they bucket
by `user.timezone`, not UTC). This patch reads `user.timezone` once per
request and stamps it on every returned ActivitySummary.

Composed inside fix-calories-total-mislabelled's get_activity_summaries
replacement — apply.py wraps the base implementation so this patch's flag
toggles only the timezone-population, not the calories/active-minutes logic.
"""


def install() -> None:
    """No direct override — composed inside _compose_activity_summaries in apply.py.

    The composed wrapper checks PATCHES_ENABLED["fix-summary-timezone-echo"]
    and either populates `summary.timezone = user.timezone` or leaves it None
    so disabling this patch returns upstream-equivalent behaviour (the field
    exists in the schema but stays None).
    """
    return
