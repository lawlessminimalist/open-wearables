"""Behaviour of ow-patch fix-activity-summary-utc-bucketing.

Pins the fork delta (user-local date bucketing when zone_offset is NULL) and the
upstream fields the 2026-10-19 rebase re-acquired (#1801 total_energy_sum and
None-not-0 semantics), so a stale rebase that drops either fails here.
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
    from app.models import DataPointSeries  # noqa: PLC0415

    return DataPointSeriesRepository(DataPointSeries)


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
    result = series_repo.get_daily_activity_aggregates(db, user.id, start, start + timedelta(days=2))
    assert [(r["activity_date"].isoformat(), r["steps_sum"]) for r in result] == [("2026-06-21", 500)]

    active = series_repo.get_daily_active_minutes(db, user.id, start, start + timedelta(days=2))
    assert [r["activity_date"].isoformat() for r in active] == ["2026-06-21"]


def test_populated_zone_offset_wins_over_user_timezone(db: Session, series_repo: DataPointSeriesRepository) -> None:
    user = UserFactory(timezone="Australia/Brisbane")
    recorded = datetime(2026, 6, 20, 22, 0, tzinfo=timezone.utc)
    series_repo.bulk_create(db, [_sample(user.id, SeriesType.steps, recorded, 500, zone_offset="+00:00")])
    db.commit()

    start = datetime(2026, 6, 20, tzinfo=timezone.utc)
    result = series_repo.get_daily_activity_aggregates(db, user.id, start, start + timedelta(days=2))
    assert [r["activity_date"].isoformat() for r in result] == ["2026-06-20"]


def test_rebased_upstream_fields_present(db: Session, series_repo: DataPointSeriesRepository) -> None:
    """#1801: total_energy_sum is aggregated, and absent sums are None (not 0)."""
    user = UserFactory()
    recorded = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
    series_repo.bulk_create(db, [_sample(user.id, SeriesType.total_energy, recorded, 2400.0)])
    db.commit()

    start = datetime(2026, 6, 20, tzinfo=timezone.utc)
    (row,) = series_repo.get_daily_activity_aggregates(db, user.id, start, start + timedelta(days=1))
    assert row["total_energy_sum"] == 2400.0
    assert row["steps_sum"] is None
    assert row["active_energy_sum"] is None
    assert row["basal_energy_sum"] is None
