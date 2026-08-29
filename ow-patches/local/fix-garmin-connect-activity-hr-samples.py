# patch_id:        fix-garmin-connect-activity-hr-samples
# upstream_file:   backend/app/services/providers/garmin_connect/workouts.py, backend/app/services/providers/garmin_connect/client.py
# upstream_symbol: GarminConnectWorkouts.load_data + GarminConnectClient.get_activity_details
# retire_when:     GarminConnectWorkouts.load_data calls a per-activity HR-detail endpoint and persists per-second (or sub-minute) heart_rate samples for each workout. Marker: presence of `get_activity_details` (or `activityDetailMetrics`) in backend/app/services/providers/garmin_connect/.

"""Pull per-sample workout metrics for each Garmin Connect activity.

Scope note (2026-08-29): this began as HR-only and now persists the same eight
series the official Garmin webhook provider does — see
garmin_connect/coverage.py::ACTIVITY_SAMPLE_SERIES, which mirrors
garmin/coverage.py one-for-one. All eight arrive in the SAME activity-details
response that was already being fetched for HR, so the extra series cost no
additional requests. Ingestion is now gated on settings.ingest_workout_samples,
matching garmin and strava; previously this provider ignored that flag.

Bug
---
The daily HR endpoint (`Garmin.get_heart_rates`) used by GarminConnect247Data
returns ~2-minute samples. During a workout the watch records HR every second,
but those samples are only retrievable via the *activity details* endpoint.
Result: API consumers see a 2-min-resolution chart of HR during workouts and
the max never quite hits the watch's true peak (e.g. 179 vs Garmin-reported 186
for a Sun May 3 trail run).

Fix
---
After saving each workout, pull `client.get_activity_details(activity_id)`
which returns a `metricDescriptors` array (mapping metric name → column index)
and an `activityDetailMetrics` array of per-sample rows. Extract the HR and
timestamp columns and bulk-insert as additional `heart_rate` time-series
samples on the same `garmin_connect` data source. The repository's
`ON CONFLICT DO NOTHING (data_source_id, series_type, recorded_at)` upsert
handles any minute-boundary collisions with the existing daily-HR rows.

Scope guards
------------
- Skip workouts without HR (averageHR is None or 0) — strength sessions, etc.
- Skip workouts under 5 minutes — incidental "activities" aren't worth a
  second API call.
- Cap `maxchart` at 10000 so a 2-hour workout still gets ~1-sample/second.
- Catch and log per-activity errors so one bad activity doesn't poison the
  whole sync.

Side effect
-----------
Per-activity samples coexist with daily-HR samples (different timestamps in
practice; identical-timestamp collisions are absorbed by the upsert). Mixed
aggregations (avg/max/min) end up dominated by the higher-resolution stream
during workouts, which is the intended outcome.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.config import settings
from app.database import DbSession
from app.schemas.model_crud.activities import TimeSeriesSampleCreate
from app.services.providers.garmin_connect.coverage import (
    ACTIVITY_SAMPLE_SERIES,
    ACTIVITY_SAMPLE_TIMESTAMP_KEYS,
)
from app.utils.structured_logging import log_structured

# Activity must have HR + this many seconds of duration before we make the
# extra API call. Five minutes filters out the "5-second walk to the mailbox"
# style records Garmin sometimes generates.
_MIN_DURATION_SECONDS = 300

# Per-second resolution for activities up to ~2h45m; anything longer gets
# (gracefully) downsampled by the Garmin endpoint.
_MAXCHART = 10000

# Which columns to persist now lives in garmin_connect/coverage.py, mirroring
# garmin/coverage.py::ACTIVITY_SAMPLE_SERIES so both Garmin providers agree on
# the contents of a workout sample set.
_HR_KEYS = ("directHeartRate", "HEART_RATE")

# Legacy descriptor-key variants Garmin has returned over the years, folded onto
# the canonical key used in ACTIVITY_SAMPLE_SERIES. Without this, a payload that
# names the column HEART_RATE resolves to no HR index at all and the workout HR
# samples this patch exists to capture are silently dropped.
_KEY_ALIASES: dict[str, str] = {"HEART_RATE": "directHeartRate"}


def _client_get_activity_details(self, activity_id: int | str) -> dict[str, Any]:
    """Wrapper added to GarminConnectClient.

    The underlying garminconnect call accepts (activity_id, maxchart, maxpoly)
    — we only care about HR samples, so let polylines default and bump maxchart
    high enough that a long activity still yields per-second data.
    """
    result = self._call_with_reauth("get_activity_details", activity_id, _MAXCHART)
    return result if isinstance(result, dict) else {}


def _extract_metric_indices(
    metric_descriptors: list[dict[str, Any]],
) -> tuple[dict[str, int], int | None]:
    """Return ({descriptor key: column index}, timestamp index).

    Garmin returns a metricDescriptors block mapping each metric name to its
    position in every activityDetailMetrics row, so the indices must be resolved
    per activity rather than assumed — different activity types expose different
    columns (no power on a walk, no GPS on a treadmill run).
    """
    wanted = {key for key, _ in ACTIVITY_SAMPLE_SERIES}
    found: dict[str, int] = {}
    ts_idx: int | None = None
    for desc in metric_descriptors:
        key = (desc.get("key") or desc.get("metricKey") or "").strip()
        key = _KEY_ALIASES.get(key, key)
        idx = desc.get("metricsIndex")
        if not key or idx is None:
            continue
        if key in wanted and key not in found:
            found[key] = int(idx)
        elif ts_idx is None and key in ACTIVITY_SAMPLE_TIMESTAMP_KEYS:
            ts_idx = int(idx)
    return found, ts_idx


def _save_activity_hr_samples(
    self,
    db: DbSession,
    user_id: UUID,
    raw_activity: dict[str, Any],
) -> int:
    """Fetch per-second HR for one workout and persist as TimeSeriesSampleCreate rows.

    Returns the number of samples persisted (0 if skipped or no HR data).
    """
    from app.services.timeseries_service import timeseries_service  # noqa: PLC0415

    # Gate on the same platform-wide flag garmin and strava honour. This
    # provider previously ingested workout samples unconditionally, which is why
    # it accumulated ~214k rows from 94 activities while the flag it was
    # ignoring defaults to False and is documented as "significantly increases
    # DB storage".
    if not settings.ingest_workout_samples:
        return 0

    activity_id = raw_activity.get("activityId")
    avg_hr = raw_activity.get("averageHR")
    duration = int(raw_activity.get("duration") or 0)

    if not activity_id or avg_hr is None or avg_hr <= 0 or duration < _MIN_DURATION_SECONDS:
        return 0

    try:
        details = self.client.get_activity_details(activity_id)
    except Exception as exc:
        log_structured(
            self.logger,
            "warning",
            "Failed to fetch Garmin Connect activity details for HR samples",
            action="garmin_connect_activity_details_error",
            activity_id=str(activity_id),
            error=str(exc),
            user_id=str(user_id),
        )
        return 0

    metric_descriptors = details.get("metricDescriptors") or []
    activity_metrics = details.get("activityDetailMetrics") or []
    if not metric_descriptors or not activity_metrics:
        return 0

    metric_idx, ts_idx = _extract_metric_indices(metric_descriptors)
    if not metric_idx or ts_idx is None:
        return 0

    device_model = self.client.get_last_used_device_model()
    series_by_key = dict(ACTIVITY_SAMPLE_SERIES)

    samples: list[TimeSeriesSampleCreate] = []
    for entry in activity_metrics:
        metrics = entry.get("metrics") or []
        if ts_idx >= len(metrics):
            continue
        epoch_ms = metrics[ts_idx]
        if epoch_ms is None:
            continue
        try:
            recorded_at = datetime.fromtimestamp(float(epoch_ms) / 1000.0, tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            continue

        for key, col in metric_idx.items():
            if col >= len(metrics):
                continue
            value = metrics[col]
            if value is None:
                continue
            # Heart rate keeps its >0 guard (0 bpm is a dropout, not a reading).
            # Latitude, longitude, elevation and air temperature are legitimately
            # negative or zero, so a blanket >0 filter would silently drop the
            # southern hemisphere, sea level and freezing conditions.
            if key in _HR_KEYS and value <= 0:
                continue
            try:
                samples.append(
                    TimeSeriesSampleCreate(
                        id=uuid4(),
                        user_id=user_id,
                        source=self.provider_name,
                        # The DataSource identity is (user_id, device_model, source),
                        # so omitting this would file these workout samples under a
                        # device-less data_source row, split from every other
                        # garmin_connect row. Cached on the client — one request per
                        # sync run, not per activity.
                        device_model=device_model,
                        recorded_at=recorded_at,
                        value=Decimal(str(value)),
                        series_type=series_by_key[key],
                        external_id=str(activity_id),
                    )
                )
            except (TypeError, ValueError, OverflowError):
                continue

    if samples:
        try:
            timeseries_service.bulk_create_samples(db, samples)
        except Exception as exc:
            log_structured(
                self.logger,
                "warning",
                "Failed to bulk-insert Garmin Connect activity HR samples",
                action="garmin_connect_activity_hr_save_error",
                activity_id=str(activity_id),
                sample_count=len(samples),
                error=str(exc),
                user_id=str(user_id),
            )
            return 0
    return len(samples)


def load_data(self, db: DbSession, user_id: UUID, **kwargs: Any) -> int:
    """Patched load_data: saves workouts AND per-activity HR samples."""
    from datetime import timedelta  # noqa: PLC0415

    from app.services.event_record_service import event_record_service  # noqa: PLC0415

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
    bundles = self._build_bundles(raw_activities, user_id)
    # _build_bundles may drop entries that fail to normalize — pair raw with the
    # corresponding bundle by activity_id so we don't fetch HR for a phantom workout.
    bundles_by_activity_id: dict[str, tuple] = {}
    for raw, bundle in zip(raw_activities, bundles):
        if isinstance(raw, dict) and raw.get("activityId") is not None:
            bundles_by_activity_id[str(raw["activityId"])] = (raw, bundle)
    # Fall back: if we couldn't pair (different lengths or missing ids), use index alignment.
    if len(bundles_by_activity_id) != len(bundles):
        ordered = list(zip(raw_activities, bundles))
    else:
        ordered = list(bundles_by_activity_id.values())

    for raw, (record, detail) in ordered:
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
            continue

        try:
            saved = self._save_activity_hr_samples(db, user_id, raw)
            if saved:
                log_structured(
                    self.logger,
                    "info",
                    "Saved per-activity HR samples",
                    action="garmin_connect_activity_hr_saved",
                    activity_id=str(raw.get("activityId")),
                    sample_count=saved,
                    user_id=str(user_id),
                )
        except Exception as exc:
            log_structured(
                self.logger,
                "warning",
                "Per-activity HR fetch failed; workout summary already saved",
                action="garmin_connect_activity_hr_unhandled",
                activity_id=str(raw.get("activityId")) if isinstance(raw, dict) else None,
                error=str(exc),
                user_id=str(user_id),
            )

    return count


def install() -> None:
    """Add get_activity_details to the client; replace workouts.load_data."""
    import sys  # noqa: PLC0415
    import app.services.providers.garmin_connect.client  # noqa: F401, PLC0415
    import app.services.providers.garmin_connect.workouts  # noqa: F401, PLC0415

    client_module = sys.modules["app.services.providers.garmin_connect.client"]
    workouts_module = sys.modules["app.services.providers.garmin_connect.workouts"]

    client_module.GarminConnectClient.get_activity_details = _client_get_activity_details
    workouts_module.GarminConnectWorkouts._save_activity_hr_samples = _save_activity_hr_samples
    workouts_module.GarminConnectWorkouts.load_data = load_data
