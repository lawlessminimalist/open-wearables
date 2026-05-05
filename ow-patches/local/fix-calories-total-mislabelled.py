# patch_id:        fix-calories-total-mislabelled
# upstream_file:   backend/app/services/providers/garmin/data_247.py, backend/app/services/providers/garmin_connect/data_247.py, backend/app/services/summaries_service.py
# upstream_symbol: Garmin247Data._build_dailies_samples + GarminConnect247Data.save_daily_stats_for_date + SummariesService.get_activity_summaries
# retire_when:     Garmin daily-stats normalization persists basal energy AND ActivitySummary.total_calories_kcal is null when basal is missing (not equal to active_calories_kcal).

"""Persist Garmin basal calories and stop returning a misleading
total_calories_kcal that's identical to active_calories_kcal.

Three coordinated changes:
  1. Garmin (OAuth Health API) — add ("bmr_calories", SeriesType.basal_energy)
     to _build_dailies_samples' series_mappings.
  2. Garmin Connect (credential-based) — add ("bmrKilocalories", SeriesType.basal_energy)
     to save_daily_stats_for_date's metric_map.
  3. SummariesService.get_activity_summaries — treat 0/None active or basal
     as "missing" and return total=null unless BOTH are present; surface
     basal_calories_kcal alongside active_calories_kcal.

The basal_calories_kcal field is added to ActivitySummary in source (structural
change, can't be runtime-toggled). When this patch is disabled, the field
remains in the schema but stays null.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.database import DbSession
from app.repositories.data_point_series_repository import (
    ActiveMinutesResult,
    IntensityMinutesResult,
)
from app.schemas.enums import SeriesType
from app.schemas.model_crud.activities import TimeSeriesSampleCreate
from app.schemas.responses.activity import (
    ActivitySummary,
    HeartRateStats,
    IntensityMinutes,
)
from app.schemas.utils import (
    PaginatedResponse,
    Pagination,
    SourceMetadata,
    TimeseriesMetadata,
)
from app.utils.exceptions import handle_exceptions
from app.utils.pagination import (
    decode_activity_cursor,
    encode_activity_cursor,
)
from app.utils.structured_logging import log_structured

# ---------------------------------------------------------------------------
# Garmin (OAuth) — replaces _build_dailies_samples
# ---------------------------------------------------------------------------


def garmin_build_dailies_samples(self, user_id: UUID, normalized_daily: dict) -> list[TimeSeriesSampleCreate]:
    """Build time series samples from normalized daily data, including basal energy."""
    samples: list[TimeSeriesSampleCreate] = []
    calendar_date = normalized_daily.get("calendar_date")
    start_ts = normalized_daily.get("start_time_seconds")

    if not calendar_date and not start_ts:
        return samples

    if start_ts:
        recorded_at = self._from_epoch_seconds(start_ts)
    elif calendar_date:
        try:
            recorded_at = datetime.strptime(calendar_date, "%Y-%m-%d").replace(hour=12, tzinfo=timezone.utc)
        except ValueError:
            return samples
    else:
        return samples

    zone_offset = normalized_daily.get("zone_offset")

    series_mappings: list[tuple[str, SeriesType]] = [
        ("steps", SeriesType.steps),
        ("active_calories", SeriesType.energy),
        ("bmr_calories", SeriesType.basal_energy),
        ("resting_heart_rate", SeriesType.resting_heart_rate),
        ("floors_climbed", SeriesType.flights_climbed),
        ("distance_meters", SeriesType.distance_walking_running),
    ]

    for field, series_type in series_mappings:
        value = normalized_daily.get(field)
        if value is not None:
            samples.append(
                TimeSeriesSampleCreate(
                    id=uuid4(),
                    user_id=user_id,
                    source=self.provider_name,
                    recorded_at=recorded_at,
                    zone_offset=zone_offset,
                    value=Decimal(str(value)),
                    series_type=series_type,
                    external_id=normalized_daily.get("garmin_summary_id"),
                )
            )

    hr_samples = normalized_daily.get("heart_rate_samples")
    if hr_samples and isinstance(hr_samples, dict):
        samples.extend(self._collect_heart_rate_samples(user_id, start_ts or 0, hr_samples, zone_offset))

    return samples


# ---------------------------------------------------------------------------
# Garmin Connect — replaces save_daily_stats_for_date
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
# SummariesService.get_activity_summaries — replaces upstream version
# ---------------------------------------------------------------------------


@handle_exceptions
def get_activity_summaries(
    self,
    db_session: DbSession,
    user_id: UUID,
    start_date: datetime,
    end_date: datetime,
    cursor: str | None,
    limit: int,
    sort_order: str = "asc",
) -> PaginatedResponse[ActivitySummary]:
    """Daily activity summaries with basal_calories_kcal and intensity-derived active_minutes.

    This implementation merges three behavioral fixes that share the same
    function — fix-calories-total-mislabelled, fix-active-minutes-broken,
    and (transparently) the source-correction implications. apply.py composes
    them: when any of the three is enabled, this is the implementation used.
    """
    from app.services.summaries_service import (
        ACTIVE_STEPS_THRESHOLD,
        METERS_PER_FLOOR,
    )

    self.logger.debug(f"Fetching activity summaries for user {user_id} from {start_date} to {end_date}")

    # Look up the user's stored timezone once and stamp it on every summary so
    # the frontend's display-tz selector knows which zone the buckets anchor
    # to. The composer in apply.py strips the field when fix-summary-timezone-echo
    # is disabled.
    user = self.user_repo.get(db_session, user_id)
    user_tz: str | None = getattr(user, "timezone", None) if user else None

    results = self.data_point_repo.get_daily_activity_aggregates(db_session, user_id, start_date, end_date)
    results = self._merge_archive_activity(db_session, user_id, start_date, end_date, results)
    results = self._filter_by_priority(db_session, user_id, results, date_key="activity_date")

    workout_aggregates = self.event_record_repo.get_daily_workout_aggregates(
        db_session, user_id, start_date, end_date
    )

    workout_lookup: dict[tuple, dict] = {}
    for wa in workout_aggregates:
        key = (wa["workout_date"], wa["source"], wa.get("device_model"))
        workout_lookup[key] = wa

    activity_minutes = self.data_point_repo.get_daily_active_minutes(
        db_session, user_id, start_date, end_date, active_threshold=ACTIVE_STEPS_THRESHOLD
    )

    activity_lookup: dict[tuple, ActiveMinutesResult] = {}
    for am in activity_minutes:
        key = (am["activity_date"], am["source"], am.get("device_model"))
        activity_lookup[key] = am

    max_hr = self._get_user_max_hr(db_session, user_id, start_date)
    hr_zones = self._get_hr_zone_thresholds(max_hr)
    intensity_minutes_data = self.data_point_repo.get_daily_intensity_minutes(
        db_session,
        user_id,
        start_date,
        end_date,
        light_min=hr_zones["light_min"],
        light_max=hr_zones["light_max"],
        moderate_max=hr_zones["moderate_max"],
        vigorous_max=hr_zones["vigorous_max"],
    )

    intensity_lookup: dict[tuple, IntensityMinutesResult] = {}
    for im in intensity_minutes_data:
        key = (im["activity_date"], im["source"], im.get("device_model"))
        intensity_lookup[key] = im

    if sort_order == "desc":
        results = list(reversed(results))

    if cursor:
        cursor_date, cursor_provider, cursor_device, direction = decode_activity_cursor(cursor)
        cursor_key = (cursor_date, cursor_provider, cursor_device or "")

        if direction == "prev":
            if sort_order == "desc":
                results = [
                    r for r in results
                    if (r["activity_date"], r["source"] or "", r.get("device_model") or "") > cursor_key
                ]
            else:
                results = [
                    r for r in results
                    if (r["activity_date"], r["source"] or "", r.get("device_model") or "") < cursor_key
                ]
            results = list(reversed(results))
        else:
            if sort_order == "desc":
                results = [
                    r for r in results
                    if (r["activity_date"], r["source"] or "", r.get("device_model") or "") < cursor_key
                ]
            else:
                results = [
                    r for r in results
                    if (r["activity_date"], r["source"] or "", r.get("device_model") or "") > cursor_key
                ]

    has_more = len(results) > limit
    if has_more:
        results = results[:limit]

    next_cursor: str | None = None
    previous_cursor: str | None = None

    if results:
        if has_more:
            last = results[-1]
            next_cursor = encode_activity_cursor(
                last["activity_date"], last["source"] or "unknown", last.get("device_model"), "next"
            )

        if cursor:
            first = results[0]
            previous_cursor = encode_activity_cursor(
                first["activity_date"], first["source"] or "unknown", first.get("device_model"), "prev"
            )

    data = []
    for result in results:
        result_key = (result["activity_date"], result["source"], result.get("device_model"))
        workout_data = workout_lookup.get(result_key, {})
        activity_data = activity_lookup.get(result_key, {})
        intensity_data = intensity_lookup.get(result_key, {})

        elevation_meters = workout_data.get("elevation_meters")

        flights_climbed = result.get("flights_climbed_sum")
        if flights_climbed is not None:
            floors_climbed = flights_climbed
        elif elevation_meters is not None and elevation_meters > 0:
            floors_climbed = int(elevation_meters / METERS_PER_FLOOR)
        else:
            floors_climbed = None

        ts_distance = result.get("distance_sum")
        total_distance = float(ts_distance) if ts_distance is not None else None

        hr_stats = None
        if result.get("hr_avg") is not None:
            hr_stats = HeartRateStats(
                avg_bpm=result.get("hr_avg"),
                max_bpm=result.get("hr_max"),
                min_bpm=result.get("hr_min"),
            )

        # --- fix-calories-total-mislabelled ---
        # Treat 0.0 as missing (the aggregator coerces nulls to 0) so total_calories_kcal
        # stays null when basal isn't reported by the source — instead of returning
        # active_cal + 0 and pretending it's TDEE.
        active_cal = result.get("active_energy_sum")
        basal_cal = result.get("basal_energy_sum")
        active_cal = active_cal if active_cal else None
        basal_cal = basal_cal if basal_cal else None
        total_cal = (active_cal + basal_cal) if (active_cal is not None and basal_cal is not None) else None

        # --- fix-active-minutes-broken ---
        sedentary_mins = activity_data.get("sedentary_minutes")
        intensity_mins = None
        active_mins: int | None = None
        if intensity_data:
            light = intensity_data.get("light_minutes", 0) or 0
            moderate = intensity_data.get("moderate_minutes", 0) or 0
            vigorous = intensity_data.get("vigorous_minutes", 0) or 0
            intensity_mins = IntensityMinutes(
                light=light,
                moderate=moderate,
                vigorous=vigorous,
            )
            active_mins = light + moderate + vigorous
        elif activity_data:
            active_mins = activity_data.get("active_minutes")

        steps = result.get("steps_sum")
        summary = ActivitySummary(
            date=result["activity_date"],
            source=SourceMetadata(provider=result["source"] or "unknown", device=result.get("device_model")),
            timezone=user_tz,
            steps=steps if steps is not None else None,
            distance_meters=total_distance,
            floors_climbed=floors_climbed,
            elevation_meters=elevation_meters,
            active_calories_kcal=active_cal,
            basal_calories_kcal=basal_cal,
            total_calories_kcal=total_cal,
            active_minutes=active_mins,
            sedentary_minutes=sedentary_mins,
            intensity_minutes=intensity_mins,
            heart_rate=hr_stats,
        )
        data.append(summary)

    return PaginatedResponse(
        data=data,
        pagination=Pagination(
            has_more=has_more,
            next_cursor=next_cursor,
            previous_cursor=previous_cursor,
        ),
        metadata=TimeseriesMetadata(
            sample_count=len(data),
            start_time=start_date,
            end_time=end_date,
        ),
    )


def install() -> None:
    """Apply all three Garmin/calories changes."""
    from app.services.providers.garmin.data_247 import Garmin247Data
    from app.services.providers.garmin_connect.data_247 import GarminConnect247Data
    from app.services.summaries_service import SummariesService

    Garmin247Data._build_dailies_samples = garmin_build_dailies_samples
    GarminConnect247Data.save_daily_stats_for_date = garmin_connect_save_daily_stats_for_date
    SummariesService.get_activity_summaries = get_activity_summaries
