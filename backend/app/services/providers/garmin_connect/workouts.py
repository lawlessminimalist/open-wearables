"""Garmin Connect workouts handler using python-garminconnect."""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.constants.workout_types.garmin import get_unified_workout_type
from app.database import DbSession
from app.repositories.event_record_repository import EventRecordRepository
from app.repositories.user_connection_repository import UserConnectionRepository
from app.schemas.model_crud.activities import (
    EventRecordCreate,
    EventRecordDetailCreate,
    EventRecordMetrics,
)
from app.services.event_record_service import event_record_service
from app.services.providers.garmin_connect.client import GarminConnectClient
from app.services.providers.templates.base_workouts import BaseWorkoutsTemplate
from app.utils.structured_logging import log_structured


class GarminConnectWorkouts(BaseWorkoutsTemplate):
    """Workouts handler that fetches data via python-garminconnect."""

    def __init__(
        self,
        workout_repo: EventRecordRepository,
        connection_repo: UserConnectionRepository,
        provider_name: str,
        api_base_url: str,
        client: GarminConnectClient,
    ) -> None:
        # oauth=None because garmin_connect bypasses OAuth entirely
        super().__init__(  # type: ignore[call-arg]
            workout_repo=workout_repo,
            connection_repo=connection_repo,
            provider_name=provider_name,
            api_base_url=api_base_url,
            oauth=None,
        )
        self.client = client

    # -------------------------------------------------------------------------
    # API access
    # -------------------------------------------------------------------------

    def get_workouts(
        self,
        db: DbSession,
        user_id: UUID,
        start_date: datetime,
        end_date: datetime,
    ) -> list[Any]:
        """Fetch activities from Garmin Connect for the given date range."""
        try:
            return self.client.get_activities_by_date(start_date.date(), end_date.date())
        except Exception as exc:
            log_structured(
                self.logger,
                "error",
                "Error fetching Garmin Connect activities",
                action="garmin_connect_activities_fetch_error",
                error=str(exc),
                user_id=str(user_id),
            )
            raise

    def get_workouts_from_api(self, db: DbSession, user_id: UUID, **kwargs: Any) -> Any:
        start_date_str = kwargs.get("start_date") or kwargs.get("startDate")
        end_date_str = kwargs.get("end_date") or kwargs.get("endDate")

        start_dt = (
            datetime.fromisoformat(start_date_str.replace("Z", "+00:00"))
            if start_date_str
            else datetime.now(timezone.utc)
        )
        end_dt = (
            datetime.fromisoformat(end_date_str.replace("Z", "+00:00")) if end_date_str else datetime.now(timezone.utc)
        )
        return self.client.get_activities_by_date(start_dt.date(), end_dt.date())

    # -------------------------------------------------------------------------
    # Normalization helpers
    # -------------------------------------------------------------------------

    def _extract_dates(self, start_timestamp: Any, end_timestamp: Any) -> tuple[datetime, datetime]:
        """Parse ISO datetime strings as returned by garminconnect."""

        def _parse(val: Any) -> datetime:
            if isinstance(val, datetime):
                return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
            raw = str(val).replace("Z", "+00:00").replace(" ", "T")
            dt = datetime.fromisoformat(raw)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

        return _parse(start_timestamp), _parse(end_timestamp)

    def _build_metrics(self, raw: dict[str, Any]) -> EventRecordMetrics:
        metrics: EventRecordMetrics = {}

        avg_hr = raw.get("averageHR")
        max_hr = raw.get("maxHR")
        calories = raw.get("calories")
        distance = raw.get("distance")
        steps = raw.get("steps")
        moving_duration = raw.get("movingDuration") or raw.get("duration")
        elev_gain = raw.get("elevationGain")
        elev_loss = raw.get("elevationLoss")
        avg_speed = raw.get("averageSpeed")
        max_speed = raw.get("maxSpeed")
        avg_watts = raw.get("avgPower")
        max_watts = raw.get("maxPower")

        if avg_hr is not None:
            metrics["heart_rate_avg"] = Decimal(str(avg_hr))
        if max_hr is not None:
            metrics["heart_rate_max"] = int(max_hr)
        if calories is not None:
            metrics["energy_burned"] = Decimal(str(calories))
        if distance is not None:
            metrics["distance"] = Decimal(str(distance))
        if steps is not None:
            metrics["steps_count"] = int(steps)
        if moving_duration is not None:
            metrics["moving_time_seconds"] = int(moving_duration)
        if elev_gain is not None:
            metrics["total_elevation_gain"] = Decimal(str(elev_gain))
        if elev_gain is not None and elev_loss is not None:
            # store elev_high/low as gain/loss proxy (no absolute elevation in Connect API)
            metrics["elev_high"] = Decimal(str(elev_gain))
            metrics["elev_low"] = Decimal(str(elev_loss))
        if avg_speed is not None:
            metrics["average_speed"] = Decimal(str(avg_speed))
        if max_speed is not None:
            metrics["max_speed"] = Decimal(str(max_speed))
        if avg_watts is not None:
            metrics["average_watts"] = Decimal(str(avg_watts))
        if max_watts is not None:
            metrics["max_watts"] = Decimal(str(max_watts))

        return metrics

    def _normalize_workout(
        self,
        raw_workout: Any,
        user_id: UUID,
    ) -> tuple[EventRecordCreate, EventRecordDetailCreate]:
        raw: dict[str, Any] = raw_workout if isinstance(raw_workout, dict) else {}

        workout_id = uuid4()

        # garminconnect uses lowercase typeKey ("running", "cycling", etc.)
        # get_unified_workout_type() already uppercases, so we can pass as-is
        type_key: str = ""
        activity_type = raw.get("activityType")
        if isinstance(activity_type, dict):
            type_key = activity_type.get("typeKey", "")
        elif isinstance(activity_type, str):
            type_key = activity_type
        workout_type = get_unified_workout_type(type_key)

        # Prefer UTC start time; fall back to local time
        start_raw = raw.get("startTimeGMT") or raw.get("startTimeLocal")
        duration_s = int(raw.get("duration") or raw.get("elapsedDuration") or 0)

        if start_raw:
            start_dt, _ = self._extract_dates(start_raw, start_raw)
        else:
            start_dt = datetime.now(timezone.utc)

        end_dt = start_dt.__class__.fromtimestamp(start_dt.timestamp() + duration_s, tz=timezone.utc)

        metrics = self._build_metrics(raw)

        record = EventRecordCreate(
            id=workout_id,
            category="workout",
            type=workout_type.value,
            source_name="Garmin Connect",
            device_model=raw.get("deviceName"),
            duration_seconds=duration_s,
            start_datetime=start_dt,
            end_datetime=end_dt,
            external_id=str(raw.get("activityId")) if raw.get("activityId") is not None else None,
            source=self.provider_name,
            user_id=user_id,
        )

        detail = EventRecordDetailCreate(record_id=workout_id, **metrics)

        return record, detail

    def _build_bundles(
        self,
        raw_list: list[Any],
        user_id: UUID,
    ) -> list[tuple[EventRecordCreate, EventRecordDetailCreate]]:
        bundles = []
        for raw in raw_list:
            try:
                bundles.append(self._normalize_workout(raw, user_id))
            except Exception as exc:
                log_structured(
                    self.logger,
                    "warning",
                    "Failed to normalize Garmin Connect activity",
                    action="garmin_connect_normalize_error",
                    error=str(exc),
                    user_id=str(user_id),
                )
        return bundles

    # -------------------------------------------------------------------------
    # Load
    # -------------------------------------------------------------------------

    def load_data(self, db: DbSession, user_id: UUID, **kwargs: Any) -> int:
        from datetime import timedelta  # noqa: PLC0415

        start = kwargs.get("start") or kwargs.get("start_date")
        end = kwargs.get("end") or kwargs.get("end_date")

        if not start:
            start_dt = datetime.now(timezone.utc) - timedelta(days=30)
        elif isinstance(start, datetime):
            start_dt = start
        else:
            start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))

        if not end:
            end_dt = datetime.now(timezone.utc)
        elif isinstance(end, datetime):
            end_dt = end
        else:
            end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))

        raw_activities = self.get_workouts(db, user_id, start_dt, end_dt)

        count = 0
        for record, detail in self._build_bundles(raw_activities, user_id):
            try:
                event_record_service.create_workout_with_detail(db, record, detail)
                count += 1
            except Exception as exc:
                db.rollback()
                log_structured(
                    self.logger,
                    "warning",
                    "Failed to save Garmin Connect activity, skipping",
                    action="garmin_connect_save_error",
                    error=str(exc),
                    user_id=str(user_id),
                )

        return count
