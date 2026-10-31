"""Fork patch fix-spo2-respiratory-missing: Ultrahuman SpO2 / respiratory rate reach the DB.

Runs the real sync (upstream load_and_save_all → patched _save_daily_activity) against a
mocked /user_data/metrics response, so it also proves the patch no longer shadows
upstream's Sync247Result-returning load_and_save_all.
"""

import copy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy.orm import Session

from app.models import DataSource, SeriesAggregate, SeriesSample, SeriesTypeDefinition
from app.schemas.enums.series_types import SeriesType
from app.services.providers.factory import ProviderFactory
from app.services.providers.sync_247_result import Sync247Result
from app.services.providers.ultrahuman.data_247 import Ultrahuman247Data
from tests.factories import DataSourceFactory, UserConnectionFactory, UserFactory

_DAY = datetime(2024, 1, 15, tzinfo=timezone.utc)


def _rows(db: Session, user_id: object, series: SeriesType) -> list[tuple[float, datetime, str | None]]:
    q = (
        db.query(SeriesSample.value, SeriesSample.recorded_at, DataSource.source)
        .join(DataSource)
        .join(SeriesTypeDefinition, SeriesSample.series_type_definition_id == SeriesTypeDefinition.id)
        .filter(DataSource.user_id == user_id, SeriesTypeDefinition.code == series.value)
        .order_by(SeriesSample.recorded_at)
    )
    return [(float(v), r, s) for v, r, s in q.all()]


def _sync(db: Session, response: dict) -> tuple[object, Sync247Result]:
    user = UserFactory()
    UserConnectionFactory(user=user, provider="ultrahuman", status="active", access_token="test_token")
    DataSourceFactory(user_id=user.id, provider="ultrahuman")
    impl = ProviderFactory().get_provider("ultrahuman").data_247
    assert isinstance(impl, Ultrahuman247Data)
    with patch.object(impl, "_make_api_request", return_value=response):
        result = impl.load_and_save_all(db, user.id, start_time=_DAY, end_time=_DAY)
        db.commit()
    return user.id, result


def _metric_data(response: dict) -> list[dict]:
    return response["data"]["metric_data"]


class TestUltrahumanSpo2RespiratoryPatch:
    def test_patch_does_not_shadow_upstream_load_and_save_all(self) -> None:
        assert Ultrahuman247Data.load_and_save_all.__module__ == "app.services.providers.ultrahuman.data_247"
        assert Ultrahuman247Data._save_daily_activity.__module__.startswith("_ow_patches")
        assert Ultrahuman247Data.normalize_activity_samples.__module__.startswith("_ow_patches")

    def test_intraday_spo2_and_respiratory_rate_are_persisted(
        self, db: Session, sample_ultrahuman_api_response: dict
    ) -> None:
        response = copy.deepcopy(sample_ultrahuman_api_response)
        _metric_data(response).extend(
            [
                {"type": "blood_oxygen", "object": {"values": [{"value": 98, "timestamp": 1705290000}]}},
                {"type": "breath_rate", "object": {"values": [{"value": 14.5, "timestamp": 1705290000}]}},
            ]
        )

        user_id, result = _sync(db, response)

        assert isinstance(result, Sync247Result)
        assert not result.failures
        assert result.outcomes["activity_samples"].rows_written > 0
        spo2 = _rows(db, user_id, SeriesType.oxygen_saturation)
        # Intraday SpO2 present, so the Sleep object's nightly 97 is NOT also emitted.
        assert [(v, s) for v, _, s in spo2] == [(98.0, "ultrahuman")]
        rr = _rows(db, user_id, SeriesType.respiratory_rate)
        assert [(v, s) for v, _, s in rr] == [(14.5, "ultrahuman")]
        # Upstream's own series still flow through the decorated normalizer.
        assert _rows(db, user_id, SeriesType.heart_rate)

    def test_sleep_spo2_average_falls_back_to_midpoint_sample(
        self, db: Session, sample_ultrahuman_api_response: dict
    ) -> None:
        response = copy.deepcopy(sample_ultrahuman_api_response)
        sleep = next(m for m in _metric_data(response) if m["type"] == "Sleep")["object"]
        sleep["spo2"] = {"value": 96}

        user_id, _ = _sync(db, response)

        mid = (sleep["bedtime_start"] + sleep["bedtime_end"]) // 2
        assert _rows(db, user_id, SeriesType.oxygen_saturation) == [
            (96.0, datetime.fromtimestamp(mid, tz=timezone.utc), "ultrahuman")
        ]

    def test_daily_scalar_series_carry_source(self, db: Session, sample_ultrahuman_api_response: dict) -> None:
        # Since upstream #1931 a daily scalar carries period=, so it lands in
        # series_aggregate only. Its data_source must still be the source-labelled row.
        user_id, _ = _sync(db, copy.deepcopy(sample_ultrahuman_api_response))

        sources = (
            db.query(DataSource.source)
            .join(SeriesAggregate, SeriesAggregate.data_source_id == DataSource.id)
            .join(SeriesTypeDefinition, SeriesAggregate.series_type_definition_id == SeriesTypeDefinition.id)
            .filter(DataSource.user_id == user_id, SeriesTypeDefinition.code == SeriesType.resting_heart_rate.value)
            .all()
        )
        assert sources
        assert {s for (s,) in sources} == {"ultrahuman"}


def test_daily_scalar_day_is_the_local_date_east_of_utc() -> None:
    """A day_start at 13:00Z is local midnight at +11:00: the row belongs to the next date."""
    import importlib.util  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    path = Path(__file__).resolve().parents[4] / "ow-patches" / "local" / "fix-spo2-respiratory-missing.py"
    spec = importlib.util.spec_from_file_location("spo2_patch", path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for day_start, want_day, want_offset in [
        (datetime(2026, 10, 30, 13, tzinfo=timezone.utc), "2026-10-31", "+11:00"),  # Sydney, summer
        (datetime(2026, 10, 30, 14, tzinfo=timezone.utc), "2026-10-31", "+10:00"),  # Brisbane
        (datetime(2026, 10, 31, 0, tzinfo=timezone.utc), "2026-10-31", "+00:00"),  # UTC
        (datetime(2026, 10, 31, 5, tzinfo=timezone.utc), "2026-10-31", "-05:00"),  # US Eastern
    ]:
        period = mod._ultrahuman_day(day_start)
        local = period.start + timedelta(
            hours=int(period.zone_offset[:3]), minutes=int(period.zone_offset[0] + period.zone_offset[4:])
        )
        assert (local.date().isoformat(), period.zone_offset) == (want_day, want_offset)
