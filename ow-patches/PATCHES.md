# Fork Patches Registry

Source of truth for every place this fork diverges from upstream.
See `../README.md#fork-patches` for usage. Run `python ow-patches/check_upstream.py`
to see whether upstream has caught up to any of these.

Upstream: https://github.com/the-momentum/open-wearables

This file has two halves:

1. **Backend Patches** — runtime monkey-patched via `apply.py`. Each is
   independently toggleable. `check_upstream.py` covers these.
2. **Frontend Patches (Source Edits)** — direct edits to `frontend/src/`,
   not toggleable at runtime (the app is built once and served as static
   assets). Documented for institutional memory.

**`replacement_kind` field.** Each patch declares how it relates to upstream so
`check_upstream.py` can escalate drift correctly:

- `wholesale-replace` (default if omitted) — the patch reimplements an upstream
  method/body. These SHADOW upstream: a `git merge` will not conflict (we never
  edit the upstream source file), so if upstream rewrites the method our copy
  silently wins and drops upstream's changes. Drift on these is a **shadow risk**.
- `decorate` — the patch wraps upstream's method and only post-processes the
  result. It inherits upstream changes; drift is lower-risk (re-verify only).
- `structural` — source edits (schema fields, columns, frontend). Upstream drift
  surfaces as ordinary git conflicts at merge time, so the runtime drift check
  doesn't apply.
- `standalone` — a self-contained helper/function swap with no upstream body to
  go stale.

When in doubt, leave it `wholesale-replace`: a false "re-verify" costs one manual
diff; a missed shadow is how `avg_hrv_rmssd_ms` silently went null.

---

## ⚠ Deployment requirement — the patches must be IN the image

`ow-patches/` lives at the **repo root**, but upstream's backend image is built
with `./backend` as the context. So `ow-patches` is not in that context and
cannot be `COPY`ed by `backend/Dockerfile` (Docker forbids `COPY ../`). If it
isn't put there some other way, `_apply_ow_patches()` in `backend/app/__init__.py`
finds nothing and returns — and **every patch in this file silently no-ops**.
The app boots clean. Nothing logs. The structural halves (DB columns, schema
fields, frontend edits) are still present, so you get the exact
"fields exist but are always null" symptom that a disabled patch produces.

**This happened.** On 2026-08-20 the homelab k8s cluster was found with
`/root_project/ow-patches` **absent** and all 14 patches inert — for weeks. The
deployments had no `volumeMounts` and no `OW_PATCHES_DIR`. The old
`docker-compose.prod.yml` bind-mounted the directory (its comment warned about
precisely this failure), but upstream deleted that file in #1429 and the k8s
manifests never replaced the mount.

Three guards now exist; keep all three:

1. **`Dockerfile.ow-patches`** (repo root) — fork-owned overlay that layers
   `ow-patches` onto the upstream-built backend image and sets
   `OW_PATCHES_DIR` + `OW_PATCHES_REQUIRED`. Both CI
   (`.github/workflows/publish-ghcr.yml`) and `scripts/build-push.sh` build
   through it, and both then assert `apply.py` is present in the result.
2. **`OW_PATCHES_REQUIRED=1`** — makes a missing directory a hard startup
   failure instead of a silent skip. Deployments of this fork should always set
   it. Unset, an unpatched run now at least warns on stderr. Covered by
   `backend/tests/test_ow_patches_guard.py`.
3. **Deployment manifests** must run the overlay image. If you build the
   backend with a plain `podman build ./backend`, you will ship an unpatched
   image again.

Quick check against a running cluster:

```bash
kubectl -n open-wearables exec deploy/app -- ls /root_project/ow-patches/apply.py
```

---

## fix-hrv-source-unknown

- patch_id:                  fix-hrv-source-unknown
- status:                    upstream_candidate
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/services/providers/ultrahuman/data_247.py
- symbol:                    Ultrahuman247Data.save_activity_samples
- what_we_changed:           Pass `source=self.provider_name` (not `provider=`) when constructing TimeSeriesSampleCreate so the data_source row carries a non-null source label and consumers don't get back `"unknown"`.
- rebased_note:              Rebased 2026-07-26 onto merged upstream. Upstream rewrote save_activity_samples: now resolves series via ACTIVITY_SAMPLE_SERIES.get (#1206) and passes is_daily_total=daily_total_flag(...) (#1232). Patch body is now upstream's current body with `source=self.provider_name` added alongside `provider=` (both kept) — the inline type_mapping and missing is_daily_total that were shadowing upstream are gone.
- retire_when:               Ultrahuman247Data.save_activity_samples passes `source=` (not just `provider=`) to TimeSeriesSampleCreate, OR the TimeSeriesSampleCreate constructor itself populates source from provider when source is omitted.
- upstream_equivalent_check: providers/ultrahuman/data_247.py::source=self.provider_name
- local_patch_file:          ow-patches/local/fix-hrv-source-unknown.py

---

## fix-hrv-nightly-aggregate

- patch_id:                  fix-hrv-nightly-aggregate
- status:                    retired
- retired_in:                upstream commit 09b7b0a ("Oura missed commit and sleep summary metrics"), merged 2026-06-07
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/services/summaries_service.py
- symbol:                    SummariesService.get_sleep_summaries
- what_we_changed:           Compute mean SDNN HRV, respiratory rate, and SpO2 over the sleep window padded by ±30min, populating avg_hrv_sdnn_ms / avg_respiratory_rate / avg_spo2_percent on each SleepSummary record (instead of always-null TODOs). Raw intraday samples remain untouched.
- retire_when:               get_sleep_summaries response includes avg_hrv_sdnn_ms as a non-null float when intraday SDNN samples exist within the sleep window.
- retirement_note:           Upstream rewrote get_sleep_summaries to populate avg_hrv_sdnn_ms / avg_respiratory_rate / avg_spo2_percent itself AND added a new avg_hrv_rmssd_ms field. Upstream averages over the EXACT sleep window (we padded ±30min) and does not round SDNN — both are accepted regressions on retirement. Our wholesale-replacement patch was shadowing upstream's new avg_hrv_rmssd_ms (leaving it null), which is why it was retired rather than kept. The marker SLEEP_PHYSIO_WINDOW_PAD never matched upstream (it is unique to our impl), so check_upstream.py could not auto-flag this — see the "Wholesale-replacement audit" note in README#fork-patches.
- upstream_equivalent_check: SLEEP_PHYSIO_WINDOW_PAD
- local_patch_file:          ow-patches/local/fix-hrv-nightly-aggregate.py

---

## fix-pace-null

- patch_id:                  fix-pace-null
- status:                    upstream_candidate
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/services/event_record_service.py
- symbol:                    EventRecordService.get_workouts
- what_we_changed:           Compute avg_pace_sec_per_km in the workout list response (was hard-coded None) using the same derivation as the detailed view: 1000/average_speed if present, else duration_seconds/(distance_meters/1000), restricted to WORKOUTS_WITH_PACE.
- retire_when:               Workout list response (get_workouts → Workout.avg_pace_sec_per_km) returns a non-null int for running/walking/cycling workouts that have distance and duration.
- upstream_equivalent_check: _compute_avg_pace_sec_per_km
- local_patch_file:          ow-patches/local/fix-pace-null.py

---

## fix-calories-total-mislabelled

- patch_id:                  fix-calories-total-mislabelled
- status:                    upstream_candidate
- replacement_kind:          decorate
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/services/providers/garmin/coverage.py, backend/app/services/providers/garmin_connect/data_247.py, backend/app/services/summaries_service.py
- symbol:                    DAILIES_SERIES (basal mapping, structural) + GarminConnect247Data.save_daily_stats_for_date + SummariesService.get_activity_summaries (decorated)
- what_we_changed:           Persist `bmrKilocalories` as SeriesType.basal_energy from both Garmin providers, surface it on ActivitySummary as basal_calories_kcal, and stop computing total_calories_kcal as `active + 0` when basal is missing — return null so the field name is honest (active+basal, not active-only).
- rebased_note:              Rebased 2026-07-26 onto merged upstream. Was a wholesale-replace of Garmin247Data._build_dailies_samples + get_activity_summaries, which shadowed #1232 (is_daily_total) and #1242 (active_time_minutes). Now: (1) Garmin OAuth basal is a one-line STRUCTURAL add to garmin/coverage.py::DAILIES_SERIES (upstream's own _build_dailies_samples persists it with the correct daily_total_flag — no shadow); (2) get_activity_summaries is a DECORATOR (apply_calories_fix in apply.py) that reconstructs basal = total - active from upstream's output and nulls total unless both present, inheriting #1242's active_time_minutes instead of shadowing it; (3) garmin_connect override retained (fork-only provider) and now stamps is_daily_total via daily_total_flag.
- retire_when:               Garmin daily-stats normalization persists basal energy AND ActivitySummary.total_calories_kcal is null when basal is missing (not equal to active_calories_kcal) AND ActivitySummary.basal_calories_kcal is populated.
- upstream_equivalent_check: basal_calories_kcal
- local_patch_file:          ow-patches/local/fix-calories-total-mislabelled.py

---

## fix-spo2-respiratory-missing

- patch_id:                  fix-spo2-respiratory-missing
- status:                    upstream_candidate
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/services/providers/ultrahuman/data_247.py, backend/app/services/providers/ultrahuman/coverage.py
- symbol:                    Ultrahuman247Data.normalize_activity_samples + Ultrahuman247Data.load_and_save_all + ACTIVITY_SAMPLE_SERIES (coverage, structural)
- what_we_changed:           Map Ultrahuman intraday SpO2 (spo2/oxygen_saturation/blood_oxygen) and respiratory rate (respiratory_rate/breath_rate/breathing_rate/breath) tokens to SeriesType.oxygen_saturation and SeriesType.respiratory_rate. Fall back to the Sleep object's `spo2.value` (single nightly average emitted at sleep midpoint) when intraday samples aren't returned.
- rebased_note:              Rebased 2026-07-26 onto merged upstream. Upstream rewrote load_and_save_all to add an active_minutes → SeriesType.active_time ingestion block (#1242); the stale copy predated it and dropped it. Now upstream's current normalize_activity_samples / load_and_save_all bodies with our SpO2/respiratory tokens + Sleep.spo2 fallback re-applied. Because upstream #1206 removed the inline type_mapping (now ACTIVITY_SAMPLE_SERIES.get), the two new SeriesTypes MUST be resolvable via that constant — added `spo2`/`respiratory_rate` to ultrahuman/coverage.py::ACTIVITY_SAMPLE_SERIES (STRUCTURAL; TIMESERIES derives from it, so the coverage tab advertises them).
- retire_when:               get_timeseries response for ultrahuman provider returns at least one record with type=oxygen_saturation or type=respiratory_rate when the user has data for those metrics.
- upstream_equivalent_check: providers/ultrahuman/data_247.py::_RESPIRATORY_TYPES
- local_patch_file:          ow-patches/local/fix-spo2-respiratory-missing.py

---

## fix-sleep-stages-missing

- patch_id:                  fix-sleep-stages-missing
- status:                    upstream_candidate
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/services/providers/ultrahuman/data_247.py, backend/app/services/summaries_service.py
- symbol:                    Ultrahuman247Data.normalize_sleep + SummariesService.get_sleep_summaries
- replacement_kind:          decorate
- what_we_changed:           Make Ultrahuman sleep-stage parsing robust to capitalization and key-name variants (deep / Deep Sleep / deep_sleep; stage_time / duration). Always emit the SleepStagesSummary object on SleepSummary responses (with null fields if the source doesn't track stages) so consumers can distinguish "source doesn't expose stages" from "feature not implemented". The summary-side change is now a decorator over upstream's get_sleep_summaries (ensure_stages_object), not a wholesale replacement — see apply.py.
- retire_when:               get_sleep_summary response.data[*].stages is always an object (never null/missing) when sleep records exist, AND ultrahuman sleep stages parse correctly when upstream returns them with the canonical type tokens.
- upstream_equivalent_check: stage_aliases
- local_patch_file:          ow-patches/local/fix-sleep-stages-missing.py

---

## fix-sleep-timezone

- patch_id:                  fix-sleep-timezone
- status:                    local_only
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/models/user.py, backend/app/schemas/responses/activity/summaries.py, backend/app/services/summaries_service.py, backend/migrations/versions/2026_05_05_1200-9b3d4f7a8c21_user_timezone.py
- symbol:                    User.timezone (column) + SleepSummary (timezone/start_time_local/end_time_local fields) + SummariesService.get_sleep_summaries (population)
- replacement_kind:          decorate
- what_we_changed:           Added User.timezone (IANA, VARCHAR(50)) DB column + migration; added timezone, start_time_local, end_time_local fields to SleepSummary; populated them in get_sleep_summaries from the user's timezone. The DB column and migration are structural and not toggleable from apply.py — only the response population is. The population is now a decorator over upstream's get_sleep_summaries (apply_timezone_fields), not a wholesale replacement — see apply.py. With the patch disabled, the columns/fields exist but contain None.
- retire_when:               UserRead response includes a timezone field AND sleep summaries surface a per-record local datetime or a top-level user.timezone the consumer can apply.
- upstream_equivalent_check: start_time_local
- local_patch_file:          ow-patches/local/fix-sleep-timezone.py

---

## fix-activity-summary-utc-bucketing

- patch_id:                  fix-activity-summary-utc-bucketing
- status:                    local_only
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/repositories/data_point_series_repository.py
- symbol:                    DataPointSeriesRepository.get_daily_activity_aggregates + .get_daily_active_minutes + .get_daily_intensity_minutes
- what_we_changed:           Bucket the three daily activity aggregator queries by user-local date instead of UTC. Resolves the bug where workouts crossing a UTC midnight (e.g. a Sunday morning trail run in Brisbane that starts 21:14 UTC Saturday) split across two day-cards in the API and the user's "Sunday" card shows post-run HR (~81/128) instead of trail-run HR (~161/186). Zone-offset-first: honour a populated EventRecord.zone_offset, else `(recorded_at AT TIME ZONE user.timezone)::date`, else UTC.
- rebased_note:              Rebased 2026-07-26 onto merged upstream. Upstream rewrote all three aggregators: #1232 added prefer_daily_sum / is_daily_total de-duplication; #1242 added SeriesType.active_time → active_time_minutes and DataSource.provider in SELECT/GROUP BY/return dict. The stale wholesale copies used naive func.sum(case(...)) and shadowed both (reintroducing daily-total double-count and dropping active_time_minutes/provider). Now upstream's current three bodies with ONLY the date-bucket sub-expression swapped to the zone_offset-first / user.timezone / UTC coalesce.
- retire_when:               DataPointSeriesRepository.get_daily_activity_aggregates groups by user-local date (any of: AT TIME ZONE user.timezone, ZoneInfo-based bucketing, per-row zone_offset cast). Marker: any reference to `_local_date_bucket_expr` or equivalent timezone-aware bucketing helper in DataPointSeriesRepository.
- upstream_equivalent_check: backend/app/repositories/data_point_series_repository.py::_local_date_bucket_expr
- local_patch_file:          ow-patches/local/fix-activity-summary-utc-bucketing.py

---

## fix-garmin-connect-activity-hr-samples

- patch_id:                  fix-garmin-connect-activity-hr-samples
- status:                    local_only
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/services/providers/garmin_connect/workouts.py, backend/app/services/providers/garmin_connect/client.py
- symbol:                    GarminConnectWorkouts.load_data + GarminConnectClient.get_activity_details
- what_we_changed:           After saving each Garmin Connect workout, fetch `client.get_activity_details(activity_id)` and persist its `activityDetailMetrics` HR column as additional `heart_rate` time-series samples. The daily HR endpoint is 2-min sampled and undersamples workout peaks (e.g. May 3 trail run reported max 186 by Garmin but stored max 179). Skips workouts with no HR or under 5 minutes; per-activity errors are caught so one bad activity can't poison the sync.
- retire_when:               GarminConnectWorkouts.load_data calls a per-activity HR-detail endpoint and persists per-second (or sub-minute) heart_rate samples for each workout. Marker: presence of `get_activity_details` (or `activityDetailMetrics`) anywhere in backend/app/services/providers/garmin_connect/.
- upstream_equivalent_check: backend/app/services/providers/garmin_connect/::activityDetailMetrics
- local_patch_file:          ow-patches/local/fix-garmin-connect-activity-hr-samples.py

---

## fix-sleep-summary-utc-bucketing

- patch_id:                  fix-sleep-summary-utc-bucketing
- status:                    local_only
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/repositories/event_record_repository.py
- symbol:                    EventRecordRepository.get_sleep_summaries + EventRecordRepository._get_sleep_sessions
- what_we_changed:           When EventRecord.zone_offset is NULL (which is the common case for Garmin Connect / Ultrahuman sync paths), upstream falls back to UTC for the `local_sleep_date` bucketing. Replace the fallback with `(end_datetime AT TIME ZONE user.timezone)::date` so a Sunday-morning Brisbane wake doesn't land on the previous UTC day. When user.timezone is also unset, falls through to UTC (= upstream behaviour) so disabling the patch is safe.
- rebased_note:              Rebased 2026-07-26 onto merged upstream. Upstream rewrote get_sleep_summaries (#1257 provider grouping + per-session `sessions` breakdown; #1259 physio LATERAL producing avg_hr/avg_hrv_sdnn/avg_hrv_rmssd/avg_resp/avg_spo2) — the stale wholesale copy dropped ALL of it (re-nulling the physio metrics, the same failure that retired fix-hrv-nightly-aggregate). Now upstream's current body with ONLY the local_sleep_date zone_offset-first / user.timezone-fallback / UTC-fallback swapped. ALSO extended to replace _get_sleep_sessions with the identical bucket, so the sessions key matches the summary key for NULL-zone_offset providers (else `sessions` came back empty for Garmin Connect / Ultrahuman).
- retire_when:               EventRecordRepository.get_sleep_summaries falls back to a non-UTC source when zone_offset is null (i.e. uses user.timezone or any other timezone-aware mechanism for the wake-date bucket).
- upstream_equivalent_check: backend/app/repositories/event_record_repository.py::func.timezone
- local_patch_file:          ow-patches/local/fix-sleep-summary-utc-bucketing.py

---

## fix-health-score-source-priority

- patch_id:                  fix-health-score-source-priority
- status:                    local_only
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/repositories/health_score_repository.py
- symbol:                    HealthScoreRepository.get_with_filters
- what_we_changed:           When `fill_missing_sleep_scores_task` finds two sleep records for the same night (Garmin + Ultrahuman), it persists two `provider='internal'` scores — one per underlying sleep_record_id. Dedupe at read time: group by (local-date in user.timezone, category), keep the score whose underlying sleep record has the highest-priority source. Resilience/recovery scores without a sleep_record_id pass through untouched. Pagination applied after dedup so total_count reflects what consumers see. No-op when caller filters by `provider`.
- retire_when:               HealthScoreRepository.get_with_filters returns at most one score per (local-date, category) when multiple providers have records for the same night, OR upstream offers an explicit dedupe option.
- upstream_equivalent_check: backend/app/repositories/health_score_repository.py::provider_order
- local_patch_file:          ow-patches/local/fix-health-score-source-priority.py

---

## fix-summary-timezone-echo

- patch_id:                  fix-summary-timezone-echo
- status:                    local_only
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/schemas/responses/activity/summaries.py, backend/app/services/summaries_service.py
- symbol:                    SummariesService.get_activity_summaries (timezone field on each ActivitySummary)
- replacement_kind:          decorate
- what_we_changed:           Echo `user.timezone` as a `timezone` field on each ActivitySummary so the frontend's display-tz selector knows which IANA zone the daily-bucket dates anchor to. Sleep already does this via fix-sleep-timezone. The schema field is added in source (structural); only the population is toggleable from apply.py.
- rebased_note:              Rebased 2026-07-26. Previously rode inside the wholesale get_activity_summaries replacement; now applied as a standalone one-line population (`summary.timezone = user_tz`) in the get_activity_summaries DECORATOR in apply.py, independent of fix-calories-total-mislabelled. No longer depends on a full method reimplementation.
- retire_when:               ActivitySummary response includes a non-null timezone hint when user.timezone is set (or upstream provides an equivalent way for the frontend to know what timezone the daily-bucket dates are anchored to).
- upstream_equivalent_check: backend/app/schemas/responses/activity/summaries.py::timezone: str | None
- local_patch_file:          ow-patches/local/fix-summary-timezone-echo.py

---

## fix-active-minutes-broken

- patch_id:                  fix-active-minutes-broken
- status:                    retired
- retired_in:                upstream #1242 "active minutes as a new series type" (commit 76ffff4). Marked retired 2026-07-26 against upstream/main; NOT YET MERGED into this fork (baseline 06a6435). Behavioral retirement lands with the upstream merge — see retirement_note.
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/services/summaries_service.py
- symbol:                    SummariesService.get_activity_summaries
- what_we_changed:           Derive ActivitySummary.active_minutes from intensity_minutes.{light,moderate,vigorous} (HR-based, intraday) when present, instead of the per-minute step bucket which collapses to ~1 for providers that store steps as a single daily total.
- retire_when:               ActivitySummary.active_minutes equals intensity_minutes.light + moderate + vigorous when HR-based intensity is available, OR upstream uses a different active-minutes signal that doesn't collapse to 1 for daily-total step providers.
- retirement_note:           Superseded by upstream #1242 (76ffff4): SeriesType.active_time ("Provider-reported daily active time", minutes) is persisted for Garmin/Oura/Polar/Ultrahuman, aggregated as active_time_minutes, and get_activity_summaries now prefers it over the step heuristic; independently the repo excludes daily-total step rows from the per-minute bucket (data_point_series_repository.py `is_daily_total.isnot(True)`, ~L668) so the fallback returns null instead of collapsing to 1 — satisfying the second retire_when clause. Keeping our override is strictly worse: our intensity-band derivation now SHADOWS upstream's more authoritative active_time signal (the composed get_activity_summaries never reads active_time_minutes). CAVEAT — retired ahead of the merge: the PATCHES_ENABLED flag is False, but active_minutes is currently still computed by the composed fix-calories-total-mislabelled base_impl (they share the wholesale get_activity_summaries replacement), so the flag flip is bookkeeping until (a) upstream/main is merged and (b) fix-calories-total-mislabelled is rebased to a decorator over upstream's get_activity_summaries. Run the pytest verification at that point, not now. garmin_connect (fork-only) does not emit SeriesType.active_time, so post-merge its active_minutes falls to the step path which now returns null (honest) rather than 1 — accepted regression.
- upstream_equivalent_check: active_mins = light + moderate + vigorous
- local_patch_file:          ow-patches/local/fix-active-minutes-broken.py

---

## fix-garmin-connect-rate-limit-backoff

- patch_id:                  fix-garmin-connect-rate-limit-backoff
- status:                    local_only
- replacement_kind:          wholesale-replace
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/services/providers/garmin_connect/client.py, backend/app/services/providers/garmin_connect/data_247.py
- symbol:                    GarminConnectClient._login + ._get_api + ._call_with_reauth, GarminConnect247Data.load_and_save_all
- what_we_changed:           Stop the credential-based Garmin Connect sync from self-inflicting an IP-level rate-limit ban. (1) Added `GarminConnectRateLimitError` and classify 429 / Cloudflare-challenge / "all login strategies exhausted" failures as rate-limiting rather than auth failures — `_call_with_reauth` no longer re-logs-in on them (upstream's auth-marker list matched "403" and "login", so a Cloudflare 403 cost two login storms instead of one). (2) `_get_api` refuses to attempt login while blocked, using an in-process `_blocked_until` plus a Redis cooldown key, so the remaining ~149 iterations of a run fail instantly without network I/O. (3) The Redis cooldown escalates geometrically per consecutive strike (30m → 1h → 2h → 4h, capped 6h, reset on success) so the hourly beat schedule stops re-hammering. (4) `load_and_save_all` pre-flight-checks the cooldown, breaks out of BOTH loops on the first rate-limit instead of swallowing it per (date, data_type), and re-raises so the run is recorded failed rather than silently `partial` with zero records. Genuinely transient errors retry with bounded exponential backoff + jitter, honouring `Retry-After`. Non-rate-limit per-day errors are still swallowed and logged exactly as before.
- why:                       `load_and_save_all` loops ~30 dates × 5 data types with a blanket `except Exception` that cannot tell "no stress data today" from "we are 429'd". Because `_get_api` only assigns `self._api` after a *successful* login, a failed login left it `None` and every one of the ~150 iterations re-attempted a full login — and the underlying `garminconnect` client tries five strategies per login, sleeping ~16–20s inside the portal strategy. That is up to ~750 auth requests per run, hourly, with overlapping runs. Observed 2026-08-20: every hourly run finishing `partial`, `garmin_connect` data stuck since 2026-08-03, logs a solid wall of `429 — IP rate limited by Garmin` and `HTTP 403 (Cloudflare bot challenge)`. Same failure class as the Ultrahuman refresh bug: an unrecoverable auth error treated as a recoverable per-day error.
- retire_when:               GarminConnectClient distinguishes rate-limit/WAF rejections from ordinary auth failures and stops re-attempting login once blocked, AND load_and_save_all aborts the run instead of continuing through every remaining (date, data_type) pair.
- upstream_equivalent_check: backend/app/services/providers/garmin_connect/::GarminConnectRateLimitError
- local_patch_file:          ow-patches/local/fix-garmin-connect-rate-limit-backoff.py

---

# Frontend Patches (Source Edits)

These changes live directly in `frontend/src/` and are **not toggleable** via
`apply.py` — the frontend is built once and served as static assets, so a
runtime monkey-patch wouldn't make sense. They're listed here for the same
reason as the backend patches: institutional memory of where we've diverged
from upstream so future developers (and `check_upstream.py` reviews of the
companion backend changes) have the full picture.

To revert a frontend patch, revert the source files via `git checkout
upstream/main -- <files>`. There's no flag to flip.

---

## frontend-display-timezone

- patch_id:           frontend-display-timezone
- status:             local_only
- replacement_kind:   structural
- upstream_url:       https://github.com/the-momentum/open-wearables
- files:
  - frontend/package.json (added `date-fns-tz`)
  - frontend/src/lib/dates.ts                       (new helper module)
  - frontend/src/contexts/display-timezone.tsx       (new context)
  - frontend/src/components/common/timezone-selector.tsx (new selector)
  - frontend/src/components/user/sleep-section.tsx   (formatInTz)
  - frontend/src/components/user/activity-section.tsx (formatInTz)
  - frontend/src/components/user/scores-section.tsx  (formatInTz)
  - frontend/src/components/user/workout-section.tsx (formatInTz)
  - frontend/src/components/user/profile-section.tsx (timezone field on Edit form)
  - frontend/src/lib/utils/timeseries.ts             (prepareHrChartData accepts tz)
  - frontend/src/lib/api/types.ts                    (UserRead/UserUpdate.timezone, SleepSummary.timezone/start_time_local/end_time_local, ActivitySummary.timezone/basal_calories_kcal)
  - frontend/src/routes/_authenticated/users/$userId.tsx (DisplayTimezoneProvider + TimezoneSelector mounted)
- what_we_changed:    Two distinct timezones in the dashboard:
  1. **User Timezone** (User.timezone IANA, settable from the profile edit
     dialog or PATCH /users/{id}). Anchors backend daily-bucket dates.
  2. **Display Timezone** (ephemeral, view-only). DropdownMenu picker at the
     top of the user dashboard. Defaults to UTC. Persisted in localStorage
     keyed per user_id. Drives `formatInTz(...)` for every UTC datetime
     rendered in sleep / activity / scores / workout sections plus the
     HR-during-sleep and HR-during-workout chart axes. Does NOT modify data.
  Calendar dates from daily-bucketed summaries (e.g. ActivitySummary.date
  "2026-05-03") are deliberately rendered in UTC anchor so the day label
  ("May 3") stays stable as the developer toggles the display tz.
- retire_when:        Upstream ships a similar two-timezone model (one stored,
  one display) — would surface as `User.timezone` in upstream + a display-tz
  context provider on the user dashboard.
- discovery:          `grep -r 'formatInTz\|DisplayTimezoneProvider\|date-fns-tz' frontend/src/`
