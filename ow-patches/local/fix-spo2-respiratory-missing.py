# patch_id:        fix-spo2-respiratory-missing
# upstream_file:   backend/app/services/providers/ultrahuman/data_247.py
# upstream_symbol: Ultrahuman247Data.normalize_activity_samples + Ultrahuman247Data._save_daily_activity
# retire_when:     get_timeseries response for ultrahuman provider returns at least one record with type=oxygen_saturation or type=respiratory_rate when the user has data for those metrics.

"""Wire Ultrahuman SpO2 and respiratory-rate samples through to canonical
SeriesType records.

Three coordinated changes:
  1. normalize_activity_samples (decorate) — upstream's normalizer runs
     unchanged; the wrapper adds the spo2/oxygen_saturation/blood_oxygen and
     respiratory_rate/breath_rate/breathing_rate/breath type tokens, collapsed
     into canonical "spo2" / "respiratory_rate" buckets.
  2. _save_daily_activity (wholesale-replace) — pass those types to the
     normalizer when present in the day's metric_data, fall back to the Sleep
     object's `spo2.value` (single nightly average) emitted at sleep midpoint
     when no intraday samples are returned, and pass `source=` on the
     DAILY_SCALAR_SERIES constructors.
  3. coverage.py — ACTIVITY_SAMPLE_SERIES gains "spo2" → oxygen_saturation and
     "respiratory_rate" → respiratory_rate so _build_activity_samples persists
     the new buckets and TIMESERIES advertises them on the coverage tab.
     (Source edit, structural.)

Rebased 2026-10-19 onto upstream d18304f8. Upstream #1501 (970bbfae) rewrote
load_and_save_all to return a Sync247Result built via Sync247Run and moved the
per-day persist into `_save_daily_activity`. The old copy of load_and_save_all
returned a dict, which broke the sync task (`results.summary()`/.outcomes) and
shadowed upstream's DAILY_SCALAR_SERIES (sleep_rhr, temperature_deviation,
active_minutes → exercise_time). The patch no longer replaces
load_and_save_all at all: upstream's runs unshadowed and calls the
`_save_daily_activity` below.

Rebased 2026-10-31 onto upstream 8adb8d0b. #1931 (4ff8f487) gave
_save_daily_activity a `day: date | None = None` parameter, which
load_and_save_all now passes positionally, and stamps the daily scalar rows with
`period=CalendarPeriod.local_day(recorded_at)` so they reach the aggregate tables
the 1day/1week/1month timeseries and summaries rollup read. The four-arg copy
raised TypeError on every day, swallowed by Sync247Run.step, so every Ultrahuman
activity_samples step would have failed after the merge.

Composes with fix-hrv-source-unknown, which owns _build_activity_samples
(adds `source=` to the intraday rows built from these buckets).
"""

from collections.abc import Callable
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.database import DbSession
from app.repositories.data_point_series_repository import WriteCounts
from app.schemas.enums import daily_total_flag
from app.schemas.model_crud.activities.data_point_series import CalendarPeriod, TimeSeriesSampleCreate
from app.services.providers.ultrahuman.coverage import DAILY_SCALAR_SERIES
from app.services.timeseries_service import timeseries_service

# Type tokens the Ultrahuman partner API has used for these metrics.
_SPO2_TYPES = ("spo2", "oxygen_saturation", "blood_oxygen")
_RESPIRATORY_TYPES = ("respiratory_rate", "breath_rate", "breathing_rate", "breath")

# Upstream's unpatched Ultrahuman247Data.normalize_activity_samples, captured by install().
_upstream_normalize_activity_samples: Callable[..., dict[str, list[dict[str, Any]]]] | None = None


def _bucket(
    provider: str,
    user_id: UUID,
    values: list[dict[str, Any]],
    unit: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for val in values:
        ts = val.get("timestamp")
        value = val.get("value")
        if ts is None or value is None:
            continue
        out.append(
            {
                "id": uuid4(),
                "user_id": user_id,
                "provider": provider,
                "recorded_at": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                "value": value,
                "unit": unit,
            }
        )
    return out


def normalize_activity_samples(
    self: Any,
    raw_samples: list[dict[str, Any]],
    user_id: UUID,
) -> dict[str, list[dict[str, Any]]]:
    """Upstream's normalizer, plus the SpO2 and respiratory-rate buckets."""
    assert _upstream_normalize_activity_samples is not None, "install() not called"
    result = _upstream_normalize_activity_samples(self, raw_samples, user_id)
    result.setdefault("spo2", [])
    result.setdefault("respiratory_rate", [])

    for sample in raw_samples:
        sample_type = sample.get("type")
        if sample_type in _SPO2_TYPES:
            result["spo2"].extend(_bucket(self.provider_name, user_id, sample.get("values", []), "percent"))
        elif sample_type in _RESPIRATORY_TYPES:
            result["respiratory_rate"].extend(_bucket(self.provider_name, user_id, sample.get("values", []), "brpm"))

    return result


def _save_daily_activity(
    self: Any,
    db: DbSession,
    user_id: UUID,
    items_by_type: dict[str, Any],
    day: date | None = None,
) -> WriteCounts:
    """Persist one day's activity series plus its single-value daily metrics.

    Upstream's body (8adb8d0b) with three FORK DELTAs marked inline.
    """
    # FORK DELTA 1: also pass any SpO2 / respiratory variants the API returns; the
    # normalizer collapses them into one canonical bucket each. Upstream's tuple
    # is ("hr", "hrv", "temp", "steps").
    sample_inputs = [
        {"type": t, "values": items_by_type[t].get("values", [])}
        for t in ("hr", "hrv", "temp", "steps", *_SPO2_TYPES, *_RESPIRATORY_TYPES)
        if t in items_by_type
    ]

    # FORK DELTA 2: the Sleep summary often carries a single nightly SpO2 average
    # ({"spo2": {"value": 97}}) when intraday samples aren't exposed. Emit it as one
    # sample at the sleep midpoint — only when no intraday SpO2 came back (before the
    # 2026-10-19 rebase it was emitted unconditionally, alongside intraday samples).
    sleep_obj = items_by_type.get("Sleep") or {}
    sleep_spo2 = (sleep_obj.get("spo2") or {}).get("value")
    bedtime_start = sleep_obj.get("bedtime_start")
    bedtime_end = sleep_obj.get("bedtime_end")
    has_intraday_spo2 = any(t in items_by_type for t in _SPO2_TYPES)
    if not has_intraday_spo2 and sleep_spo2 is not None and bedtime_start and bedtime_end:
        mid_ts = (int(bedtime_start) + int(bedtime_end)) // 2
        sample_inputs.append({"type": "spo2", "values": [{"timestamp": mid_ts, "value": sleep_spo2}]})

    samples: list[TimeSeriesSampleCreate] = []
    if sample_inputs:
        samples.extend(self._build_activity_samples(user_id, self.normalize_activity_samples(sample_inputs, user_id)))

    for key, series_type in DAILY_SCALAR_SERIES.items():
        scalar = items_by_type.get(key) or {}
        value, day_start = scalar.get("value"), scalar.get("day_start_timestamp")
        if value is None or not day_start:
            continue
        # day_start is the local midnight as Ultrahuman reports it, stored as the UTC instant; the day row
        # takes the same instant, so it and the sample read as one day everywhere.
        recorded_at = datetime.fromtimestamp(day_start, tz=timezone.utc)
        samples.append(
            TimeSeriesSampleCreate(
                id=uuid4(),
                user_id=user_id,
                provider=self.provider_name,
                # FORK DELTA 3: upstream omits source=. The DataSource identity is
                # (user_id, device_model, source), so a None source mints a SECOND
                # data_source row and splits Ultrahuman history (FORK.md §3.6).
                source=self.provider_name,
                recorded_at=recorded_at,
                value=Decimal(str(value)),
                series_type=series_type,
                is_daily_total=daily_total_flag(series_type, True),
                period=CalendarPeriod.local_day(recorded_at),
            )
        )

    if not samples:
        return WriteCounts(0, 0)
    return timeseries_service.bulk_create_samples(db, samples)


def install() -> None:
    """Decorate the Ultrahuman normalizer and replace the per-day persist step."""
    global _upstream_normalize_activity_samples  # noqa: PLW0603

    from app.services.providers.ultrahuman.data_247 import Ultrahuman247Data

    # Capture upstream once; the guard keeps install() idempotent so a second call
    # cannot capture our own wrapper and recurse.
    if _upstream_normalize_activity_samples is None:
        _upstream_normalize_activity_samples = Ultrahuman247Data.normalize_activity_samples

    Ultrahuman247Data._SPO2_TYPES = _SPO2_TYPES
    Ultrahuman247Data._RESPIRATORY_TYPES = _RESPIRATORY_TYPES
    Ultrahuman247Data.normalize_activity_samples = normalize_activity_samples
    Ultrahuman247Data._save_daily_activity = _save_daily_activity
