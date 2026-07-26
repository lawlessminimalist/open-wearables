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
     post-process each summary so total_calories_kcal is null unless BOTH active
     and basal are present, and basal_calories_kcal is surfaced. We no longer
     wholesale-replace the method (that shadowed #1242's active_time_minutes) —
     we reconstruct basal = total - active from upstream's own output.

The basal_calories_kcal / timezone fields on ActivitySummary are added to the
schema in source (structural). When this patch is disabled, upstream's behaviour
returns (total = active + (basal or 0); basal_calories_kcal stays null).
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.database import DbSession
from app.schemas.enums import SeriesType, daily_total_flag
from app.schemas.model_crud.activities import TimeSeriesSampleCreate
from app.schemas.responses.activity import ActivitySummary
from app.utils.structured_logging import log_structured

# ---------------------------------------------------------------------------
# Garmin Connect (fork-only provider) — replaces save_daily_stats_for_date
# ---------------------------------------------------------------------------


def garmin_connect_save_daily_stats_for_date(self, db: DbSession, user_id: UUID, cdate: date) -> int:
    """Fetch and save daily stats (steps, energy, basal energy, distance) for one date."""
    from app.services.timeseries_service import timeseries_service  # noqa: PLC0415

    raw = self.client.get_stats(cdate)
    if not raw:
        return 0

    midnight = datetime(cdate.year, cdate.month, cdate.day, tzinfo=timezone.utc)
    samples: list[TimeSeriesSampleCreate] = []

    metric_map: list[tuple[str, SeriesType]] = [
        ("totalSteps", SeriesType.steps),
        ("activeKilocalories", SeriesType.energy),
        ("bmrKilocalories", SeriesType.basal_energy),
        ("totalDistanceMeters", SeriesType.distance_walking_running),
        ("averageStressLevel", SeriesType.garmin_stress_level),
        ("restingHeartRate", SeriesType.resting_heart_rate),
    ]

    for field, series_type in metric_map:
        value = raw.get(field)
        if value is None:
            continue
        try:
            samples.append(
                TimeSeriesSampleCreate(
                    id=uuid4(),
                    user_id=user_id,
                    source=self.provider_name,
                    recorded_at=midnight,
                    value=Decimal(str(value)),
                    series_type=series_type,
                    # These are provider-reported daily totals (one sample/day);
                    # flag them so the post-#1232 prefer_daily_sum aggregator
                    # de-duplicates daily-total vs intraday correctly.
                    is_daily_total=daily_total_flag(series_type, is_daily=True),
                )
            )
        except Exception as exc:
            log_structured(
                self.logger,
                "warning",
                "Failed to build daily stat sample",
                action="garmin_connect_daily_stat_error",
                field=field,
                error=str(exc),
                user_id=str(user_id),
            )

    if samples:
        timeseries_service.bulk_create_samples(db, samples)
    return len(samples)


# ---------------------------------------------------------------------------
# SummariesService.get_activity_summaries — DECORATOR (post-processor)
# ---------------------------------------------------------------------------


def apply_calories_fix(summary: ActivitySummary) -> None:
    """Rewrite one upstream ActivitySummary to honest calorie fields, in place.

    Upstream sets `active_calories_kcal = active_energy_sum` and
    `total_calories_kcal = (active or 0) + (basal or 0)`, and never populates
    `basal_calories_kcal`. From that output we recover
    `basal = total - active`, treat 0 as missing (a real day's BMR is never 0),
    surface `basal_calories_kcal`, and null out `total_calories_kcal` unless
    BOTH energies are present — so the field name (active + basal) stays honest
    instead of silently meaning active-only.

    This is a pure function of upstream's response, so it inherits any upstream
    change to get_activity_summaries (e.g. #1242 active_time_minutes) rather
    than shadowing it.
    """
    active = summary.active_calories_kcal
    total = summary.total_calories_kcal
    basal = None if total is None else (total - (active or 0.0))

    active = active if active else None
    basal = basal if basal else None
    total = (active + basal) if (active is not None and basal is not None) else None

    summary.active_calories_kcal = active
    summary.basal_calories_kcal = basal
    summary.total_calories_kcal = total


def install() -> None:
    """Install the Garmin Connect daily-stats override (persists basal energy).

    The Garmin OAuth basal persistence is handled structurally by adding
    ("bmr_calories", SeriesType.basal_energy) to garmin/coverage.py::DAILIES_SERIES
    — upstream's own _build_dailies_samples then persists it (no shadow). The
    get_activity_summaries calorie post-processing is applied as a decorator by
    apply.py::_compose_activity_summaries (apply_calories_fix), not here.
    """
    from app.services.providers.garmin_connect.data_247 import GarminConnect247Data

    GarminConnect247Data.save_daily_stats_for_date = garmin_connect_save_daily_stats_for_date
