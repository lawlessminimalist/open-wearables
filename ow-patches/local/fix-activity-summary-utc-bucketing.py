# patch_id:        fix-activity-summary-utc-bucketing
# upstream_file:   backend/app/repositories/data_point_series_repository.py
# upstream_symbol: DataPointSeriesRepository.get_daily_activity_aggregates + .get_daily_active_minutes + .get_daily_intensity_minutes
# retire_when:     get_daily_activity_aggregates groups by the user's local date (using user.timezone or equivalent) instead of UTC `cast(recorded_at, Date)`. Marker: presence of `_local_date_bucket_expr` (or any equivalent timezone() / AT TIME ZONE / ZoneInfo bucketing) in DataPointSeriesRepository.

"""Bucket daily activity / active-minutes / intensity-minutes aggregates by
the user's local date instead of UTC.

Bug
---
Upstream's three daily aggregator queries all do:

    cast(self.model.recorded_at, Date).label("activity_date")
    ...
    .group_by(cast(self.model.recorded_at, Date), …)

That's a UTC-date cast. For users east or west of UTC, workouts that cross a
UTC midnight get split across two day-buckets in the API. Concrete example:

    User in Brisbane (UTC+10) goes for a trail run on the morning of Sun May 3
      → samples timestamps span 21:14 UTC (Sat) to 23:42 UTC (Sat)
      → these all bucket to UTC date 2026-05-02
    The "Sun May 3" card in the UI is actually the UTC May 3 bucket
      → contains ZERO samples from the run, just post-run + early-morning May 3
      → avg HR shows ~81 instead of ~161, max shows 128 instead of 186

Fix
---
Look up `user.timezone` (the IANA column added by fix-sleep-timezone) once at
the start of the query and use it as the timezone for the bucketing expression:

    cast(func.timezone(<user_tz>, recorded_at), Date)   ≡   (recorded_at AT TIME ZONE <tz>)::date

When `user.timezone` is NULL (i.e. the user hasn't set a timezone yet), fall
back to UTC, which is exactly upstream behavior — so disabling this patch
leaves the system in upstream-equivalent state.

Caveat
------
This patches only the daily-aggregation step. The `Garmin Connect`
save_daily_stats_for_date stamps daily totals at `datetime(year, month, day,
tzinfo=timezone.utc)` (UTC midnight of the local cdate). For timezones AHEAD
of UTC (Brisbane, Tokyo, etc.) UTC midnight still falls inside the same local
day, so the local-date cast resolves correctly. For timezones BEHIND UTC
(Americas), UTC midnight resolves to the prior local day — daily totals will
land one local day early. A follow-up patch should stamp those rows at the
local-midnight UTC instant. Not in scope here; called out so it isn't lost.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import Date, asc, case, cast, func, literal, literal_column

from app.database import DbSession
from app.models import DataSource
from app.repositories.data_point_series_repository import (
    ActiveMinutesResult,
    IntensityMinutesResult,
)
from app.schemas.enums import SeriesType, get_series_type_id


def _resolve_user_timezone(db_session: DbSession, user_id: UUID) -> str:
    """Return the user's IANA timezone or `'UTC'` so the SQL falls back to
    upstream UTC bucketing when the user hasn't set a timezone yet.
    """
    from app.models import User  # noqa: PLC0415 — keep coupling local to the patch

    tz = db_session.query(User.timezone).filter(User.id == user_id).scalar()
    return tz or "UTC"


def _local_date_bucket_expr(model, user_tz: str):
    """SQLAlchemy expression for `(recorded_at AT TIME ZONE <tz>)::date`."""
    return cast(func.timezone(literal(user_tz), model.recorded_at), Date)


# ---------------------------------------------------------------------------
# get_daily_activity_aggregates — primary surface that backs the broken UI card
# ---------------------------------------------------------------------------


def get_daily_activity_aggregates(
    self,
    db_session: DbSession,
    user_id: UUID,
    start_date: datetime,
    end_date: datetime,
):  # mirrors the upstream return type ActivityAggregateResult list
    """Per-(local-date, source, device) activity aggregates."""
    user_tz = _resolve_user_timezone(db_session, user_id)
    local_date = _local_date_bucket_expr(self.model, user_tz)

    steps_id = get_series_type_id(SeriesType.steps)
    energy_id = get_series_type_id(SeriesType.energy)
    basal_energy_id = get_series_type_id(SeriesType.basal_energy)
    hr_id = get_series_type_id(SeriesType.heart_rate)
    distance_id = get_series_type_id(SeriesType.distance_walking_running)
    flights_id = get_series_type_id(SeriesType.flights_climbed)

    results = (
        db_session.query(
            local_date.label("activity_date"),
            DataSource.source.label("source"),
            DataSource.device_model.label("device_model"),
            func.sum(case((self.model.series_type_definition_id == steps_id, self.model.value), else_=0)).label(
                "steps_sum"
            ),
            func.sum(case((self.model.series_type_definition_id == energy_id, self.model.value), else_=0)).label(
                "active_energy_sum"
            ),
            func.sum(
                case((self.model.series_type_definition_id == basal_energy_id, self.model.value), else_=0)
            ).label("basal_energy_sum"),
            func.avg(case((self.model.series_type_definition_id == hr_id, self.model.value), else_=None)).label(
                "hr_avg"
            ),
            func.max(case((self.model.series_type_definition_id == hr_id, self.model.value), else_=None)).label(
                "hr_max"
            ),
            func.min(case((self.model.series_type_definition_id == hr_id, self.model.value), else_=None)).label(
                "hr_min"
            ),
            func.sum(case((self.model.series_type_definition_id == distance_id, self.model.value))).label(
                "distance_sum"
            ),
            func.sum(case((self.model.series_type_definition_id == flights_id, self.model.value))).label(
                "flights_climbed_sum"
            ),
        )
        .join(DataSource, self.model.data_source_id == DataSource.id)
        .filter(
            DataSource.user_id == user_id,
            self.model.recorded_at >= start_date,
            self.model.recorded_at < end_date,
            self.model.series_type_definition_id.in_(
                [steps_id, energy_id, basal_energy_id, hr_id, distance_id, flights_id]
            ),
        )
        .group_by(local_date, DataSource.source, DataSource.device_model)
        .order_by(asc(local_date))
        .all()
    )

    aggregates: list[dict] = []
    for row in results:
        aggregates.append(
            {
                "activity_date": row.activity_date,
                "source": row.source,
                "device_model": row.device_model,
                "steps_sum": int(row.steps_sum) if row.steps_sum else 0,
                "active_energy_sum": float(row.active_energy_sum) if row.active_energy_sum else 0.0,
                "basal_energy_sum": float(row.basal_energy_sum) if row.basal_energy_sum else 0.0,
                "hr_avg": int(round(float(row.hr_avg))) if row.hr_avg is not None else None,
                "hr_max": int(row.hr_max) if row.hr_max is not None else None,
                "hr_min": int(row.hr_min) if row.hr_min is not None else None,
                "distance_sum": float(row.distance_sum) if row.distance_sum is not None else None,
                "flights_climbed_sum": int(row.flights_climbed_sum) if row.flights_climbed_sum is not None else None,
            }
        )
    return aggregates


# ---------------------------------------------------------------------------
# get_daily_active_minutes — same UTC-bucketing bug for the step-bucket query
# ---------------------------------------------------------------------------


def get_daily_active_minutes(
    self,
    db_session: DbSession,
    user_id: UUID,
    start_date: datetime,
    end_date: datetime,
    active_threshold: int = 30,
) -> list[ActiveMinutesResult]:
    """Per-(local-date, source, device) active/sedentary minutes from steps."""
    user_tz = _resolve_user_timezone(db_session, user_id)
    local_date = _local_date_bucket_expr(self.model, user_tz)

    steps_id = get_series_type_id(SeriesType.steps)
    minute_trunc = func.date_trunc(literal_column("'minute'"), self.model.recorded_at)

    minute_bucket = (
        db_session.query(
            local_date.label("activity_date"),
            DataSource.source,
            DataSource.device_model,
            minute_trunc.label("minute_bucket"),
            func.sum(self.model.value).label("steps_in_minute"),
        )
        .join(DataSource, self.model.data_source_id == DataSource.id)
        .filter(
            DataSource.user_id == user_id,
            self.model.recorded_at >= start_date,
            self.model.recorded_at < end_date,
            self.model.series_type_definition_id == steps_id,
        )
        .group_by(local_date, DataSource.source, DataSource.device_model, minute_trunc)
        .subquery()
    )

    results = (
        db_session.query(
            minute_bucket.c.activity_date,
            minute_bucket.c.source,
            minute_bucket.c.device_model,
            func.sum(case((minute_bucket.c.steps_in_minute >= active_threshold, 1), else_=0)).label("active_minutes"),
            func.count(minute_bucket.c.minute_bucket).label("tracked_minutes"),
        )
        .group_by(minute_bucket.c.activity_date, minute_bucket.c.source, minute_bucket.c.device_model)
        .order_by(asc(minute_bucket.c.activity_date))
        .all()
    )

    aggregates: list[dict] = []
    for row in results:
        active = int(row.active_minutes) if row.active_minutes else 0
        tracked = int(row.tracked_minutes) if row.tracked_minutes else 0
        aggregates.append(
            {
                "activity_date": row.activity_date,
                "source": row.source,
                "device_model": row.device_model,
                "active_minutes": active,
                "tracked_minutes": tracked,
                "sedentary_minutes": tracked - active,
            }
        )
    return aggregates


# ---------------------------------------------------------------------------
# get_daily_intensity_minutes — same fix for HR-zone bucketing
# ---------------------------------------------------------------------------


def get_daily_intensity_minutes(
    self,
    db_session: DbSession,
    user_id: UUID,
    start_date: datetime,
    end_date: datetime,
    light_min: int,
    light_max: int,
    moderate_max: int,
    vigorous_max: int,
) -> list[IntensityMinutesResult]:
    """Per-(local-date, source, device) intensity minutes from HR zones."""
    user_tz = _resolve_user_timezone(db_session, user_id)
    local_date = _local_date_bucket_expr(self.model, user_tz)

    hr_id = get_series_type_id(SeriesType.heart_rate)
    minute_trunc = func.date_trunc(literal_column("'minute'"), self.model.recorded_at)

    minute_bucket = (
        db_session.query(
            local_date.label("activity_date"),
            DataSource.source,
            DataSource.device_model,
            minute_trunc.label("minute_bucket"),
            func.avg(self.model.value).label("avg_hr_in_minute"),
        )
        .join(DataSource, self.model.data_source_id == DataSource.id)
        .filter(
            DataSource.user_id == user_id,
            self.model.recorded_at >= start_date,
            self.model.recorded_at < end_date,
            self.model.series_type_definition_id == hr_id,
        )
        .group_by(local_date, DataSource.source, DataSource.device_model, minute_trunc)
        .subquery()
    )

    results = (
        db_session.query(
            minute_bucket.c.activity_date,
            minute_bucket.c.source,
            minute_bucket.c.device_model,
            func.sum(
                case(
                    (
                        (minute_bucket.c.avg_hr_in_minute >= light_min)
                        & (minute_bucket.c.avg_hr_in_minute <= light_max),
                        1,
                    ),
                    else_=0,
                )
            ).label("light_minutes"),
            func.sum(
                case(
                    (
                        (minute_bucket.c.avg_hr_in_minute > light_max)
                        & (minute_bucket.c.avg_hr_in_minute <= moderate_max),
                        1,
                    ),
                    else_=0,
                )
            ).label("moderate_minutes"),
            func.sum(
                case(
                    (
                        (minute_bucket.c.avg_hr_in_minute > moderate_max)
                        & (minute_bucket.c.avg_hr_in_minute <= vigorous_max),
                        1,
                    ),
                    else_=0,
                )
            ).label("vigorous_minutes"),
        )
        .group_by(minute_bucket.c.activity_date, minute_bucket.c.source, minute_bucket.c.device_model)
        .order_by(asc(minute_bucket.c.activity_date))
        .all()
    )

    aggregates: list[dict] = []
    for row in results:
        aggregates.append(
            {
                "activity_date": row.activity_date,
                "source": row.source,
                "device_model": row.device_model,
                "light_minutes": int(row.light_minutes) if row.light_minutes else 0,
                "moderate_minutes": int(row.moderate_minutes) if row.moderate_minutes else 0,
                "vigorous_minutes": int(row.vigorous_minutes) if row.vigorous_minutes else 0,
            }
        )
    return aggregates


def install() -> None:
    """Replace the three daily-aggregation methods on DataPointSeriesRepository."""
    import sys  # noqa: PLC0415
    import app.repositories.data_point_series_repository  # noqa: F401, PLC0415

    repo_module = sys.modules["app.repositories.data_point_series_repository"]
    repo_module.DataPointSeriesRepository.get_daily_activity_aggregates = get_daily_activity_aggregates
    repo_module.DataPointSeriesRepository.get_daily_active_minutes = get_daily_active_minutes
    repo_module.DataPointSeriesRepository.get_daily_intensity_minutes = get_daily_intensity_minutes
