"""Behaviour of ow-patch fix-activity-summary-utc-bucketing.

Pins the fork delta (user-local date bucketing when zone_offset is NULL) on the two
minutes methods the patch still replaces. Day sums moved to upstream's
series_aggregate rollup in the 2026-10-31 reconcile and are not patched.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.repositories.data_point_series_repository import DataPointSeriesRepository
from app.schemas.enums import SeriesType
from app.schemas.model_crud.activities import TimeSeriesSampleCreate
from tests.factories import UserFactory


@pytest.fixture
def series_repo() -> DataPointSeriesRepository:
    from app.models import SeriesSample  # noqa: PLC0415

    return DataPointSeriesRepository(SeriesSample)


def _sample(
    user_id: UUID,
    series_type: SeriesType,
    recorded_at: datetime,
    value: float,
    zone_offset: str | None = None,
) -> TimeSeriesSampleCreate:
    return TimeSeriesSampleCreate(
        id=uuid4(),
        user_id=user_id,
        source="garmin_connect",
        device_model="fenix",
        recorded_at=recorded_at,
        zone_offset=zone_offset,
        value=value,
        series_type=series_type,
        is_daily_total=False,
    )


def test_null_zone_offset_buckets_by_user_timezone(db: Session, series_repo: DataPointSeriesRepository) -> None:
    """Sat 22:00 UTC is Sun 08:00 in Brisbane: it belongs to Sunday, not Saturday."""
    user = UserFactory(timezone="Australia/Brisbane")
    recorded = datetime(2026, 6, 20, 22, 0, tzinfo=timezone.utc)  # Saturday UTC
    series_repo.bulk_create(db, [_sample(user.id, SeriesType.steps, recorded, 500)])
    db.commit()

    start = datetime(2026, 6, 20, tzinfo=timezone.utc)
    active = series_repo.get_daily_active_minutes(db, user.id, start, start + timedelta(days=2))
    assert [r["activity_date"].isoformat() for r in active] == ["2026-06-21"]


def test_populated_zone_offset_wins_over_user_timezone(db: Session, series_repo: DataPointSeriesRepository) -> None:
    user = UserFactory(timezone="Australia/Brisbane")
    recorded = datetime(2026, 6, 20, 22, 0, tzinfo=timezone.utc)
    series_repo.bulk_create(db, [_sample(user.id, SeriesType.steps, recorded, 500, zone_offset="+00:00")])
    db.commit()

    start = datetime(2026, 6, 20, tzinfo=timezone.utc)
    active = series_repo.get_daily_active_minutes(db, user.id, start, start + timedelta(days=2))
    assert [r["activity_date"].isoformat() for r in active] == ["2026-06-20"]


def test_intensity_minutes_bucket_by_user_timezone(db: Session, series_repo: DataPointSeriesRepository) -> None:
    user = UserFactory(timezone="Australia/Brisbane")
    recorded = datetime(2026, 6, 20, 22, 0, tzinfo=timezone.utc)
    series_repo.bulk_create(db, [_sample(user.id, SeriesType.heart_rate, recorded, 150)])
    db.commit()

    start = datetime(2026, 6, 20, tzinfo=timezone.utc)
    rows = series_repo.get_daily_intensity_minutes(db, user.id, start, start + timedelta(days=2), 90, 120, 160, 200)
    assert [r["activity_date"].isoformat() for r in rows] == ["2026-06-21"]
