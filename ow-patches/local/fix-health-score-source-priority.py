# patch_id:        fix-health-score-source-priority
# upstream_file:   backend/app/repositories/health_score_repository.py
# upstream_symbol: HealthScoreRepository.get_with_filters
# retire_when:     get_with_filters returns at most one health score per (local-date, category) when multiple providers have sleep/recovery records for the same night, OR upstream offers an explicit dedupe option that consumers can pass. Marker: presence of `provider_order` (or any provider-priority lookup) inside HealthScoreRepository.

"""Dedupe internal health scores by provider priority.

Bug
---
`fill_missing_sleep_scores_task` (and friends) compute one **internal** sleep
score per underlying sleep `EventRecord`. When the user has both Garmin and
Ultrahuman sleep records for the same night (a typical multi-device setup),
two `HealthScore` rows get persisted for that night — both with
`provider='internal'`, but each tied to a different `sleep_record_id` whose
underlying source is different (`garmin_connect` vs `ultrahuman`).

The dashboard therefore renders two scores per date, which is confusing and
defeats the point of provider priority elsewhere in the app.

Fix
---
At read time, dedupe `(local_date_in_user_tz, category)` keeping the score
whose underlying sleep record has the highest-priority source. Falls back
to keeping all rows when:
  - The caller filters by a specific provider (no dedup needed).
  - A score has no `sleep_record_id` (e.g. resilience scores) — those are
    independent of source priority.

Pagination is applied AFTER dedup so `total_count` reflects what the
consumer actually sees.

Behaviour preservation
----------------------
When `user.timezone` is null we fall back to UTC date for the dedup key,
which mirrors the rest of the codebase's UTC-fallback convention. When
`provider` is filtered, this patch is a no-op.
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, desc

from app.database import DbSession
from app.models import DataSource, EventRecord, HealthScore, ProviderPriority
from app.repositories.provider_priority_repository import ProviderPriorityRepository
from app.schemas.enums import ProviderName
from app.schemas.model_crud.activities import HealthScoreQueryParams


def _resolve_user_timezone(db_session: DbSession, user_id: UUID) -> str:
    from app.models import User  # noqa: PLC0415

    tz = db_session.query(User.timezone).filter(User.id == user_id).scalar()
    return tz or "UTC"


def get_with_filters(
    self,
    db_session: DbSession,
    user_id: UUID,
    params: HealthScoreQueryParams,
) -> tuple[list[HealthScore], int]:
    """Return health scores deduped by source priority per (local-date, category)."""
    filters = [HealthScore.user_id == user_id]
    if params.category:
        filters.append(HealthScore.category == params.category)
    if params.provider:
        filters.append(HealthScore.provider == params.provider)
    if params.data_source_id:
        filters.append(HealthScore.data_source_id == params.data_source_id)
    if params.start_datetime:
        filters.append(HealthScore.recorded_at >= params.start_datetime)
    if params.end_datetime:
        filters.append(HealthScore.recorded_at < params.end_datetime)

    # Pull all matching rows plus the underlying sleep record's provider AND
    # source. `provider` is the canonical ProviderName enum column (added
    # upstream in #1414); `source` is free-form (str_100) and holds values like
    # "apple_health_sdk" or "com.apple.health.<UUID>". Ranking on `source` alone
    # meant strict ProviderName() raised for every non-canonical value, so every
    # row scored 99 and the dedup winner was arbitrary. Outer joins so
    # resilience / non-sleep scores (no sleep_record_id) still appear.
    rows: list[tuple[HealthScore, str | None, str | None]] = (
        db_session.query(HealthScore, DataSource.provider, DataSource.source)
        .outerjoin(EventRecord, EventRecord.id == HealthScore.sleep_record_id)
        .outerjoin(DataSource, DataSource.id == EventRecord.data_source_id)
        .filter(and_(*filters))
        .order_by(desc(HealthScore.recorded_at))
        .all()
    )

    # Caller filtered by provider — they explicitly want every row of that
    # provider; no priority dedup applies.
    if params.provider is not None:
        scores = [hs for hs, _prov, _src in rows]
        total = len(scores)
        return scores[params.offset : params.offset + params.limit], total

    # Build the local-date bucket key in the user's timezone.
    user_tz_name = _resolve_user_timezone(db_session, user_id)
    try:
        user_tz = ZoneInfo(user_tz_name)
    except Exception:
        user_tz = ZoneInfo("UTC")

    provider_order = ProviderPriorityRepository(ProviderPriority).get_priority_order(db_session)

    def _priority_index(provider_col: str | None, source: str | None) -> int:
        """Rank a row's provider, mirroring upstream's _filter_by_priority.

        Prefer the canonical `provider` column; fall back to parsing the
        free-form `source` tolerantly (ProviderName.from_source_string) rather
        than with the strict constructor, which raises on anything that is not
        already an exact enum value.
        """
        raw = provider_col or source
        if not raw:
            return 99  # unknown provider loses to anything ranked
        try:
            provider = ProviderName(raw)
        except ValueError:
            provider = ProviderName.from_source_string(raw)
        return provider_order.get(provider, 99)

    def _local_date(recorded_at, tz):  # noqa: ANN001, ANN202
        """Return the local calendar date for a HealthScore.recorded_at.

        fill_missing_sleep_scores_task writes an ALREADY-LOCAL datetime wearing a
        UTC label::

            recorded_at=local_end_by_id[record_id].replace(tzinfo=timezone.utc)

        where local_end_datetime = end_datetime + COALESCE(zone_offset,'+00:00').
        So .date() on it is already the local sleep date; calling .astimezone(tz)
        first would apply the offset a second time and shift a night backwards for
        negative offsets (and forwards past 14:00 local for +10). Naive datetimes
        are converted, since those did not come from that writer.
        """
        if recorded_at.tzinfo is None:
            return recorded_at.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz).date()
        return recorded_at.date()

    # Group by (local_date, category). Resilience/recovery scores without a
    # sleep_record_id pass through untouched — they don't dedupe.
    groups: dict[tuple, list[tuple[HealthScore, str | None, str | None]]] = defaultdict(list)
    untracked: list[HealthScore] = []
    for hs, prov, src in rows:
        if hs.sleep_record_id is None:
            untracked.append(hs)
            continue
        local_date = _local_date(hs.recorded_at, user_tz)
        groups[(local_date, hs.category)].append((hs, prov, src))

    deduped: list[HealthScore] = list(untracked)
    for entries in groups.values():
        entries.sort(key=lambda e: (_priority_index(e[1], e[2]), str(e[0].id)))
        deduped.append(entries[0][0])

    # Re-establish the recorded_at DESC ordering the route expects.
    deduped.sort(key=lambda s: s.recorded_at, reverse=True)

    total_count = len(deduped)
    paginated = deduped[params.offset : params.offset + params.limit]
    return paginated, total_count


def install() -> None:
    """Replace HealthScoreRepository.get_with_filters with the priority-deduped version."""
    import sys  # noqa: PLC0415
    import app.repositories.health_score_repository  # noqa: F401, PLC0415

    repo_module = sys.modules["app.repositories.health_score_repository"]
    repo_module.HealthScoreRepository.get_with_filters = get_with_filters
