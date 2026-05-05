# patch_id:        fix-sleep-summary-utc-bucketing
# upstream_file:   backend/app/repositories/event_record_repository.py
# upstream_symbol: EventRecordRepository.get_sleep_summaries
# retire_when:     get_sleep_summaries' `local_sleep_date` falls back to the user's IANA timezone (or any non-UTC source) when zone_offset is null. Marker: presence of `user.timezone`-aware bucketing in get_sleep_summaries.

"""Bucket sleep summaries by `(end_datetime AT TIME ZONE user.timezone)::date`
when the row's `zone_offset` is NULL.

Bug
---
Upstream's `get_sleep_summaries` defines:

    local_sleep_date = cast(
        EventRecord.end_datetime + cast(func.coalesce(EventRecord.zone_offset, "+00:00"), Interval),
        Date,
    )

When `zone_offset` is NULL on the row, this falls back to UTC, which is wrong
for users east of UTC. Concrete example (Brisbane, UTC+10):

    sleep started 2026-05-03 21:54 Brisbane = 2026-05-03 11:54 UTC
    sleep ended   2026-05-04 06:11 Brisbane = 2026-05-03 20:11 UTC
    zone_offset:  NULL  (Garmin Connect / Ultrahuman both leave it null)
    upstream buckets to: 2026-05-03 (UTC)   <-- the day BEFORE the wake date
    correct bucket:      2026-05-04 (Brisbane wake date)

Result: the most recent night's sleep card lands on the previous day, every
sleep summary is shifted one local day earlier, and the "today" bar on the
chart appears empty because no UTC sleep ended on that day yet.

Fix
---
Resolve the user's IANA timezone once at the top of the function and use it as
the fallback for the local-date calculation:

    local_sleep_date = cast(
        coalesce(
            end_datetime + zone_offset::interval,         -- when offset is set, honor it
            timezone(<user_tz>, end_datetime),            -- otherwise use the user's tz
        ),
        Date,
    )

When `user.timezone` is also null we fall through to UTC — preserves upstream
behavior so disabling this patch is safe.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import (
    UUID as SQL_UUID,
    Date,
    Integer,
    Interval,
    String,
    and_,
    asc,
    case,
    cast,
    desc,
    func,
    literal,
    tuple_,
)

from app.database import DbSession
from app.models import DataSource, EventRecord, SleepDetails
from app.utils.pagination import decode_cursor


def _resolve_user_timezone(db_session: DbSession, user_id: UUID) -> str:
    """Return the user's IANA timezone or 'UTC' so the fallback matches upstream."""
    from app.models import User  # noqa: PLC0415

    tz = db_session.query(User.timezone).filter(User.id == user_id).scalar()
    return tz or "UTC"


def get_sleep_summaries(
    self,
    db_session: DbSession,
    user_id: UUID,
    start_date: datetime,
    end_date: datetime,
    cursor: str | None,
    limit: int,
) -> list[dict]:
    """Daily sleep summaries bucketed by user-local wake date.

    Mirrors upstream behaviour exactly except for the `local_sleep_date`
    expression — which now falls back to the user's IANA timezone when
    `zone_offset` is null on the row.
    """
    user_tz = _resolve_user_timezone(db_session, user_id)

    is_main_sleep = func.coalesce(SleepDetails.is_nap, False) == False  # noqa: E712

    # Local calendar date the session ended (wake-up date).
    #
    # Order of preference:
    #   1. If `zone_offset` is set on the row, honor it (the device sent a
    #      specific UTC offset — typically the most authoritative).
    #   2. Otherwise convert end_datetime via the user's IANA timezone.
    #   3. If that's also null/unset, the convert resolves to UTC because
    #      _resolve_user_timezone returns 'UTC' as fallback.
    local_sleep_date = cast(
        func.coalesce(
            EventRecord.end_datetime + cast(EventRecord.zone_offset, Interval),
            func.timezone(literal(user_tz), EventRecord.end_datetime),
        ),
        Date,
    )

    subquery = (
        db_session.query(
            local_sleep_date.label("sleep_date"),
            func.min(case((is_main_sleep, EventRecord.start_datetime), else_=None)).label("min_start_time"),
            func.max(case((is_main_sleep, EventRecord.end_datetime), else_=None)).label("max_end_time"),
            func.sum(
                case(
                    (
                        is_main_sleep,
                        func.coalesce(
                            SleepDetails.sleep_total_duration_minutes * 60,
                            EventRecord.duration_seconds,
                            0,
                        ),
                    ),
                    else_=0,
                )
            ).label("total_duration"),
            DataSource.source,
            DataSource.device_model,
            func.min(cast(EventRecord.id, String)).label("record_id_text"),
            func.sum(case((is_main_sleep, SleepDetails.sleep_time_in_bed_minutes), else_=None)).label(
                "time_in_bed_minutes"
            ),
            func.sum(case((is_main_sleep, SleepDetails.sleep_deep_minutes), else_=None)).label("deep_minutes"),
            func.sum(case((is_main_sleep, SleepDetails.sleep_light_minutes), else_=None)).label("light_minutes"),
            func.sum(case((is_main_sleep, SleepDetails.sleep_rem_minutes), else_=None)).label("rem_minutes"),
            func.sum(case((is_main_sleep, SleepDetails.sleep_awake_minutes), else_=None)).label("awake_minutes"),
            func.sum(
                case(
                    (is_main_sleep, SleepDetails.sleep_efficiency_score * EventRecord.duration_seconds),
                    else_=None,
                )
            ).label("efficiency_weighted_sum"),
            func.sum(
                case(
                    (
                        and_(is_main_sleep, SleepDetails.sleep_efficiency_score != None),  # noqa: E711
                        EventRecord.duration_seconds,
                    ),
                    else_=0,
                )
            ).label("efficiency_duration_sum"),
            func.sum(cast(SleepDetails.is_nap == True, Integer)).label("nap_count"),  # noqa: E712
            func.sum(
                case((SleepDetails.is_nap == True, EventRecord.duration_seconds), else_=0)  # noqa: E712
            ).label("nap_duration"),
        )
        .join(DataSource, EventRecord.data_source_id == DataSource.id)
        .outerjoin(SleepDetails, SleepDetails.record_id == EventRecord.id)
        .filter(
            DataSource.user_id == user_id,
            EventRecord.category == "sleep",
            EventRecord.end_datetime >= start_date - timedelta(days=1),
            local_sleep_date >= cast(start_date, Date),
            local_sleep_date < cast(end_date, Date),
        )
        .group_by(local_sleep_date, DataSource.source, DataSource.device_model)
    ).subquery()

    record_id_col = cast(subquery.c.record_id_text, SQL_UUID).label("record_id")
    query = db_session.query(
        subquery.c.sleep_date,
        subquery.c.min_start_time,
        subquery.c.max_end_time,
        subquery.c.total_duration,
        subquery.c.source,
        subquery.c.device_model,
        record_id_col,
        subquery.c.time_in_bed_minutes,
        subquery.c.deep_minutes,
        subquery.c.light_minutes,
        subquery.c.rem_minutes,
        subquery.c.awake_minutes,
        subquery.c.efficiency_weighted_sum,
        subquery.c.efficiency_duration_sum,
        subquery.c.nap_count,
        subquery.c.nap_duration,
    )

    if cursor:
        cursor_ts, cursor_id, direction = decode_cursor(cursor)
        cursor_date = cursor_ts.date()

        if direction == "prev":
            query = query.filter(tuple_(subquery.c.sleep_date, record_id_col) < (cursor_date, cursor_id))
            query = query.order_by(desc(subquery.c.sleep_date), desc(record_id_col))
        else:
            query = query.filter(tuple_(subquery.c.sleep_date, record_id_col) > (cursor_date, cursor_id))
            query = query.order_by(asc(subquery.c.sleep_date), asc(record_id_col))
    else:
        query = query.order_by(asc(subquery.c.sleep_date), asc(record_id_col))

    results = query.limit(limit + 1).all()

    summaries: list[dict] = []
    for row in results:
        efficiency_percent = None
        if row.efficiency_duration_sum and row.efficiency_duration_sum > 0:
            efficiency_percent = float(row.efficiency_weighted_sum) / float(row.efficiency_duration_sum)

        summaries.append(
            {
                "sleep_date": row.sleep_date,
                "min_start_time": row.min_start_time,
                "max_end_time": row.max_end_time,
                "total_duration_minutes": int(row.total_duration or 0) // 60,
                "source": row.source,
                "device_model": row.device_model,
                "record_id": row.record_id,
                "time_in_bed_minutes": int(row.time_in_bed_minutes) if row.time_in_bed_minutes is not None else None,
                "deep_minutes": int(row.deep_minutes) if row.deep_minutes is not None else None,
                "light_minutes": int(row.light_minutes) if row.light_minutes is not None else None,
                "rem_minutes": int(row.rem_minutes) if row.rem_minutes is not None else None,
                "awake_minutes": int(row.awake_minutes) if row.awake_minutes is not None else None,
                "efficiency_percent": efficiency_percent,
                "nap_count": int(row.nap_count) if row.nap_count is not None else None,
                "nap_duration_minutes": int(row.nap_duration) // 60 if row.nap_duration is not None else None,
            }
        )
    return summaries


def install() -> None:
    """Replace EventRecordRepository.get_sleep_summaries with the patched version."""
    import sys  # noqa: PLC0415
    import app.repositories.event_record_repository  # noqa: F401, PLC0415

    repo_module = sys.modules["app.repositories.event_record_repository"]
    repo_module.EventRecordRepository.get_sleep_summaries = get_sleep_summaries
