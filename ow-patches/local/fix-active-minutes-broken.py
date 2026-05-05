# patch_id:        fix-active-minutes-broken
# upstream_file:   backend/app/services/summaries_service.py
# upstream_symbol: SummariesService.get_activity_summaries
# retire_when:     ActivitySummary.active_minutes equals intensity_minutes.light + moderate + vigorous when HR-based intensity is available, OR upstream uses a different active-minutes signal that doesn't collapse to 1 for daily-total step providers.

"""Derive active_minutes from HR-based intensity bands instead of the
per-minute step bucket.

Upstream's active_minutes counts minutes where steps >= 30 in that minute.
For providers that emit a single daily-total step sample (Garmin Connect's
get_stats endpoint), that bucket has exactly one minute, so active_minutes
collapses to 1 regardless of actual activity. The HR-based intensity bands
already in get_activity_summaries are the correct signal.

This change is composed with fix-calories-total-mislabelled in apply.py —
both target SummariesService.get_activity_summaries and that file owns the
canonical replacement. Installing this patch alone simply ensures the
composed implementation is selected.
"""


def install() -> None:
    """No direct override — the active-minutes logic lives inside the
    get_activity_summaries replacement in fix-calories-total-mislabelled.
    apply.py orchestrates which composed implementation gets installed.
    """
    return
