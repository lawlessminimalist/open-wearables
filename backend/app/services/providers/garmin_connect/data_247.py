"""Garmin Connect 24/7 data handler (sleep, HR, stress, steps, body comp, HRV)."""

import logging
from collections.abc import Callable
from contextlib import suppress
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from app.config import settings
from app.constants.sleep import SleepStageType
from app.database import DbSession
from app.models import EventRecord
from app.repositories import EventRecordRepository, UserConnectionRepository
from app.schemas.enums import SeriesType
from app.schemas.model_crud.activities import (
    EventRecordCreate,
    EventRecordDetailCreate,
    SleepStage,
    TimeSeriesSampleCreate,
)
from app.services.event_record_service import event_record_service
from app.services.providers.garmin_connect.client import GarminConnectClient
from app.services.providers.templates.base_247_data import Base247DataTemplate
from app.services.timeseries_service import timeseries_service
from app.utils.structured_logging import log_structured

logger = logging.getLogger(__name__)

_PROVIDER = "garmin_connect"

# Map garminconnect sleep level activity values → SleepStageType
# sleepLevels entries have activityLevel: 0=deep, 1=light, 2=rem, 3=awake
_ACTIVITY_LEVEL_TO_STAGE: dict[int, SleepStageType] = {
    0: SleepStageType.DEEP,
    1: SleepStageType.LIGHT,
    2: SleepStageType.REM,
    3: SleepStageType.AWAKE,
}


class GarminConnect247Data(Base247DataTemplate):
    """24/7 data handler for Garmin Connect (sleep, HR, stress, steps, body comp, HRV)."""

    def __init__(
        self,
        provider_name: str,
        api_base_url: str,
        client: GarminConnectClient,
    ) -> None:
        # oauth=None because garmin_connect bypasses OAuth entirely
        super().__init__(provider_name=provider_name, api_base_url=api_base_url, oauth=None)  # type: ignore[arg-type]
        self.client = client
        self.event_record_repo = EventRecordRepository(EventRecord)
        self.connection_repo = UserConnectionRepository()
        self.logger = logging.getLogger(self.__class__.__name__)

    # -------------------------------------------------------------------------
    # Abstract method stubs (not used; we override load_and_save_all instead)
    # -------------------------------------------------------------------------

    def get_sleep_data(self, db: DbSession, user_id: UUID, start_time: datetime, end_time: datetime) -> list[dict]:
        return []

    def normalize_sleep(self, raw_sleep: dict, user_id: UUID) -> dict:
        return {}

    def get_recovery_data(self, db: DbSession, user_id: UUID, start_time: datetime, end_time: datetime) -> list[dict]:
        return []

    def normalize_recovery(self, raw_recovery: dict, user_id: UUID) -> dict:
        return {}

    def get_activity_samples(
        self, db: DbSession, user_id: UUID, start_time: datetime, end_time: datetime
    ) -> list[dict]:
        return []

    def normalize_activity_samples(self, raw_samples: list[dict], user_id: UUID) -> dict[str, list[dict]]:
        return {}

    def get_daily_activity_statistics(
        self, db: DbSession, user_id: UUID, start_date: datetime, end_date: datetime
    ) -> list[dict]:
        return []

    def normalize_daily_activity(self, raw_stats: dict, user_id: UUID) -> dict:
        return {}

    # -------------------------------------------------------------------------
    # Sleep
    # -------------------------------------------------------------------------

    def _extract_sleep_stages(self, sleep_levels: list[dict]) -> list[SleepStage]:
        """Build SleepStage list from garminconnect sleepLevels entries."""
        stages: list[SleepStage] = []
        for entry in sleep_levels:
            start_raw = entry.get("startGMT")
            end_raw = entry.get("endGMT")
            level = entry.get("activityLevel")
            if start_raw is None or end_raw is None or level is None:
                continue
            stage_type = _ACTIVITY_LEVEL_TO_STAGE.get(int(level))
            if stage_type is None:
                continue
            try:
                start_dt = datetime.fromisoformat(str(start_raw).replace(" ", "T")).replace(tzinfo=timezone.utc)
                end_dt = datetime.fromisoformat(str(end_raw).replace(" ", "T")).replace(tzinfo=timezone.utc)
                stages.append(SleepStage(stage=stage_type, start_time=start_dt, end_time=end_dt))
            except (ValueError, TypeError):
                continue
        return sorted(stages, key=lambda s: s.start_time)

    def save_sleep_for_date(self, db: DbSession, user_id: UUID, cdate: date) -> int:
        """Fetch, normalize, and save sleep data for a single date."""
        raw = self.client.get_sleep_data(cdate)
        dto: dict = raw.get("dailySleepDTO") or {}
        if not dto:
            return 0

        start_ts = dto.get("sleepStartTimestampGMT")
        end_ts = dto.get("sleepEndTimestampGMT")
        if not start_ts or not end_ts:
            return 0

        # Garmin Connect returns sleepStartTimestampGMT / sleepEndTimestampGMT in
        # milliseconds, not seconds.
        start_dt = datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc)
        end_dt = datetime.fromtimestamp(end_ts / 1000, tz=timezone.utc)
        duration_s = int(end_dt.timestamp() - start_dt.timestamp())

        deep_s = dto.get("deepSleepSeconds") or 0
        light_s = dto.get("lightSleepSeconds") or 0
        rem_s = dto.get("remSleepSeconds") or 0
        awake_s = dto.get("awakeSleepSeconds") or 0
        total_sleep_s = deep_s + light_s + rem_s

        sleep_levels: list[dict] = raw.get("sleepLevels") or []
        stage_timestamps = self._extract_sleep_stages(sleep_levels)

        sleep_id = uuid4()
        record = EventRecordCreate(
            id=sleep_id,
            category="sleep",
            type="sleep_session",
            source_name="Garmin Connect",
            device_model=None,
            duration_seconds=duration_s,
            start_datetime=start_dt,
            end_datetime=end_dt,
            source=self.provider_name,
            user_id=user_id,
        )

        detail = EventRecordDetailCreate(
            record_id=sleep_id,
            sleep_total_duration_minutes=total_sleep_s // 60,
            sleep_time_in_bed_minutes=duration_s // 60,
            sleep_deep_minutes=deep_s // 60,
            sleep_light_minutes=light_s // 60,
            sleep_rem_minutes=rem_s // 60,
            sleep_awake_minutes=awake_s // 60,
            sleep_stages=stage_timestamps or None,
        )

        try:
            event_record_service.create_or_merge_sleep(db, user_id, record, detail, settings.sleep_end_gap_minutes)
            return 1
        except Exception as exc:
            log_structured(
                self.logger,
                "error",
                "Failed to save Garmin Connect sleep record",
                action="garmin_connect_sleep_save_error",
                error=str(exc),
                user_id=str(user_id),
            )
            return 0

    # -------------------------------------------------------------------------
    # Heart Rate
    # -------------------------------------------------------------------------

    def save_heart_rate_for_date(self, db: DbSession, user_id: UUID, cdate: date) -> int:
        """Fetch and save heart rate samples for a single date."""
        raw = self.client.get_heart_rates(cdate)
        hr_values: list = raw.get("heartRateValues") or []
        resting_hr = raw.get("restingHeartRate")

        samples: list[TimeSeriesSampleCreate] = []

        for entry in hr_values:
            # entry is [epoch_ms, bpm]
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            epoch_ms, bpm = entry[0], entry[1]
            if bpm is None:
                continue
            try:
                recorded_at = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
                samples.append(
                    TimeSeriesSampleCreate(
                        id=uuid4(),
                        user_id=user_id,
                        source=self.provider_name,
                        recorded_at=recorded_at,
                        value=Decimal(str(bpm)),
                        series_type=SeriesType.heart_rate,
                    )
                )
            except Exception as exc:
                log_structured(
                    self.logger,
                    "warning",
                    "Failed to build HR sample",
                    action="garmin_connect_hr_sample_error",
                    error=str(exc),
                    user_id=str(user_id),
                )

        if resting_hr is not None:
            try:
                midnight = datetime(cdate.year, cdate.month, cdate.day, tzinfo=timezone.utc)
                samples.append(
                    TimeSeriesSampleCreate(
                        id=uuid4(),
                        user_id=user_id,
                        source=self.provider_name,
                        recorded_at=midnight,
                        value=Decimal(str(resting_hr)),
                        series_type=SeriesType.resting_heart_rate,
                    )
                )
            except Exception as exc:
                log_structured(
                    self.logger,
                    "warning",
                    "Failed to save resting HR",
                    action="garmin_connect_resting_hr_error",
                    error=str(exc),
                    user_id=str(user_id),
                )

        if samples:
            timeseries_service.bulk_create_samples(db, samples)
        return len(samples)

    # -------------------------------------------------------------------------
    # Daily Stats (steps, calories, distance, stress summary)
    # -------------------------------------------------------------------------

    def save_daily_stats_for_date(self, db: DbSession, user_id: UUID, cdate: date) -> int:
        """Fetch and save daily stats (steps, energy, distance) for a single date."""
        raw = self.client.get_stats(cdate)
        if not raw:
            return 0

        midnight = datetime(cdate.year, cdate.month, cdate.day, tzinfo=timezone.utc)
        samples: list[TimeSeriesSampleCreate] = []

        metric_map: list[tuple[str, SeriesType]] = [
            ("totalSteps", SeriesType.steps),
            ("activeKilocalories", SeriesType.energy),
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

    # -------------------------------------------------------------------------
    # Stress (time-series)
    # -------------------------------------------------------------------------

    def save_stress_for_date(self, db: DbSession, user_id: UUID, cdate: date) -> int:
        """Fetch and save time-series stress data for a single date."""
        raw = self.client.get_stress_data(cdate)
        stress_values: list = raw.get("stressValuesArray") or []

        samples: list[TimeSeriesSampleCreate] = []
        for entry in stress_values:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            epoch_ms, stress = entry[0], entry[1]
            if stress is None or stress < 0:
                # garminconnect uses -1 / -2 for unmeasured intervals
                continue
            try:
                recorded_at = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
                samples.append(
                    TimeSeriesSampleCreate(
                        id=uuid4(),
                        user_id=user_id,
                        source=self.provider_name,
                        recorded_at=recorded_at,
                        value=Decimal(str(stress)),
                        series_type=SeriesType.garmin_stress_level,
                    )
                )
            except Exception as exc:
                log_structured(
                    self.logger,
                    "warning",
                    "Failed to build stress sample",
                    action="garmin_connect_stress_sample_error",
                    error=str(exc),
                    user_id=str(user_id),
                )

        if samples:
            timeseries_service.bulk_create_samples(db, samples)
        return len(samples)

    # -------------------------------------------------------------------------
    # Body Composition
    # -------------------------------------------------------------------------

    def save_body_composition(self, db: DbSession, user_id: UUID, start_date: date, end_date: date) -> int:
        """Fetch and save body composition data for a date range."""
        raw = self.client.get_body_composition(start_date, end_date)
        date_list: list[dict] = raw.get("dateWeightList") or []

        samples: list[TimeSeriesSampleCreate] = []

        for entry in date_list:
            cal_date = entry.get("calendarDate")
            if not cal_date:
                continue
            try:
                recorded_at = datetime.strptime(cal_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            body_metric_map: list[tuple[str, SeriesType]] = [
                ("weight", SeriesType.weight),
                ("bmi", SeriesType.body_mass_index),
                ("bodyFat", SeriesType.body_fat_percentage),
                ("muscleMass", SeriesType.skeletal_muscle_mass),
                ("boneMass", SeriesType.lean_body_mass),
            ]

            for field, series_type in body_metric_map:
                value = entry.get(field)
                if value is None:
                    continue
                # weight from garminconnect is in grams — convert to kg
                if field == "weight":
                    value = value / 1000.0
                try:
                    samples.append(
                        TimeSeriesSampleCreate(
                            id=uuid4(),
                            user_id=user_id,
                            source=self.provider_name,
                            recorded_at=recorded_at,
                            value=Decimal(str(round(value, 4))),
                            series_type=series_type,
                        )
                    )
                except Exception as exc:
                    log_structured(
                        self.logger,
                        "warning",
                        "Failed to build body comp sample",
                        action="garmin_connect_body_comp_error",
                        field=field,
                        error=str(exc),
                        user_id=str(user_id),
                    )

        if samples:
            timeseries_service.bulk_create_samples(db, samples)
        return len(samples)

    # -------------------------------------------------------------------------
    # HRV
    # -------------------------------------------------------------------------

    def save_hrv_for_date(self, db: DbSession, user_id: UUID, cdate: date) -> int:
        """Fetch and save HRV data for a single date."""
        raw = self.client.get_hrv_data(cdate)
        hrv_data: dict = raw.get("hrv") or {}
        summary: dict = hrv_data.get("hrvSummary") or {}

        last_night = summary.get("lastNight")
        if last_night is None:
            return 0

        midnight = datetime(cdate.year, cdate.month, cdate.day, tzinfo=timezone.utc)

        samples: list[TimeSeriesSampleCreate] = []
        try:
            samples.append(
                TimeSeriesSampleCreate(
                    id=uuid4(),
                    user_id=user_id,
                    source=self.provider_name,
                    recorded_at=midnight,
                    value=Decimal(str(last_night)),
                    series_type=SeriesType.heart_rate_variability_rmssd,
                )
            )
        except Exception as exc:
            log_structured(
                self.logger,
                "warning",
                "Failed to build HRV sample",
                action="garmin_connect_hrv_error",
                error=str(exc),
                user_id=str(user_id),
            )
            return 0

        weekly_avg = summary.get("weeklyAvg")
        if weekly_avg is not None:
            with suppress(Exception):
                samples.append(
                    TimeSeriesSampleCreate(
                        id=uuid4(),
                        user_id=user_id,
                        source=self.provider_name,
                        recorded_at=midnight,
                        value=Decimal(str(weekly_avg)),
                        series_type=SeriesType.heart_rate_variability_sdnn,
                    )
                )

        if samples:
            timeseries_service.bulk_create_samples(db, samples)
        return len(samples)

    # -------------------------------------------------------------------------
    # Combined load
    # -------------------------------------------------------------------------

    def load_and_save_all(
        self,
        db: DbSession,
        user_id: UUID,
        start_time: datetime | str | None = None,
        end_time: datetime | str | None = None,
        is_first_sync: bool = False,
    ) -> dict[str, int]:
        """Load and save all 24/7 data types for the given date range."""
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        if isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))

        from datetime import timedelta  # noqa: PLC0415

        if not start_time:
            start_time = datetime.now(timezone.utc) - timedelta(days=30)
        if not end_time:
            end_time = datetime.now(timezone.utc)

        start_date = start_time.date()
        end_date = end_time.date()

        results: dict[str, int] = {
            "sleep": 0,
            "heart_rate": 0,
            "daily_stats": 0,
            "stress": 0,
            "hrv": 0,
        }

        per_day_tasks: dict[str, Callable[[date], int]] = {
            "sleep": lambda d: self.save_sleep_for_date(db, user_id, d),
            "heart_rate": lambda d: self.save_heart_rate_for_date(db, user_id, d),
            "daily_stats": lambda d: self.save_daily_stats_for_date(db, user_id, d),
            "stress": lambda d: self.save_stress_for_date(db, user_id, d),
            "hrv": lambda d: self.save_hrv_for_date(db, user_id, d),
        }

        for cdate in self.client.iter_dates(start_date, end_date):
            for data_type, fn in per_day_tasks.items():
                try:
                    results[data_type] += fn(cdate)
                except Exception as exc:
                    log_structured(
                        self.logger,
                        "error",
                        f"Failed to sync {data_type} for {cdate}",
                        action="garmin_connect_sync_error",
                        data_type=data_type,
                        date=str(cdate),
                        error=str(exc),
                        user_id=str(user_id),
                    )

        # Body composition fetched once for the full range
        try:
            results["body_composition"] = self.save_body_composition(db, user_id, start_date, end_date)
        except Exception as exc:
            results["body_composition"] = 0
            log_structured(
                self.logger,
                "error",
                "Failed to sync body composition data",
                action="garmin_connect_body_comp_sync_error",
                error=str(exc),
                user_id=str(user_id),
            )

        return results
