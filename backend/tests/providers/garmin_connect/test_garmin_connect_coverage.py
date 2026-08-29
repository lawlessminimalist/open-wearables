"""Tests for the garmin_connect data-coverage fixes.

Covers four defects found by auditing the library payloads against what we
persist, plus the provider-inference fix they depend on:

1. HRV was fetched every day and never stored — the code read ``lastNight`` but
   the payload key is ``lastNightAvg``, so the guard returned 0 on every date.
2. Daily totals were written without ``is_daily_total``, which aggregation
   treats as summable — double-counting against another provider.
3. The nightly SpO2 / respiration / HRV averages sitting in the sleep payload we
   already fetch were discarded.
4. ``ProviderName.from_source_string`` let ``garmin`` shadow ``garmin_connect``.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from typing import Any
from uuid import uuid4

import pytest

from app.schemas.enums import SeriesType
from app.schemas.enums.provider import ProviderName
from app.services.providers.garmin_connect.data_247 import GarminConnect247Data


class _FakeClient:
    """Minimal stand-in returning canned payloads."""

    def __init__(self, **payloads: Any) -> None:
        self._payloads = payloads
        self.device_calls = 0

    def get_hrv_data(self, _cdate: date) -> dict:
        return self._payloads.get("hrv", {})

    def get_stats(self, _cdate: date) -> dict:
        return self._payloads.get("stats", {})

    def get_last_used_device_model(self) -> str | None:
        self.device_calls += 1
        return self._payloads.get("device")

    def iter_dates(self, start: date, end: date) -> list[date]:
        from datetime import timedelta

        out, cur = [], start
        while cur <= end:
            out.append(cur)
            cur += timedelta(days=1)
        return out


@pytest.fixture
def handler_factory(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """Build a handler whose writes are captured instead of hitting the DB."""

    def _make(**payloads: Any):  # noqa: ANN202
        captured: list[Any] = []
        h = GarminConnect247Data(
            provider_name="garmin_connect",
            api_base_url="https://connect.garmin.com",
            client=_FakeClient(**payloads),
        )
        import app.services.providers.garmin_connect.data_247 as mod

        monkeypatch.setattr(
            mod.timeseries_service,
            "bulk_create_samples",
            lambda _db, samples: captured.extend(samples),
        )
        return h, captured

    return _make


class TestHrvKeyRegression:
    """The single highest-cost bug: N requests per sync, zero rows written."""

    def test_last_night_avg_is_persisted(self, handler_factory: Any) -> None:
        h, captured = handler_factory(hrv={"hrvSummary": {"lastNightAvg": 62, "weeklyAvg": 58}})
        n = h.save_hrv_for_date(None, uuid4(), date(2026, 8, 18))  # type: ignore[arg-type]
        assert n >= 1, "lastNightAvg must be persisted"
        rmssd = [s for s in captured if s.series_type is SeriesType.heart_rate_variability_rmssd]
        assert len(rmssd) == 1
        assert int(rmssd[0].value) == 62

    def test_old_lastnight_key_no_longer_required(self, handler_factory: Any) -> None:
        """Pre-fix this payload wrote nothing because it looked for 'lastNight'."""
        h, captured = handler_factory(hrv={"hrvSummary": {"lastNightAvg": 55}})
        assert h.save_hrv_for_date(None, uuid4(), date(2026, 8, 18)) == 1  # type: ignore[arg-type]
        assert captured

    def test_weekly_avg_is_not_written_as_sdnn(self, handler_factory: Any) -> None:
        """weeklyAvg is a rolling RMSSD average, not an SDNN measurement."""
        h, captured = handler_factory(hrv={"hrvSummary": {"lastNightAvg": 60, "weeklyAvg": 58}})
        h.save_hrv_for_date(None, uuid4(), date(2026, 8, 18))  # type: ignore[arg-type]
        assert not [s for s in captured if s.series_type is SeriesType.heart_rate_variability_sdnn]

    def test_intraday_readings_are_persisted(self, handler_factory: Any) -> None:
        h, captured = handler_factory(
            hrv={
                "hrvSummary": {"lastNightAvg": 60},
                "hrvReadings": [
                    {"hrvValue": 58, "readingTimeGMT": "2026-08-18T14:05:00.0"},
                    {"hrvValue": 61, "readingTimeGMT": "2026-08-18T14:10:00.0"},
                    {"hrvValue": None, "readingTimeGMT": "2026-08-18T14:15:00.0"},
                ],
            }
        )
        h.save_hrv_for_date(None, uuid4(), date(2026, 8, 18))  # type: ignore[arg-type]
        rmssd = [s for s in captured if s.series_type is SeriesType.heart_rate_variability_rmssd]
        assert len(rmssd) == 3, "nightly average + 2 valid readings (null skipped)"

    def test_empty_payload_is_harmless(self, handler_factory: Any) -> None:
        h, captured = handler_factory(hrv={})
        assert h.save_hrv_for_date(None, uuid4(), date(2026, 8, 18)) == 0  # type: ignore[arg-type]
        assert captured == []


class TestDailyTotalsFlag:
    """Daily totals must not be treated as summable intraday samples."""

    _STATS = {
        "totalSteps": 8000,
        "activeKilocalories": 500,
        "bmrKilocalories": 1600,
        "totalDistanceMeters": 6000,
        "floorsAscended": 12,
        "moderateIntensityMinutes": 30,
        "vigorousIntensityMinutes": 15,
        "averageStressLevel": 28,
        "restingHeartRate": 58,
    }

    def test_sum_series_are_marked_daily_total(self, handler_factory: Any) -> None:
        h, captured = handler_factory(stats=self._STATS)
        h.save_daily_stats_for_date(None, uuid4(), date(2026, 8, 18))  # type: ignore[arg-type]
        by_type = {s.series_type: s for s in captured}
        for st in (SeriesType.steps, SeriesType.energy, SeriesType.distance_walking_running):
            assert by_type[st].is_daily_total is True, f"{st} must be flagged a daily total"

    def test_non_sum_series_are_not_flagged(self, handler_factory: Any) -> None:
        h, captured = handler_factory(stats=self._STATS)
        h.save_daily_stats_for_date(None, uuid4(), date(2026, 8, 18))  # type: ignore[arg-type]
        by_type = {s.series_type: s for s in captured}
        assert by_type[SeriesType.resting_heart_rate].is_daily_total is not True


class TestNewlyIngestedStatsFields:
    """Fields already present in get_stats that used to be discarded."""

    def test_basal_energy_and_floors_are_ingested(self, handler_factory: Any) -> None:
        h, captured = handler_factory(stats=TestDailyTotalsFlag._STATS)
        h.save_daily_stats_for_date(None, uuid4(), date(2026, 8, 18))  # type: ignore[arg-type]
        by_type = {s.series_type: s for s in captured}
        assert int(by_type[SeriesType.basal_energy].value) == 1600
        assert int(by_type[SeriesType.flights_climbed].value) == 12

    def test_intensity_minutes_are_summed_not_duplicated(self, handler_factory: Any) -> None:
        """Two samples at one timestamp would silently lose one to the upsert."""
        h, captured = handler_factory(stats=TestDailyTotalsFlag._STATS)
        h.save_daily_stats_for_date(None, uuid4(), date(2026, 8, 18))  # type: ignore[arg-type]
        ex = [s for s in captured if s.series_type is SeriesType.exercise_time]
        assert len(ex) == 1
        assert int(ex[0].value) == 45, "moderate 30 + vigorous 15"

    def test_absent_fields_are_skipped(self, handler_factory: Any) -> None:
        h, captured = handler_factory(stats={"totalSteps": 100})
        h.save_daily_stats_for_date(None, uuid4(), date(2026, 8, 18))  # type: ignore[arg-type]
        assert {s.series_type for s in captured} == {SeriesType.steps}


class TestSleepPhysiology:
    """Nightly averages carried free in the sleep payload."""

    def test_spo2_respiration_and_hrv_are_persisted(self, handler_factory: Any) -> None:
        h, captured = handler_factory()
        start = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)
        # Field names verified against a live Garmin Connect payload 2026-08-29.
        # The previous fixture used avgSpO2 / avgRespirationValue / avgSleepHRV,
        # none of which exist in the response — so it asserted the same wrong
        # shape the code assumed, and the suite stayed green while the provider
        # wrote nothing. avgOvernightHrv sits at the response ROOT.
        n = h._save_sleep_physiology(
            None,  # type: ignore[arg-type]
            uuid4(),
            {"avgOvernightHrv": 63},
            {"averageSpO2Value": 96, "averageRespirationValue": 14.5},
            start,
            end,
        )
        assert n == 3
        types = {s.series_type for s in captured}
        assert types == {
            SeriesType.oxygen_saturation,
            SeriesType.respiratory_rate,
            SeriesType.heart_rate_variability_rmssd,
        }

    def test_emitted_at_sleep_midpoint(self, handler_factory: Any) -> None:
        h, captured = handler_factory()
        start = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)
        h._save_sleep_physiology(None, uuid4(), {}, {"averageSpO2Value": 96}, start, end)  # type: ignore[arg-type]
        assert captured[0].recorded_at == datetime(2026, 8, 18, 16, 0, tzinfo=timezone.utc)

    def test_missing_fields_emit_nothing(self, handler_factory: Any) -> None:
        h, captured = handler_factory()
        start = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)
        assert h._save_sleep_physiology(None, uuid4(), {}, {}, start, end) == 0  # type: ignore[arg-type]
        assert captured == []


class TestProviderPrefixShadowing:
    """`garmin` must not shadow the more specific `garmin_connect`."""

    def test_patch_is_installed(self) -> None:
        assert sys.modules.get("_ow_patches_fix_provider_prefix_shadowing") is not None
        assert ProviderName.from_source_string.__func__.__module__.startswith("_ow_patches")

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("garmin_connect", ProviderName.GARMIN_CONNECT),
            ("Garmin Connect", ProviderName.GARMIN_CONNECT),
            ("garmin-connect", ProviderName.GARMIN_CONNECT),
            ("garmin", ProviderName.GARMIN),
            ("Garmin", ProviderName.GARMIN),
        ],
    )
    def test_garmin_variants(self, source: str, expected: ProviderName) -> None:
        assert ProviderName.from_source_string(source) is expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("apple_health_sdk", ProviderName.APPLE),
            ("com.apple.health.ABC123", ProviderName.APPLE),
            ("ultrahuman", ProviderName.ULTRAHUMAN),
            ("oura", ProviderName.OURA),
            ("whoop", ProviderName.WHOOP),
            ("nonsense", ProviderName.UNKNOWN),
            ("", ProviderName.UNKNOWN),
            (None, ProviderName.UNKNOWN),
        ],
    )
    def test_no_regression_for_other_providers(self, source: str | None, expected: ProviderName) -> None:
        assert ProviderName.from_source_string(source) is expected

    def test_no_provider_value_can_be_shadowed(self) -> None:
        """Generic guard: every provider must resolve to itself from its own value."""
        for p in ProviderName:
            if p in (ProviderName.UNKNOWN, ProviderName.INTERNAL):
                continue
            assert ProviderName.from_source_string(p.value) is p, (
                f"{p.value} is shadowed by a shorter provider value — from_source_string must prefer the longest match."
            )


class TestCoverageDeclared:
    def test_strategy_declares_non_empty_coverage(self) -> None:
        from app.services.providers.garmin_connect.strategy import GarminConnectStrategy

        c = GarminConnectStrategy().coverage
        assert c.timeseries, "garmin_connect reported zero coverage on /meta/coverage"
        assert SeriesType.heart_rate_variability_rmssd in c.timeseries
        assert SeriesType.basal_energy in c.timeseries
        assert SeriesType.oxygen_saturation in c.timeseries

    def test_declared_detail_fields_exist_on_the_schema(self) -> None:
        from app.schemas.model_crud.activities import EventRecordDetailCreate
        from app.services.providers.garmin_connect.strategy import GarminConnectStrategy

        valid = set(EventRecordDetailCreate.model_fields)
        c = GarminConnectStrategy().coverage
        assert not (c.workout_fields - valid)
        assert not (c.sleep_fields - valid)


class TestSessionRestoreHydratesDisplayName:
    """Regression: a token restored from disk left display_name unset.

    `garminconnect` sets display_name only inside login(). client.load()
    authenticates but leaves it None, and get_stats / get_heart_rates
    interpolate it into their URLs — raising "Display name is not set" and
    returning 403 respectively. Latent until the token store moved to a PVC:
    before that /tmp was wiped every pod restart, so every sync did a full login
    and set display_name as a side effect. After the PVC, a restored session
    silently lost daily stats and heart rate on every date.
    """

    def _client(self, tmp_path: Any):  # noqa: ANN202
        from app.services.providers.garmin_connect.client import GarminConnectClient

        c = GarminConnectClient()
        c._token_store_path = lambda: tmp_path  # type: ignore[method-assign]
        return c

    def _api(self, profile: Any, authenticated: bool = True):  # noqa: ANN202
        class FakeInner:
            is_authenticated = authenticated

            def load(self, _p: str) -> None:
                pass

            def connectapi(self, path: str) -> Any:
                assert path == "/userprofile-service/socialProfile"
                if isinstance(profile, Exception):
                    raise profile
                return profile

        class FakeApi:
            display_name = None
            full_name = None
            client = FakeInner()

        return FakeApi()

    def test_display_name_is_populated_from_social_profile(self, tmp_path: Any) -> None:
        c = self._client(tmp_path)
        api = self._api({"displayName": "dhlaw", "fullName": "Daniel Lawless"})
        assert c._try_load_saved_session(api) is True
        assert api.display_name == "dhlaw", "get_stats/get_heart_rates need this"
        assert api.full_name == "Daniel Lawless"

    def test_falls_back_to_login_when_profile_fetch_fails(self, tmp_path: Any) -> None:
        """Returning False makes the caller do a full login, which sets it itself."""
        c = self._client(tmp_path)
        api = self._api(RuntimeError("boom"))
        assert c._try_load_saved_session(api) is False
        assert api.display_name is None

    def test_falls_back_when_profile_has_no_display_name(self, tmp_path: Any) -> None:
        c = self._client(tmp_path)
        assert c._try_load_saved_session(self._api({"fullName": "x"})) is False
        assert c._try_load_saved_session(self._api({})) is False
        assert c._try_load_saved_session(self._api(None)) is False

    def test_unauthenticated_session_short_circuits(self, tmp_path: Any) -> None:
        c = self._client(tmp_path)
        api = self._api({"displayName": "dhlaw"}, authenticated=False)
        assert c._try_load_saved_session(api) is False

    def test_missing_token_store_is_not_an_error(self, tmp_path: Any) -> None:
        from app.services.providers.garmin_connect.client import GarminConnectClient

        c = GarminConnectClient()
        c._token_store_path = lambda: tmp_path / "does-not-exist"  # type: ignore[method-assign]
        assert c._try_load_saved_session(self._api({"displayName": "dhlaw"})) is False


class TestSleepEpochSeries:
    """Intraday SpO2 / respiration ride along in the sleep payload we already fetch.

    The official Garmin webhook provider persists both a nightly average and an
    intraday series for these (see Garmin247Data), so garmin_connect matches that
    shape rather than storing only the average.
    """

    def _raw(self) -> dict:
        # Shapes verified against a live payload 2026-08-29.
        return {
            "wellnessEpochSPO2DataDTOList": [
                {"epochTimestamp": "2026-08-18T14:05:00.0", "spo2Reading": 96},
                {"epochTimestamp": "2026-08-18T14:06:00.0", "spo2Reading": 94},
                {"epochTimestamp": "2026-08-18T14:07:00.0", "spo2Reading": 0},
            ],
            "wellnessEpochRespirationDataDTOList": [
                {"startTimeGMT": 1787836000000, "respirationValue": 15.0},
                {"startTimeGMT": 1787836120000, "respirationValue": -1.0},
            ],
        }

    def test_intraday_spo2_and_respiration_persisted(self, handler_factory: Any) -> None:
        h, captured = handler_factory()
        out = h._build_sleep_epoch_samples(uuid4(), self._raw(), "EPIX Gen2")
        spo2 = [s for s in out if s.series_type is SeriesType.oxygen_saturation]
        resp = [s for s in out if s.series_type is SeriesType.respiratory_rate]
        # The 0 SpO2 and -1 respiration are Garmin's "not measured" sentinels.
        assert len(spo2) == 2, "non-positive SpO2 readings must be skipped"
        assert len(resp) == 1, "non-positive respiration readings must be skipped"
        assert {int(s.value) for s in spo2} == {96, 94}

    def test_hrv_not_duplicated_from_sleep_payload(self, handler_factory: Any) -> None:
        """hrvData duplicates get_hrv_data's hrvReadings; only one source writes it."""
        h, _ = handler_factory()
        raw = self._raw() | {"hrvData": [{"value": 44.0, "startGMT": 1787836000000}]}
        out = h._build_sleep_epoch_samples(uuid4(), raw, None)
        assert not [s for s in out if s.series_type is SeriesType.heart_rate_variability_rmssd]

    def test_device_model_is_stamped(self, handler_factory: Any) -> None:
        """Identity is (user_id, device_model, source) — omitting it splits the data source."""
        h, _ = handler_factory()
        out = h._build_sleep_epoch_samples(uuid4(), self._raw(), "EPIX Gen2")
        assert out
        assert all(s.device_model == "EPIX Gen2" for s in out)


class TestPayloadKeysMatchReality:
    """Guard the bug class itself: fixtures that agree with the code but not Garmin.

    The previous suite asserted avgSpO2 / avgRespirationValue / avgSleepHRV and a
    {"hrv": {...}} wrapper. None exist in a real response, so code and fixture
    shared one wrong assumption and the suite stayed green while the provider
    wrote zero rows for months. These pin the keys actually observed on the wire.
    """

    LIVE_SLEEP_DTO_KEYS = {"averageSpO2Value", "averageRespirationValue"}
    LIVE_SLEEP_ROOT_KEYS = {"avgOvernightHrv", "wellnessEpochSPO2DataDTOList", "wellnessEpochRespirationDataDTOList"}
    LIVE_HRV_ROOT_KEYS = {"hrvSummary", "hrvReadings"}

    def test_sleep_physiology_reads_the_live_keys(self, handler_factory: Any) -> None:
        h, captured = handler_factory()
        start = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
        end = datetime(2026, 8, 18, 20, 0, tzinfo=timezone.utc)
        raw = {k: 50.0 for k in self.LIVE_SLEEP_ROOT_KEYS if k == "avgOvernightHrv"}
        dto = dict.fromkeys(self.LIVE_SLEEP_DTO_KEYS, 90.0)
        assert h._save_sleep_physiology(None, uuid4(), raw, dto, start, end) == 3  # type: ignore[arg-type]

    def test_hrv_summary_is_read_from_the_root(self, handler_factory: Any) -> None:
        """A {"hrv": {...}} wrapper must NOT be required — it does not exist."""
        h, captured = handler_factory(hrv={"hrvSummary": {"lastNightAvg": 44}})
        assert h.save_hrv_for_date(None, uuid4(), date(2026, 8, 18)) == 1  # type: ignore[arg-type]
        assert int(captured[0].value) == 44
