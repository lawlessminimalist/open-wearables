"""garmin_connect daily totals reach the aggregates stack (since the 2026-10-31 reconcile).

Upstream's rollup skips is_daily_total samples and only mirrors samples that carry a
period, so two fork changes keep Garmin Connect steps and energy in activity summaries:
new syncs stamp period=CalendarPeriod.of_date(cdate), and the history backfill has a
garmin_connect rule (backend/app/constants/series_types/history_rules.py) for the rows
written before that. Both must key the day to Garmin's own calendar date.
"""

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import SeriesAggregate, SeriesSample, SeriesTypeDefinition
from app.repositories.series_aggregate_repository import LOCAL_DAY
from app.schemas.enums import ComputedBy, ProviderName, SeriesType, get_series_type_from_id, get_series_type_id
from app.services.aggregate_backfill_service import aggregate_backfill_service
from app.services.providers.garmin_connect.data_247 import GarminConnect247Data
from tests.factories import DataPointSeriesFactory, DataSourceFactory, UserFactory


class _StatsClient:
    def __init__(self, stats: dict[str, Any]) -> None:
        self._stats = stats

    def get_stats(self, _cdate: date) -> dict[str, Any]:
        return self._stats

    def get_last_used_device_model(self) -> str | None:
        return "fenix 8"


def _provider_days(db: Session) -> list[tuple[str, date, str, Decimal | None]]:
    rows = db.query(SeriesAggregate, LOCAL_DAY).filter(SeriesAggregate.computed_by == ComputedBy.PROVIDER)
    return sorted(
        (get_series_type_from_id(row.series_type_definition_id).value, day, row.zone_offset, row.sum)
        for row, day in rows
    )


def test_new_daily_totals_become_provider_day_rows_on_garmins_date(db: Session) -> None:
    user = UserFactory(timezone="Australia/Brisbane")
    handler = GarminConnect247Data(
        provider_name="garmin_connect",
        api_base_url="https://connect.garmin.com",
        client=_StatsClient({"totalSteps": 8432, "activeKilocalories": 610}),
    )

    handler.save_daily_stats_for_date(db, user.id, date(2026, 10, 31))
    db.flush()

    assert _provider_days(db) == [
        ("active_energy", date(2026, 10, 31), "+00:00", Decimal("610")),
        ("steps", date(2026, 10, 31), "+00:00", Decimal("8432")),
    ]
    # A day value lives in the aggregate tables only.
    steps_samples = (
        db.query(SeriesSample)
        .join(SeriesTypeDefinition, SeriesSample.series_type_definition_id == SeriesTypeDefinition.id)
        .filter(SeriesTypeDefinition.code == SeriesType.steps.value)
        .count()
    )
    assert steps_samples == 0


def test_history_rule_copies_old_daily_totals_but_not_intraday_samples(db: Session) -> None:
    source = DataSourceFactory(provider=ProviderName.GARMIN_CONNECT)
    midnight = datetime(2025, 6, 2, tzinfo=timezone.utc)  # garmin_connect stored 00:00 UTC of cdate
    steps = db.get(SeriesTypeDefinition, get_series_type_id(SeriesType.steps))
    DataPointSeriesFactory(
        mapping=source, series_type=steps, recorded_at=midnight, value=Decimal("8432"), is_daily_total=True
    )
    DataPointSeriesFactory(
        mapping=source,
        series_type=steps,
        recorded_at=midnight.replace(hour=9),
        value=Decimal("120"),
        is_daily_total=False,
    )

    aggregate_backfill_service.run(db, budget_seconds=60)

    assert _provider_days(db) == [("steps", date(2025, 6, 2), "+00:00", Decimal("8432"))]
