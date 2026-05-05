# patch_id:        fix-spo2-respiratory-missing
# upstream_file:   backend/app/services/providers/ultrahuman/data_247.py
# upstream_symbol: Ultrahuman247Data.normalize_activity_samples + Ultrahuman247Data.load_and_save_all
# retire_when:     get_timeseries response for ultrahuman provider returns at least one record with type=oxygen_saturation or type=respiratory_rate when the user has data for those metrics.

"""Wire Ultrahuman SpO2 and respiratory-rate samples through to canonical
SeriesType records.

Two coordinated changes:
  1. normalize_activity_samples — accept the spo2/oxygen_saturation/blood_oxygen
     and respiratory_rate/breath_rate/breathing_rate/breath type tokens.
  2. load_and_save_all — pass those types to the normalizer when present in
     the day's metric_data, and fall back to the Sleep object's `spo2.value`
     (single nightly average) emitted at sleep midpoint when no intraday
     samples are returned.

This patch composes with fix-hrv-source-unknown via apply.py — both target the
same class, but different methods.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException

from app.database import DbSession
from app.schemas.enums.series_types import SeriesType
from app.schemas.model_crud.activities.data_point_series import TimeSeriesSampleCreate

# Type tokens the Ultrahuman partner API has used for these metrics.
_SPO2_TYPES = ("spo2", "oxygen_saturation", "blood_oxygen")
_RESPIRATORY_TYPES = ("respiratory_rate", "breath_rate", "breathing_rate", "breath")


def normalize_activity_samples(
    self,
    raw_samples: list[dict[str, Any]],
    user_id: UUID,
) -> dict[str, list[dict[str, Any]]]:
    """Normalize activity samples into categorized data, including SpO2 and respiratory rate."""
    result: dict[str, list[dict[str, Any]]] = {
        "heart_rate": [],
        "hrv": [],
        "temperature": [],
        "steps": [],
        "spo2": [],
        "respiratory_rate": [],
    }

    for sample in raw_samples:
        sample_type = sample.get("type")

        if sample_type == "hr":
            for val in sample.get("values", []):
                ts = val.get("timestamp")
                recorded_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None
                if recorded_at:
                    result["heart_rate"].append({
                        "id": uuid4(),
                        "user_id": user_id,
                        "provider": self.provider_name,
                        "recorded_at": recorded_at,
                        "value": val.get("value"),
                        "unit": "bpm",
                    })

        elif sample_type == "hrv":
            for val in sample.get("values", []):
                ts = val.get("timestamp")
                recorded_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None
                if recorded_at:
                    result["hrv"].append({
                        "id": uuid4(),
                        "user_id": user_id,
                        "provider": self.provider_name,
                        "recorded_at": recorded_at,
                        "value": val.get("value"),
                        "unit": "ms",
                    })

        elif sample_type == "temp":
            for val in sample.get("values", []):
                ts = val.get("timestamp")
                recorded_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None
                if recorded_at:
                    result["temperature"].append({
                        "id": uuid4(),
                        "user_id": user_id,
                        "provider": self.provider_name,
                        "recorded_at": recorded_at,
                        "value": val.get("value"),
                        "unit": "celsius",
                    })

        elif sample_type == "steps":
            for val in sample.get("values", []):
                ts = val.get("timestamp")
                steps_val = val.get("value")
                recorded_at = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat() if ts else None
                if recorded_at and steps_val and steps_val > 0:
                    result["steps"].append({
                        "id": uuid4(),
                        "user_id": user_id,
                        "provider": self.provider_name,
                        "recorded_at": recorded_at,
                        "value": steps_val,
                        "unit": "count",
                    })

        elif sample_type in _SPO2_TYPES:
            for val in sample.get("values", []):
                ts = val.get("timestamp")
                spo2_val = val.get("value")
                if ts is None or spo2_val is None:
                    continue
                result["spo2"].append({
                    "id": uuid4(),
                    "user_id": user_id,
                    "provider": self.provider_name,
                    "recorded_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    "value": spo2_val,
                    "unit": "percent",
                })

        elif sample_type in _RESPIRATORY_TYPES:
            for val in sample.get("values", []):
                ts = val.get("timestamp")
                rr_val = val.get("value")
                if ts is None or rr_val is None:
                    continue
                result["respiratory_rate"].append({
                    "id": uuid4(),
                    "user_id": user_id,
                    "provider": self.provider_name,
                    "recorded_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    "value": rr_val,
                    "unit": "brpm",
                })

    return result


def load_and_save_all(
    self,
    db: DbSession,
    user_id: UUID,
    start_time: datetime | str | None = None,
    end_time: datetime | str | None = None,
    is_first_sync: bool = False,
) -> dict[str, Any]:
    """Patched daily fetch loop that includes SpO2 and respiratory rate.

    Identical to upstream's loop except for the intraday_types list and the
    Sleep-object SpO2 fallback near the bottom.
    """
    if isinstance(start_time, str):
        start_time = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    if isinstance(end_time, str):
        end_time = datetime.fromisoformat(end_time.replace("Z", "+00:00"))

    if end_time is None:
        end_time = datetime.now(timezone.utc)
    if start_time is None:
        start_time = end_time - timedelta(days=30)

    results: dict[str, Any] = {
        "sleep_sessions_synced": 0,
        "activity_samples": 0,
        "recovery_days_synced": 0,
        "failed_days": 0,
        "errors": [],
    }

    current_date = datetime.combine(start_time.date(), datetime.min.time(), tzinfo=timezone.utc)
    end_date = datetime.combine(end_time.date(), datetime.min.time(), tzinfo=timezone.utc)
    while current_date <= end_date:
        date_str = current_date.strftime("%Y-%m-%d")
        day_error = None

        try:
            metrics_list = self._fetch_daily_metrics(db, user_id, current_date)

            items_by_type = {}
            for item in metrics_list:
                t = item.get("type")
                if t and "object" in item:
                    items_by_type[t] = item["object"]

            if "Sleep" in items_by_type:
                try:
                    normalized_sleep = self.normalize_sleep(items_by_type["Sleep"], user_id)
                    if self.save_sleep_data(db, user_id, normalized_sleep):
                        results["sleep_sessions_synced"] += 1
                except Exception as e:
                    day_error = f"Sleep processing failed: {e}"

            try:
                # Pass any of the SpO2 / respiratory variants the API may return;
                # the normalizer collapses them into a single canonical bucket.
                sample_inputs = []
                intraday_types = ["hr", "hrv", "temp", "steps", *_SPO2_TYPES, *_RESPIRATORY_TYPES]
                for t in intraday_types:
                    if t in items_by_type:
                        sample_inputs.append({"type": t, "values": items_by_type[t].get("values", [])})

                # Sleep summary often carries a single nightly SpO2 average
                # ({"spo2": {"value": 97}}) when intraday samples aren't exposed.
                # Emit it as one sample at the sleep midpoint.
                sleep_obj = items_by_type.get("Sleep")
                if sleep_obj:
                    sleep_spo2 = (sleep_obj.get("spo2") or {}).get("value")
                    bedtime_start = sleep_obj.get("bedtime_start")
                    bedtime_end = sleep_obj.get("bedtime_end")
                    if sleep_spo2 is not None and bedtime_start and bedtime_end:
                        mid_ts = (int(bedtime_start) + int(bedtime_end)) // 2
                        sample_inputs.append(
                            {"type": "spo2", "values": [{"timestamp": mid_ts, "value": sleep_spo2}]}
                        )

                if sample_inputs:
                    normalized_samples = self.normalize_activity_samples(sample_inputs, user_id)
                    saved_count = self.save_activity_samples(db, user_id, normalized_samples)
                    results["activity_samples"] += saved_count

                if "vo2_max" in items_by_type:
                    vo2_obj = items_by_type["vo2_max"]
                    vo2_value = vo2_obj.get("value")
                    vo2_ts = vo2_obj.get("day_start_timestamp")
                    if vo2_value and vo2_ts:
                        recorded_at = datetime.fromtimestamp(vo2_ts, tz=timezone.utc)
                        ts_sample = TimeSeriesSampleCreate(
                            id=uuid4(),
                            user_id=user_id,
                            source=self.provider_name,
                            recorded_at=recorded_at,
                            value=Decimal(str(vo2_value)),
                            series_type=SeriesType.vo2_max,
                        )
                        self.data_point_repo.create(db, ts_sample)
                        results["activity_samples"] += 1
            except Exception as e:
                day_error = f"Activity samples processing failed: {e}"

        except HTTPException:
            raise

        except Exception as e:
            day_error = f"Unexpected error: {e}"

        if day_error:
            results["failed_days"] += 1
            results["errors"].append({"date": date_str, "error": day_error})

        current_date += timedelta(days=1)

    return results


def install() -> None:
    """Apply both Ultrahuman normalizer + load loop changes."""
    from app.services.providers.ultrahuman.data_247 import Ultrahuman247Data

    Ultrahuman247Data._SPO2_TYPES = _SPO2_TYPES
    Ultrahuman247Data._RESPIRATORY_TYPES = _RESPIRATORY_TYPES
    Ultrahuman247Data.normalize_activity_samples = normalize_activity_samples
    Ultrahuman247Data.load_and_save_all = load_and_save_all
