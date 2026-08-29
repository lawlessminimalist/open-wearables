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

from collections.abc import Callable
from typing import Any
from uuid import UUID

from app.schemas.responses.activity import SleepStagesSummary, SleepSummary

# Upstream's unpatched Ultrahuman247Data.normalize_sleep, captured by install().
_upstream_normalize_sleep: Callable[..., dict[str, Any]] | None = None

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
    """Normalize Ultrahuman sleep, tolerating stage type-token and key variants.

    Delegates to upstream and overwrites ONLY the four aggregate stage counts.
    Upstream's strict lookup expects `deep_sleep`/`light_sleep`/`rem_sleep`/
    `awake` with a `stage_time` field; the live API has also been observed
    emitting `deep` / `Deep Sleep` and `duration`, which upstream maps to 0.

    Written as a WRAPPER rather than a body replacement on purpose. The previous
    wholesale copy shadowed upstream #1476, which added
    `"stage_timestamps": self._normalize_sleep_stages(raw_sleep)` to this dict —
    so granular per-stage timestamps were never persisted for Ultrahuman, with
    no error and no failing fork test. Delegating means every key upstream adds
    next is inherited for free instead of being silently dropped.
    """
    normalized = _upstream_normalize_sleep(self, raw_sleep, user_id)

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

    normalized["stages"] = {
        "deep_seconds": int(stage_seconds["deep"]),
        "light_seconds": int(stage_seconds["light"]),
        "rem_seconds": int(stage_seconds["rem"]),
        "awake_seconds": int(stage_seconds["awake"]),
    }
    return normalized


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
    global _upstream_normalize_sleep  # noqa: PLW0603

    from app.services.providers.ultrahuman.data_247 import Ultrahuman247Data

    # Capture upstream's bound function once so the wrapper can delegate. The
    # guard keeps install() idempotent — a second call must not capture our own
    # wrapper and recurse.
    if _upstream_normalize_sleep is None:
        _upstream_normalize_sleep = Ultrahuman247Data.normalize_sleep

    Ultrahuman247Data.normalize_sleep = normalize_sleep
