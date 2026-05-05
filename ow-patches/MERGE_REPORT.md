# Merge Report: dhlaw-patches-baseline-func vs upstream

**Upstream ref:** `the-momentum/open-wearables` main
**Assessed:** 2026-05-16
**Upstream commits ahead:** 16

---

## Summary

| Patch | Status | Action |
|---|---|---|
| `fix-activity-summary-utc-bucketing` | **CONFLICT** | Rewrite — layer `user.timezone` fallback over upstream's `zone_offset` approach |
| `frontend-display-timezone` | **CSS CONFLICT** | No logic conflict; replace hardcoded `zinc-*` classes with upstream CSS-var equivalents |
| `fix-sleep-summary-utc-bucketing` | KEEP | Upstream fixed workout bucketing but did NOT touch `get_sleep_summaries` |
| `fix-health-score-source-priority` | KEEP | Upstream adds write-path recompute; read-path dedup still needed for legacy rows |
| `fix-hrv-nightly-aggregate` | KEEP | Not addressed upstream; upstream SDNN disable (46bd6d7) is in a different service |
| `fix-active-minutes-broken` | KEEP | Not addressed upstream |
| `fix-pace-null` | KEEP | Not addressed upstream |
| `fix-calories-total-mislabelled` | KEEP | Not addressed upstream |
| `fix-hrv-source-unknown` | KEEP | Not addressed upstream |
| `fix-spo2-respiratory-missing` | KEEP | Not addressed upstream |
| `fix-sleep-stages-missing` | KEEP | Not addressed upstream |
| `fix-sleep-timezone` | KEEP | Upstream uses per-record `zone_offset` only; `User.timezone` column/migration not in upstream |
| `fix-summary-timezone-echo` | KEEP | Upstream only added `avg_hrv_rmssd_ms` to `BodyAveraged`; `ActivitySummary` untouched |
| `fix-garmin-connect-activity-hr-samples` | KEEP | Upstream Garmin changes are type-annotation comments only, no logic |

---

## Upstream commits

```
cea6502  fix: Cast to local date for summary endpoints (#1033)
54f2c3c  fix: commit each data type individually and rollback on exception for Oura/Whoop (#1022)
46bd6d7  chore: disable SDNN fallback in HRV-CV score calculation pending validation (#1040)
267aa27  feat(backend): add recovery summary endpoint (#1027)
2e827c7  fix(backend): garmin allDayRespiration & moveIQActivities (#1026)
9f85c4e  refactor(frontend): updated all TypeScript dependencies (#1025)
d284b0a  refactor(backend): updated all python dependencies (#1023)
08d8af3  fix(backend): drop event dispatch if svix is not configured (#1017)
5e38d2c  feat: Add data migration script for body fat percentage (#1016)
be8be31  refactor: Strava webhooks (#1010)
467f009  fix(backend): scope x100 ratio->percent conversion to Apple provider only (#917)
0de0659  refactor(frontend): Dashboard redesigned (#1015)
8b6fa96  feat: Sleep score recalculation after sleep session merge (#884)
25a8d4b  feat: sync status (fastapi SSE) (#986)
34df8a5  refactor(frontend): Dashboard v2 (#1003)
1dd06a5  refactor: polish Oura webhooks and adjust them to new webhook pattern (#990)
2bffe6b  refactor: migrate all recovery scores from data_point_series to health_score (#950)
467bda8  fix: add hrv rmssd to body summary (#949)
```

---

## Conflict detail

### `fix-activity-summary-utc-bucketing`

Upstream commit `cea6502` implemented timezone-aware bucketing on the exact same three methods our patch covers in `data_point_series_repository.py`:

- `get_daily_activity_aggregates`
- `get_daily_active_minutes`
- `get_daily_intensity_minutes`

The approaches differ:

| | Expression | Join required |
|---|---|---|
| **Upstream** | `recorded_at + COALESCE(zone_offset, '+00:00')::interval` cast to Date | No — reads per-record column |
| **Ours** | `(recorded_at AT TIME ZONE user.timezone)::date` | Yes — joins user table |

**Why we cannot retire this patch:** Garmin Connect and Ultrahuman ingest do not populate `zone_offset` on historical records. Upstream falls back to UTC in that case, meaning the cross-midnight bucketing bug (e.g. Sunday-morning Brisbane trail run landing on Saturday) persists for those providers. Our `user.timezone` fallback is still necessary.

**Required rewrite:** Update the patch to apply on top of upstream's already-modified code, combining both sources of truth:

```sql
-- Preferred: use per-record zone_offset when present, otherwise fall back to user.timezone
COALESCE(
    zone_offset::interval,
    make_interval(
        secs => EXTRACT(EPOCH FROM (
            NOW() AT TIME ZONE user.timezone - NOW() AT TIME ZONE 'UTC'
        ))
    )
)
```

The patch cannot be re-applied as-is — upstream has changed the function bodies, and a naive re-patch silently overwrites the `zone_offset` logic.

---

## Notes on merge

### Frontend CSS design-system conflict (`frontend-display-timezone`)

Upstream commits `0de0659` and `34df8a5` (Dashboard redesign v1/v2) touched every file our `frontend-display-timezone` patch modifies:

- `frontend/src/components/user/sleep-section.tsx`
- `frontend/src/components/user/activity-section.tsx`
- `frontend/src/components/user/scores-section.tsx`
- `frontend/src/components/user/workout-section.tsx`
- `frontend/src/routes/_authenticated/users/$userId.tsx`
- `frontend/src/lib/api/types.ts`

The upstream changes are **purely cosmetic** — hardcoded `zinc-*` Tailwind classes replaced with CSS design-system variables (`text-muted-foreground`, `bg-muted`, `border-border`, etc.). No structural or logic changes to any of these files.

Our versions still use `zinc-*`. After merging, our components will look visually inconsistent with the rest of the redesigned UI.

**Action:** For each file in the list above, apply the `zinc-*` → CSS-var substitutions from upstream's diff. This is mechanical find-and-replace with no risk to timezone logic.

Upstream also made additive additions to `types.ts` that do not conflict with our fields:
- `has_active_connection: boolean` on `UserRead`
- New `ConnectionsCoverage` and `ProviderConnectionCount` interfaces
- `SourceMetadata` interface
- `avg_hrv_rmssd_ms: number | null` on `BodyAveraged`

### SDNN and `fix-hrv-nightly-aggregate`

Upstream `46bd6d7` disabled the SDNN fallback in `resilience_service.py` (the HRV-CV / resilience score service) pending further validation. This is an entirely separate service from `summaries_service.py::get_sleep_summaries` where our patch operates. The sleep-window SDNN aggregation is unaffected.

### `fix-health-score-source-priority` — complementary upstream work

Upstream `8b6fa96` added `_recompute_sleep_scores` to `EventRecordService` — it deletes and recomputes internal sleep scores when sessions are merged or updated (write-path dedup). Our patch deduplicates at read time in `HealthScoreRepository.get_with_filters`. The two mechanisms are complementary:

- Upstream's write-path fix prevents new duplicates from accumulating after the merge.
- Our read-path fix handles the duplicate rows that already exist in the database from before that fix.

No conflict; keep both.

### New upstream features — no action needed

The following are purely additive and do not intersect any patched surface:

| Commit | Feature |
|---|---|
| 267aa27 | Recovery summary endpoint (`GET /recovery-summary`) |
| 8b6fa96 | Sleep score recompute on session merge |
| be8be31 | Strava webhook refactor |
| 1dd06a5 | Oura webhook refactor |
| 467f009 | Apple provider x100 ratio fix |
| 5e38d2c | Body fat data migration script |
| 25a8d4b | Sync status SSE endpoint |
| 2bffe6b | Recovery scores moved to `health_score` table |
| 2e827c7 | Garmin `allDayRespiration` / `moveIQActivities` fix |
| 08d8af3 | Drop event dispatch when Svix not configured |

---

## Recommended merge order

1. Merge `upstream/main` into `main` on the fork.
2. Rebase `dhlaw-patches-baseline-func` onto the updated `main`.
3. Rewrite `fix-activity-summary-utc-bucketing` to layer `user.timezone` fallback over upstream's `zone_offset` expression.
4. Apply `zinc-*` → CSS-var substitutions across the five affected frontend files.
5. Re-run `python ow-patches/apply.py` and smoke-test activity, sleep, and score bucketing for a user with a non-UTC `user.timezone`.
