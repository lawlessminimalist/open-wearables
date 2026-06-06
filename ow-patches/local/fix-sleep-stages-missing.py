# patch_id:        fix-sleep-stages-missing
# upstream_file:   backend/app/services/providers/ultrahuman/data_247.py, backend/app/services/summaries_service.py
# upstream_symbol: Ultrahuman247Data.normalize_sleep + SummariesService.get_sleep_summaries
# retire_when:     get_sleep_summary response.data[*].stages is always an object (never null/missing) when sleep records exist, AND ultrahuman sleep stages parse correctly when upstream returns them with the canonical type tokens.

"""Make Ultrahuman sleep-stage parsing robust to API key/casing variants and
always emit the SleepStagesSummary object on responses.

Two coordinated changes:
  1. normalize_sleep — accept deep / Deep Sleep / deep_sleep alike, and either
     stage_time or duration as the seconds field. Upstream's strict
     `deep_sleep` lookup silently maps real-world responses to 0.
  2. get_sleep_summaries — always construct SleepStagesSummary (with null
     fields if no data) instead of returning null when no stage data exists,
     so consumers can distinguish "source doesn't track stages" from
     "feature not implemented in this fork".

Since upstream (commit 09b7b0a) now owns the body of get_sleep_summaries,
the (2) change is delivered by apply.py wrapping upstream's method and calling
ensure_stages_object() on each summary — see _compose_sleep_summaries().
"""

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from app.schemas.responses.activity import SleepStagesSummary, SleepSummary

# Stage aliases — accept any reasonable spelling.
_STAGE_ALIASES: dict[str, str] = {
    "deep": "deep",
    "deep_sleep": "deep",
    "light": "light",
    "light_sleep": "light",
    "rem": "rem",
    "rem_sleep": "rem",
    "awake": "awake",
    "wake": "awake",
}


def normalize_sleep(self, raw_sleep: dict[str, Any], user_id: UUID) -> dict[str, Any]:
    """Normalize Ultrahuman sleep payload into our schema, robust to type-token variants."""
    bedtime_start_ts = raw_sleep.get("bedtime_start")
    bedtime_end_ts = raw_sleep.get("bedtime_end")
    date_str = raw_sleep.get("ultrahuman_date")

    start_dt = None
    end_dt = None
    if bedtime_start_ts:
        start_dt = datetime.fromtimestamp(bedtime_start_ts, tz=timezone.utc)
    if bedtime_end_ts:
        end_dt = datetime.fromtimestamp(bedtime_end_ts, tz=timezone.utc)

    quick_metrics = {
        m.get("type"): m.get("value", 0) for m in raw_sleep.get("quick_metrics", [])
    }
    time_in_bed_seconds = quick_metrics.get("time_in_bed", 0) or 0

    # Robust stage parsing — accept deep / deep_sleep / Deep Sleep equivalently,
    # and either `stage_time` or `duration` as the seconds field.
    stage_seconds: dict[str, int] = {"deep": 0, "light": 0, "rem": 0, "awake": 0}
    for entry in raw_sleep.get("sleep_stages", []) or []:
        raw_type = entry.get("type")
        if not raw_type:
            continue
        canonical = _STAGE_ALIASES.get(str(raw_type).strip().lower())
        if not canonical:
            continue
        seconds = entry.get("stage_time") or entry.get("duration") or 0
        try:
            stage_seconds[canonical] += int(seconds)
        except (TypeError, ValueError):
            continue

    deep_seconds = stage_seconds["deep"]
    light_seconds = stage_seconds["light"]
    rem_seconds = stage_seconds["rem"]
    awake_seconds = stage_seconds["awake"]

    efficiency = quick_metrics.get("sleep_efic")
    if efficiency is None:
        efficiency = raw_sleep.get("sleep_efficiency")

    internal_id = uuid4()

    return {
        "id": internal_id,
        "user_id": user_id,
        "provider": self.provider_name,
        "timestamp": start_dt.isoformat() if start_dt else date_str,
        "start_time": start_dt,
        "end_time": end_dt,
        "duration_seconds": time_in_bed_seconds,
        "efficiency_percent": float(efficiency) if efficiency is not None else None,
        "is_nap": False,
        "stages": {
            "deep_seconds": int(deep_seconds),
            "light_seconds": int(light_seconds),
            "rem_seconds": int(rem_seconds),
            "awake_seconds": int(awake_seconds),
        },
        "ultrahuman_date": date_str,
        "raw": raw_sleep,
    }


def ensure_stages_object(summary: SleepSummary) -> None:
    """Always emit a SleepStagesSummary object on the response.

    Upstream returns stages=None when a sleep record has no stage data; we emit
    an all-null SleepStagesSummary instead so consumers can distinguish "source
    doesn't track stages" from "feature not implemented". Operates in place on
    the summary returned by upstream's get_sleep_summaries.
    """
    if summary.stages is None:
        summary.stages = SleepStagesSummary(
            deep_minutes=None,
            light_minutes=None,
            rem_minutes=None,
            awake_minutes=None,
        )


def install() -> None:
    """Patch the Ultrahuman normalizer. The summary-side change ('always emit stages')
    is delivered by apply.py's _compose_sleep_summaries() wrapping upstream's
    get_sleep_summaries and calling ensure_stages_object().
    """
    from app.services.providers.ultrahuman.data_247 import Ultrahuman247Data

    Ultrahuman247Data.normalize_sleep = normalize_sleep
