# patch_id:        fix-hrv-nightly-aggregate
# status:          RETIRED (upstream commit 09b7b0a, merged 2026-06-07)
# upstream_file:   backend/app/services/summaries_service.py
# upstream_symbol: SummariesService.get_sleep_summaries
# retire_when:     get_sleep_summaries response includes avg_hrv_sdnn_ms as a non-null float when intraday SDNN samples exist within the sleep window.
#
# RETIRED — kept for institutional memory only. Disabled in apply.py
# (PATCHES_ENABLED["fix-hrv-nightly-aggregate"] = False) and no longer loaded or
# composed. Upstream now populates avg_hrv_sdnn_ms / avg_hrv_rmssd_ms /
# avg_respiratory_rate / avg_spo2_percent in get_sleep_summaries itself. The
# install() below is dead code; do not re-enable without first confirming
# upstream no longer owns these fields.

"""Populate avg_hrv_sdnn_ms / avg_respiratory_rate / avg_spo2_percent on the
SleepSummary response by averaging the relevant SeriesType samples over each
sleep session's window padded by ±30 min. Upstream had these as TODOs returning
None.

Co-located with fix-sleep-stages-missing (always-emit stages object) and
fix-sleep-timezone (per-record local datetimes) since they share the same
function. We compose all three into the same patched implementation when their
flags are enabled — see apply.py.
"""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.database import DbSession
from app.schemas.enums import SeriesType
from app.schemas.responses.activity import SleepStagesSummary, SleepSummary
from app.schemas.utils import (
    PaginatedResponse,
    Pagination,
    SourceMetadata,
    TimeseriesMetadata,
)
from app.utils.exceptions import handle_exceptions
from app.utils.pagination import encode_cursor
from app.utils.structured_logging import log_structured

# Sleep physiological metrics averaged over each session.
SLEEP_PHYSIO_SERIES_TYPES = [
    SeriesType.heart_rate,
    SeriesType.heart_rate_variability_sdnn,
    SeriesType.respiratory_rate,
    SeriesType.oxygen_saturation,
]

# Pad the window so wind-down + wake samples count.
SLEEP_PHYSIO_WINDOW_PAD = timedelta(minutes=30)


def _to_local(dt: datetime | None, tz_name: str | None) -> datetime | None:
    """Render a UTC datetime in the given IANA timezone, or None on bad input."""
    if dt is None or not tz_name:
        return None
    try:
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        return dt.astimezone(ZoneInfo(tz_name))
    except Exception:
        return None


@handle_exceptions
def get_sleep_summaries(
    self,
    db_session: DbSession,
    user_id: UUID,
    start_date: datetime,
    end_date: datetime,
    cursor: str | None,
    limit: int,
) -> PaginatedResponse[SleepSummary]:
    """Get daily sleep summaries aggregated by date, provider, and device."""
    self.logger.debug(f"Fetching sleep summaries for user {user_id} from {start_date} to {end_date}")
    user = self.user_repo.get(db_session, user_id)
    user_tz: str | None = getattr(user, "timezone", None) if user else None

    results = self.event_record_repo.get_sleep_summaries(db_session, user_id, start_date, end_date, cursor, limit)
    results = self._filter_by_priority(db_session, user_id, results, date_key="sleep_date")

    has_more = len(results) > limit
    if has_more:
        results = results[:limit]

    next_cursor: str | None = None
    previous_cursor: str | None = None

    if results:
        last_result = results[-1]
        last_date = last_result["sleep_date"]
        last_id = last_result["record_id"]
        last_date_midnight = datetime.combine(last_date, datetime.min.time()).replace(tzinfo=timezone.utc)
        if has_more:
            next_cursor = encode_cursor(last_date_midnight, last_id, "next")

        if cursor:
            first_result = results[0]
            first_date = first_result["sleep_date"]
            first_id = first_result["record_id"]
            first_date_midnight = datetime.combine(first_date, datetime.min.time()).replace(tzinfo=timezone.utc)
            previous_cursor = encode_cursor(first_date_midnight, first_id, "prev")

    data = []
    for result in results:
        # Always emit stages so consumers can distinguish "source doesn't track stages"
        # (null fields) from "feature not implemented" (object missing). This subsumes
        # the fix-sleep-stages-missing patch when it's enabled.
        stages = SleepStagesSummary(
            deep_minutes=result.get("deep_minutes"),
            light_minutes=result.get("light_minutes"),
            rem_minutes=result.get("rem_minutes"),
            awake_minutes=result.get("awake_minutes"),
        )

        avg_hr: int | None = None
        avg_hrv_sdnn_ms: float | None = None
        avg_respiratory_rate: float | None = None
        avg_spo2_percent: float | None = None

        sleep_start = result.get("min_start_time")
        sleep_end = result.get("max_end_time")
        if sleep_start and sleep_end:
            try:
                physio_averages = self.data_point_repo.get_averages_for_time_range(
                    db_session,
                    user_id,
                    sleep_start - SLEEP_PHYSIO_WINDOW_PAD,
                    sleep_end + SLEEP_PHYSIO_WINDOW_PAD,
                    SLEEP_PHYSIO_SERIES_TYPES,
                )
                hr_avg = physio_averages.get(SeriesType.heart_rate)
                avg_hr = int(round(hr_avg)) if hr_avg is not None else None
                hrv_avg = physio_averages.get(SeriesType.heart_rate_variability_sdnn)
                avg_hrv_sdnn_ms = round(hrv_avg, 1) if hrv_avg is not None else None
                rr_avg = physio_averages.get(SeriesType.respiratory_rate)
                avg_respiratory_rate = round(rr_avg, 1) if rr_avg is not None else None
                spo2_avg = physio_averages.get(SeriesType.oxygen_saturation)
                avg_spo2_percent = round(spo2_avg, 1) if spo2_avg is not None else None
            except Exception as e:
                log_structured(
                    self.logger,
                    "warning",
                    f"Failed to fetch sleep physiological metrics: {e}",
                    sleep_start=sleep_start,
                    sleep_end=sleep_end,
                )

        summary = SleepSummary(
            date=result["sleep_date"],
            source=SourceMetadata(provider=result["source"] or "unknown", device=result.get("device_model")),
            timezone=user_tz,
            start_time=result["min_start_time"],
            end_time=result["max_end_time"],
            start_time_local=_to_local(result["min_start_time"], user_tz),
            end_time_local=_to_local(result["max_end_time"], user_tz),
            duration_minutes=result["total_duration_minutes"],
            time_in_bed_minutes=result.get("time_in_bed_minutes"),
            efficiency_percent=result.get("efficiency_percent"),
            stages=stages,
            nap_count=result.get("nap_count"),
            nap_duration_minutes=result.get("nap_duration_minutes"),
            avg_heart_rate_bpm=avg_hr,
            avg_hrv_sdnn_ms=avg_hrv_sdnn_ms,
            avg_respiratory_rate=avg_respiratory_rate,
            avg_spo2_percent=avg_spo2_percent,
        )
        data.append(summary)

    return PaginatedResponse(
        data=data,
        pagination=Pagination(
            has_more=has_more,
            next_cursor=next_cursor,
            previous_cursor=previous_cursor,
        ),
        metadata=TimeseriesMetadata(
            sample_count=len(data),
            start_time=start_date,
            end_time=end_date,
        ),
    )


def install() -> None:
    """Replace SummariesService.get_sleep_summaries with the patched implementation."""
    import sys  # noqa: PLC0415
    import app.services.summaries_service  # noqa: F401, PLC0415  (ensures sys.modules entry)

    _module = sys.modules["app.services.summaries_service"]
    _module.SummariesService.get_sleep_summaries = get_sleep_summaries
    # Expose the constants on the module for any introspection code.
    _module.SLEEP_PHYSIO_SERIES_TYPES = SLEEP_PHYSIO_SERIES_TYPES
    _module.SLEEP_PHYSIO_WINDOW_PAD = SLEEP_PHYSIO_WINDOW_PAD
