# patch_id:        fix-calories-total-mislabelled
# upstream_file:   backend/app/services/providers/garmin/coverage.py, backend/app/services/providers/garmin_connect/data_247.py, backend/app/services/summaries_service.py
# upstream_symbol: DAILIES_SERIES (basal mapping) + GarminConnect247Data.save_daily_stats_for_date + SummariesService.get_activity_summaries (decorated)
# retire_when:     Garmin daily-stats normalization persists basal energy AND ActivitySummary.total_calories_kcal is null when basal is missing (not equal to active_calories_kcal) AND ActivitySummary.basal_calories_kcal is populated.

"""Persist Garmin basal calories and stop returning a misleading
total_calories_kcal that's identical to active_calories_kcal.

Three coordinated changes, rebased onto upstream after the 2026-07 merge:

  1. Garmin (OAuth Health API) — STRUCTURAL: add
     ("bmr_calories", SeriesType.basal_energy) to
     garmin/coverage.py::DAILIES_SERIES. Upstream's own _build_dailies_samples
     iterates DAILIES_SERIES and stamps is_daily_total via daily_total_flag, so
     we inherit #1232's daily-total handling instead of shadowing it. (The
     normalizer already emits a "bmr_calories" key — data_247.py:~495.) This
     lives in source, not here — see PATCHES.md.

  2. Garmin Connect (credential-based, fork-only provider) — override
     save_daily_stats_for_date to also persist bmrKilocalories as
     SeriesType.basal_energy, flagging daily-total series via daily_total_flag
     so the post-#1232 aggregator de-duplicates correctly.

  3. SummariesService.get_activity_summaries — DECORATOR (composed in apply.py):
     post-process each summary so basal_calories_kcal is surfaced. (Until
     2026-10-19 it also nulled total_calories_kcal unless BOTH active and basal
     were present; upstream #1801 does that itself now.) We no longer
     wholesale-replace the method (that shadowed #1242's active_time_minutes) —
     we reconstruct basal = total - active from upstream's own output.

The basal_calories_kcal / timezone fields on ActivitySummary are added to the
schema in source (structural). When this patch is disabled, upstream's behaviour
returns (total per upstream #1801; basal_calories_kcal stays null).
"""


from app.schemas.responses.activity import ActivitySummary

# ---------------------------------------------------------------------------
# SummariesService.get_activity_summaries — DECORATOR (post-processor)
# ---------------------------------------------------------------------------


def apply_calories_fix(summary: ActivitySummary) -> None:
    """Surface basal_calories_kcal on one upstream ActivitySummary, in place.

    Since upstream #1801 (c53a1ac2, 0.10.0), get_activity_summaries already
    reports total_calories_kcal honestly: the provider's own total when it has
    one, else active + basal when both are present, else null. The half of this
    patch that nulled an active-only total is therefore upstream behaviour now
    and was dropped on 2026-10-19; recomputing total here would also null a real
    provider-reported total (Whoop's cycle energy) whenever active is missing.

    What upstream still never does is populate the fork-only
    basal_calories_kcal field, so recover it from upstream's own output as
    total - active when both are present. 0 or less is treated as missing (a
    real day's BMR is never 0). Pure function of upstream's response, so it
    inherits any upstream change to get_activity_summaries.
    """
    active = summary.active_calories_kcal
    total = summary.total_calories_kcal
    basal = (total - active) if (active is not None and total is not None) else None
    summary.basal_calories_kcal = basal if basal is not None and basal > 0 else None


def install() -> None:
    """No-op: this patch is decorator-only now.

    The Garmin OAuth basal persistence is structural — ("bmr_calories",
    SeriesType.basal_energy) added to garmin/coverage.py::DAILIES_SERIES, which
    upstream's own _build_dailies_samples then persists (no shadow). The
    get_activity_summaries calorie post-processing is applied as a decorator by
    apply.py::_compose_activity_summaries (apply_calories_fix).

    This used to ALSO replace GarminConnect247Data.save_daily_stats_for_date to
    persist bmrKilocalories. That override is REMOVED: garmin_connect is a
    fork-only provider whose source we own outright, so the fix belongs in
    data_247.py directly. Keeping it as a runtime patch shadowed later edits to
    the very file it patched — new fields added to the real
    save_daily_stats_for_date (floors ascended, intensity minutes) silently
    never ran. Patching your own source buys every shadowing hazard and none of
    the upstream-conflict benefit.
    """
