# patch_id:        fix-activity-summary-utc-bucketing
# upstream_file:   backend/app/repositories/data_point_series_repository.py
# upstream_symbol: DataPointSeriesRepository.get_daily_activity_aggregates + .get_daily_active_minutes + .get_daily_intensity_minutes
# retire_when:     DataPointSeriesRepository falls back to user.timezone (not UTC) when zone_offset is NULL.
#                  Marker: presence of `user.timezone` or equivalent in the local_date expression in
#                  data_point_series_repository.py (upstream's cea6502 fix uses zone_offset only).

"""Bucket daily activity / active-minutes / intensity-minutes aggregates by
the user's local date, combining upstream's per-record zone_offset approach
with a user.timezone fallback for providers that don't populate zone_offset.

Background
----------
Upstream commit cea6502 added timezone-aware bucketing to these three methods
using the per-record zone_offset column:

    local_date = cast(
        recorded_at + coalesce(zone_offset, "+00:00")::interval,
        Date,
    )

This correctly handles Apple Health (zone_offset always populated) but falls
back to UTC for Garmin Connect and Ultrahuman, which leave zone_offset NULL on
their historical sync data. For a Brisbane user (UTC+10), that means a Sunday
morning trail run (UTC timestamps: Saturday evening) lands on the Saturday
card with wrong HR stats.

Fix
---
Priority chain:
  1. zone_offset present on the row  →  use it (upstream behaviour, unchanged)
  2. zone_offset NULL, user.timezone set  →  apply AT TIME ZONE user.timezone
  3. both NULL  →  UTC (identical to upstream behaviour, safe default)

SQLAlchemy expression:

    local_date = cast(
        coalesce(
            recorded_at + zone_offset::interval,          -- NULL when zone_offset is NULL
            timezone(user_tz, recorded_at),               -- user_tz='UTC' when unset
        ),
        Date,
    )

The WHERE clause also needs the upstream -1 day lookback so cross-midnight
samples are captured when their UTC timestamp falls in the prior calendar day:

    recorded_at >= start_date - 1 day  (raw pre-filter)
    local_date  >= start_date          (precise local-date lower bound)
    local_date  <  end_date            (precise local-date upper bound)
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import Date, Interval, asc, case, cast, func, literal_column

from app.database import DbSession
from app.models import DataSource
from app.repositories.data_point_series_repository import (
    ActiveMinutesResult,
    IntensityMinutesResult,
)
from app.schemas.enums import SeriesType, get_series_type_id


def _resolve_user_timezone(db_session: DbSession, user_id: UUID) -> str:
    """Return the user's IANA timezone string, or 'UTC' as the safe default."""
    from app.models import User  # noqa: PLC0415

    tz = db_session.query(User.timezone).filter(User.id == user_id).scalar()
    return tz or "UTC"


def _local_date_expr(model, user_tz: str):
    """Combined local-date expression: zone_offset first, user_tz fallback, UTC default.

    Priority:
      1. recorded_at + zone_offset::interval   (NULL when zone_offset is NULL)
      2. recorded_at AT TIME ZONE user_tz       (user_tz='UTC' when unset → same as UTC)
    """
    zone_offset_shifted = model.recorded_at + cast(model.zone_offset, Interval)
    user_tz_shifted = func.timezone(user_tz, model.recorded_at)
    return cast(func.coalesce(zone_offset_shifted, user_tz_shifted), Date)


# ---------------------------------------------------------------------------
# get_daily_activity_aggregates
# ---------------------------------------------------------------------------

def get_daily_activity_aggregates(
    self,
    db_session: DbSession,
    user_id: UUID,
    start_date: datetime,
    end_date: datetime,
):
    """Per-(local-date, source, device) activity aggregates."""
    user_tz = _resolve_user_timezone(db_session, user_id)
    local_date = _local_date_expr(self.model, user_tz)

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
            func.sum(case((self.model.series_type_definition_id == steps_id, self.model.value), else_=0)).label("steps_sum"),
            func.sum(case((self.model.series_type_definition_id == energy_id, self.model.value), else_=0)).label("active_energy_sum"),
            func.sum(case((self.model.series_type_definition_id == basal_energy_id, self.model.value), else_=0)).label("basal_energy_sum"),
            func.avg(case((self.model.series_type_definition_id == hr_id, self.model.value), else_=None)).label("hr_avg"),
            func.max(case((self.model.series_type_definition_id == hr_id, self.model.value), else_=None)).label("hr_max"),
            func.min(case((self.model.series_type_definition_id == hr_id, self.model.value), else_=None)).label("hr_min"),
            func.sum(case((self.model.series_type_definition_id == distance_id, self.model.value))).label("distance_sum"),
            func.sum(case((self.model.series_type_definition_id == flights_id, self.model.value))).label("flights_climbed_sum"),
        )
        .join(DataSource, self.model.data_source_id == DataSource.id)
        .filter(
            DataSource.user_id == user_id,
            self.model.recorded_at >= start_date - timedelta(days=1),
            local_date >= cast(start_date, Date),
            local_date < cast(end_date, Date),
            self.model.series_type_definition_id.in_(
                [steps_id, energy_id, basal_energy_id, hr_id, distance_id, flights_id]
            ),
        )
        .group_by(local_date, DataSource.source, DataSource.device_model)
        .order_by(asc(local_date))
        .all()
    )

    return [
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
        for row in results
    ]


# ---------------------------------------------------------------------------
# get_daily_active_minutes
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
    local_date = _local_date_expr(self.model, user_tz)

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
            self.model.recorded_at >= start_date - timedelta(days=1),
            local_date >= cast(start_date, Date),
            local_date < cast(end_date, Date),
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

    return [
        {
            "activity_date": row.activity_date,
            "source": row.source,
            "device_model": row.device_model,
            "active_minutes": int(row.active_minutes) if row.active_minutes else 0,
            "tracked_minutes": int(row.tracked_minutes) if row.tracked_minutes else 0,
            "sedentary_minutes": (int(row.tracked_minutes) if row.tracked_minutes else 0)
                                 - (int(row.active_minutes) if row.active_minutes else 0),
        }
        for row in results
    ]


# ---------------------------------------------------------------------------
# get_daily_intensity_minutes
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
    local_date = _local_date_expr(self.model, user_tz)

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
            self.model.recorded_at >= start_date - timedelta(days=1),
            local_date >= cast(start_date, Date),
            local_date < cast(end_date, Date),
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
            func.sum(case(
                ((minute_bucket.c.avg_hr_in_minute >= light_min) & (minute_bucket.c.avg_hr_in_minute <= light_max), 1),
                else_=0,
            )).label("light_minutes"),
            func.sum(case(
                ((minute_bucket.c.avg_hr_in_minute > light_max) & (minute_bucket.c.avg_hr_in_minute <= moderate_max), 1),
                else_=0,
            )).label("moderate_minutes"),
            func.sum(case(
                ((minute_bucket.c.avg_hr_in_minute > moderate_max) & (minute_bucket.c.avg_hr_in_minute <= vigorous_max), 1),
                else_=0,
            )).label("vigorous_minutes"),
        )
        .group_by(minute_bucket.c.activity_date, minute_bucket.c.source, minute_bucket.c.device_model)
        .order_by(asc(minute_bucket.c.activity_date))
        .all()
    )

    return [
        {
            "activity_date": row.activity_date,
            "source": row.source,
            "device_model": row.device_model,
            "light_minutes": int(row.light_minutes) if row.light_minutes else 0,
            "moderate_minutes": int(row.moderate_minutes) if row.moderate_minutes else 0,
            "vigorous_minutes": int(row.vigorous_minutes) if row.vigorous_minutes else 0,
        }
        for row in results
    ]


def install() -> None:
    """Replace the three daily-aggregation methods on DataPointSeriesRepository."""
    import sys  # noqa: PLC0415
    import app.repositories.data_point_series_repository  # noqa: F401, PLC0415

    repo_module = sys.modules["app.repositories.data_point_series_repository"]
    repo_module.DataPointSeriesRepository.get_daily_activity_aggregates = get_daily_activity_aggregates
    repo_module.DataPointSeriesRepository.get_daily_active_minutes = get_daily_active_minutes
    repo_module.DataPointSeriesRepository.get_daily_intensity_minutes = get_daily_intensity_minutes
