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

When `user.timezone` is also null we fall through to UTC (``_resolve_user_timezone``
returns 'UTC') — preserves upstream behavior so disabling this patch is safe.

Scope
-----
These are WHOLESALE method replacements, so they must track upstream's current
bodies verbatim except for the single `local_sleep_date` delta above. In
particular they preserve upstream's:
  * the aggregates physiology path (#1931-#1941): ``_get_sleep_sessions`` returns
    ``(sessions_by_key, main_sleeps_by_key)`` and ``get_sleep_summaries`` merges
    ``self._sleep_physiology(...)`` (event_record_aggregate, then own-device
    samples, provider day series_aggregate, other devices) into each night,
    replacing the old #1259 LATERAL over data_point_series,
  * #1257 provider grouping + per-session `sessions` breakdown attached via
    ``self._get_sleep_sessions``,
  * #1845 paging by night: the required ``provider_order`` / ``device_type_order``
    arguments, the ``source_rank`` row_number() window (partitioned by
    ``local_sleep_date``, so it inherits the fork's bucketing) and the
    ``source_rank == 1`` filter applied before the cursor/limit.

Rebased 2026-10-19 onto upstream d18304f8; the previous copy lacked #1845's two
positional arguments, so SummariesService's 8-argument call raised TypeError.

Rebased 2026-10-31 onto upstream 8adb8d0b. #1930 renamed DataPointSeries to
SeriesSample, so the old copy's `from app.models import DataPointSeries` raised
ImportError and, because apply_patches() re-raises at app import, took the whole
backend down. The physio LATERAL was also stale against the aggregates rewrite.

Both ``get_sleep_summaries`` AND ``_get_sleep_sessions`` are replaced with the
identical `local_sleep_date` fallback, so the summary key (user-tz date) and the
per-session key stay in lock-step even when zone_offset is NULL. Without patching
both, NULL-zone_offset providers (Garmin Connect / Ultrahuman) in a non-UTC user
timezone would key their summary a day apart from their sessions and get an empty
`sessions` list.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import UUID as SQL_UUID
from sqlalchemy import Date, Integer, Interval, String, and_, asc, case, cast, desc, func, literal, tuple_

from app.database import DbSession
from app.models import DataSource, EventRecord, SleepDetails
from app.repositories.repositories import source_priority_order
from app.schemas.enums import DeviceType, EventCategory, ProviderName
from app.utils.pagination import decode_cursor


def _upstream_sleep_physiology_keys() -> dict:
    """Upstream's private _SLEEP_PHYSIOLOGY, looked up at call time.

    A module-level import of a private upstream name turns an upstream rename into an
    ImportError at app boot (apply_patches re-raises), the failure the 2026-10-31 rebase
    fixed for DataPointSeries. Looked up lazily, a rename fails only the request, and
    the sleep-summary tests catch it.
    """
    import sys  # noqa: PLC0415

    return sys.modules["app.repositories.event_record_repository"]._SLEEP_PHYSIOLOGY


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
    provider_order: dict[ProviderName, int],
    device_type_order: dict[DeviceType, int],
) -> list[dict]:
    """Get daily sleep summaries: per date, the aggregate of its highest-priority source.

    The source is picked before pagination, so a page holds `limit` dates however many
    sources recorded each night.

    Returns list of dicts with keys:
    - sleep_date, min_start_time, max_end_time, total_duration_minutes
    - provider, source, device_model, device_type, record_id
    - time_in_bed_minutes, efficiency_percent
    - deep_minutes, light_minutes, rem_minutes, awake_minutes
    - nap_count, nap_duration_minutes
    """
    # Helper: condition for "is NOT a nap" (main sleep)
    # is_nap can be True, False, or NULL - we treat NULL as "not a nap"
    is_main_sleep = func.coalesce(SleepDetails.is_nap, False) == False  # noqa: E712

    # Local calendar date the session ended (wake-up date) — mirrors score
    # date logic in fill_missing_sleep_scores_task so chart, score, and
    # session list all key on the same date.
    # FORK DELTA: a NULL zone_offset reads as the user's IANA timezone, not UTC
    # (UTC only when user.timezone is also unset, i.e. upstream behaviour).
    user_tz = _resolve_user_timezone(db_session, user_id)
    local_sleep_date = cast(
        func.coalesce(
            EventRecord.end_datetime + cast(EventRecord.zone_offset, Interval),
            func.timezone(literal(user_tz), EventRecord.end_datetime),
        ),
        Date,
    )

    # Build base aggregated query as subquery
    # Join with SleepDetails to get sleep stage data
    # Cast UUID to text for min() since PostgreSQL doesn't support min() on UUID directly
    subquery = (
        db_session.query(
            local_sleep_date.label("sleep_date"),
            # Main sleep times (exclude naps)
            func.min(case((is_main_sleep, EventRecord.start_datetime), else_=None)).label("min_start_time"),
            func.max(case((is_main_sleep, EventRecord.end_datetime), else_=None)).label("max_end_time"),
            # Main sleep duration (exclude naps) — prefer net sleep time over
            # wall-clock duration.  Oura (and some other providers) store
            # time_in_bed in duration_seconds; sleep_total_duration_minutes
            # holds the actual sleep time and should be used when available.
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
            DataSource.provider,
            DataSource.source,
            DataSource.device_model,
            # Functionally dependent on the three columns above (uq_data_source_identity),
            # so grouping by it as well cannot change the number of groups.
            DataSource.device_type,
            func.min(cast(EventRecord.id, String)).label("record_id_text"),
            func.row_number()
            .over(
                partition_by=local_sleep_date,
                order_by=(
                    *source_priority_order(provider_order, device_type_order),
                    func.min(cast(EventRecord.id, String)),
                ),
            )
            .label("source_rank"),
            # Sleep details aggregations - main sleep only (minutes stored, convert to seconds later)
            func.sum(case((is_main_sleep, SleepDetails.sleep_time_in_bed_minutes), else_=None)).label(
                "time_in_bed_minutes"
            ),
            func.sum(case((is_main_sleep, SleepDetails.sleep_deep_minutes), else_=None)).label("deep_minutes"),
            func.sum(case((is_main_sleep, SleepDetails.sleep_light_minutes), else_=None)).label("light_minutes"),
            func.sum(case((is_main_sleep, SleepDetails.sleep_rem_minutes), else_=None)).label("rem_minutes"),
            func.sum(case((is_main_sleep, SleepDetails.sleep_awake_minutes), else_=None)).label("awake_minutes"),
            # Weighted average for efficiency - main sleep only (weight by duration)
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
            # Nap aggregations
            func.sum(
                cast(SleepDetails.is_nap == True, Integer)  # noqa: E712
            ).label("nap_count"),
            func.sum(
                case((SleepDetails.is_nap == True, EventRecord.duration_seconds), else_=0)  # noqa: E712
            ).label("nap_duration"),
        )
        .join(DataSource, EventRecord.data_source_id == DataSource.id)
        .outerjoin(SleepDetails, SleepDetails.record_id == EventRecord.id)
        .filter(
            DataSource.user_id == user_id,
            EventRecord.category == EventCategory.SLEEP,
            EventRecord.end_datetime >= start_date - timedelta(days=1),
            local_sleep_date >= cast(start_date, Date),
            local_sleep_date < cast(end_date, Date),
        )
        .group_by(
            local_sleep_date,
            DataSource.provider,
            DataSource.source,
            DataSource.device_model,
            DataSource.device_type,
        )
    ).subquery()

    # Build main query from subquery, casting record_id back to UUID
    record_id_col = cast(subquery.c.record_id_text, SQL_UUID).label("record_id")
    query = db_session.query(
        subquery.c.sleep_date,
        subquery.c.min_start_time,
        subquery.c.max_end_time,
        subquery.c.total_duration,
        subquery.c.provider,
        subquery.c.source,
        subquery.c.device_model,
        subquery.c.device_type,
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
    query = query.filter(subquery.c.source_rank == 1)

    # Handle cursor pagination
    if cursor:
        cursor_ts, cursor_id, direction = decode_cursor(cursor)
        cursor_date = cursor_ts.date()

        if direction == "prev":
            # Backward pagination: get items BEFORE cursor
            query = query.filter(tuple_(subquery.c.sleep_date, record_id_col) < (cursor_date, cursor_id))
            query = query.order_by(desc(subquery.c.sleep_date), desc(record_id_col))
        else:
            # Forward pagination: get items AFTER cursor
            query = query.filter(tuple_(subquery.c.sleep_date, record_id_col) > (cursor_date, cursor_id))
            query = query.order_by(asc(subquery.c.sleep_date), asc(record_id_col))
    else:
        # No cursor: default ordering
        query = query.order_by(asc(subquery.c.sleep_date), asc(record_id_col))

    # Limit + 1 to check for has_more
    results = query.limit(limit + 1).all()

    # Transform results to dict format
    summaries = []
    for row in results:
        # Calculate weighted average efficiency
        efficiency_percent = None
        if row.efficiency_duration_sum and row.efficiency_duration_sum > 0:
            efficiency_percent = float(row.efficiency_weighted_sum) / float(row.efficiency_duration_sum)

        summaries.append(
            {
                "sleep_date": row.sleep_date,
                "min_start_time": row.min_start_time,
                "max_end_time": row.max_end_time,
                "total_duration_minutes": int(row.total_duration or 0) // 60,
                "provider": row.provider,
                "source": row.source,
                "device_model": row.device_model,
                "device_type": row.device_type,
                "record_id": row.record_id,
                "time_in_bed_minutes": int(row.time_in_bed_minutes)
                if row.time_in_bed_minutes is not None
                else None,
                "deep_minutes": int(row.deep_minutes) if row.deep_minutes is not None else None,
                "light_minutes": int(row.light_minutes) if row.light_minutes is not None else None,
                "rem_minutes": int(row.rem_minutes) if row.rem_minutes is not None else None,
                "awake_minutes": int(row.awake_minutes) if row.awake_minutes is not None else None,
                "efficiency_percent": efficiency_percent,
                # Nap tracking
                "nap_count": int(row.nap_count) if row.nap_count is not None else None,
                "nap_duration_minutes": int(row.nap_duration) // 60 if row.nap_duration is not None else None,
            }
        )

    # Attach per-session breakdown (individual sleep/nap records) for each summary,
    # keyed by the same (date, provider, source, device_model) grouping identity.
    sessions_by_key, main_sleeps_by_key = self._get_sleep_sessions(db_session, user_id, start_date, end_date)
    # Only this page's nights are read from the aggregate table, not every night in the range.
    page_keys = {(s["sleep_date"], s["provider"], s["source"], s["device_model"]) for s in summaries}
    physiology = self._sleep_physiology(
        db_session, {key: sleeps for key, sleeps in main_sleeps_by_key.items() if key in page_keys}
    )
    for summary in summaries:
        key = (
            summary["sleep_date"],
            summary["provider"],
            summary["source"],
            summary["device_model"],
        )
        summary["sessions"] = sessions_by_key.get(key, [])
        summary.update(physiology.get(key, dict.fromkeys(_upstream_sleep_physiology_keys())))

    return summaries


def _get_sleep_sessions(
    self,
    db_session: DbSession,
    user_id: UUID,
    start_date: datetime,
    end_date: datetime,
) -> tuple[dict[tuple, list[dict]], dict[tuple, list[tuple[UUID, int]]]]:
    """Get individual sleep/nap sessions keyed by (sleep_date, provider, source, device_model),
    and per key the main sleeps' record ids with their durations.

    Mirrors the date filter and grouping identity of ``get_sleep_summaries`` but returns one
    entry per underlying EventRecord instead of collapsing them. Per-session duration prefers
    net sleep time (SleepDetails.sleep_total_duration_minutes) and falls back to wall-clock
    duration, matching the aggregate duration logic. Sessions are sorted by start time.
    """
    # FORK DELTA: a NULL zone_offset reads as the user's IANA timezone, not UTC
    # (UTC only when user.timezone is also unset, i.e. upstream behaviour).
    user_tz = _resolve_user_timezone(db_session, user_id)
    local_sleep_date = cast(
        func.coalesce(
            EventRecord.end_datetime + cast(EventRecord.zone_offset, Interval),
            func.timezone(literal(user_tz), EventRecord.end_datetime),
        ),
        Date,
    )
    is_nap_expr = func.coalesce(SleepDetails.is_nap, False)
    duration_seconds = case(
        (is_nap_expr, EventRecord.duration_seconds),
        else_=func.coalesce(
            SleepDetails.sleep_total_duration_minutes * 60,
            EventRecord.duration_seconds,
            0,
        ),
    )

    rows = (
        db_session.query(
            local_sleep_date.label("sleep_date"),
            EventRecord.id.label("record_id"),
            EventRecord.start_datetime.label("start_time"),
            EventRecord.end_datetime.label("end_time"),
            EventRecord.zone_offset.label("zone_offset"),
            duration_seconds.label("duration_seconds"),
            func.coalesce(SleepDetails.is_nap, False).label("is_nap"),
            DataSource.provider,
            DataSource.source,
            DataSource.device_model,
        )
        .join(DataSource, EventRecord.data_source_id == DataSource.id)
        .outerjoin(SleepDetails, SleepDetails.record_id == EventRecord.id)
        .filter(
            DataSource.user_id == user_id,
            EventRecord.category == EventCategory.SLEEP,
            EventRecord.end_datetime >= start_date - timedelta(days=1),
            local_sleep_date >= cast(start_date, Date),
            local_sleep_date < cast(end_date, Date),
        )
        .order_by(local_sleep_date.asc(), EventRecord.start_datetime.asc())
        .all()
    )

    sessions_by_key: dict[tuple, list[dict]] = {}
    main_sleeps_by_key: dict[tuple, list[tuple[UUID, int]]] = {}
    for row in rows:
        key = (row.sleep_date, row.provider, row.source, row.device_model)
        if not row.is_nap:
            main_sleeps_by_key.setdefault(key, []).append((row.record_id, int(row.duration_seconds or 0)))
        sessions_by_key.setdefault(key, []).append(
            {
                "start_time": row.start_time,
                "end_time": row.end_time,
                "zone_offset": row.zone_offset,
                "duration_minutes": int(row.duration_seconds) // 60 if row.duration_seconds is not None else None,
                "is_nap": bool(row.is_nap),
            }
        )
    return sessions_by_key, main_sleeps_by_key


def install() -> None:
    """Replace get_sleep_summaries and _get_sleep_sessions with the patched versions."""
    import sys  # noqa: PLC0415

    import app.repositories.event_record_repository  # noqa: F401, PLC0415

    repo_module = sys.modules["app.repositories.event_record_repository"]
    repo_module.EventRecordRepository.get_sleep_summaries = get_sleep_summaries
    repo_module.EventRecordRepository._get_sleep_sessions = _get_sleep_sessions
