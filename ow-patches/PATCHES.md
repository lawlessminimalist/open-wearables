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

---

## fix-hrv-source-unknown

- patch_id:                  fix-hrv-source-unknown
- status:                    upstream_candidate
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/services/providers/ultrahuman/data_247.py
- symbol:                    Ultrahuman247Data.save_activity_samples
- what_we_changed:           Pass `source=self.provider_name` (not `provider=`) when constructing TimeSeriesSampleCreate so the data_source row carries a non-null source label and consumers don't get back `"unknown"`.
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
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/services/providers/garmin/data_247.py, backend/app/services/providers/garmin_connect/data_247.py, backend/app/services/summaries_service.py
- symbol:                    Garmin247Data._build_dailies_samples + GarminConnect247Data.save_daily_stats_for_date + SummariesService.get_activity_summaries
- what_we_changed:           Persist `bmrKilocalories` as SeriesType.basal_energy from both Garmin providers, surface it on ActivitySummary as basal_calories_kcal, and stop computing total_calories_kcal as `active + 0` when basal is missing — return null so the field name is honest (active+basal, not active-only).
- retire_when:               Garmin daily-stats normalization persists basal energy AND ActivitySummary.total_calories_kcal is null when basal is missing (not equal to active_calories_kcal).
- upstream_equivalent_check: basal_calories_kcal
- local_patch_file:          ow-patches/local/fix-calories-total-mislabelled.py

---

## fix-spo2-respiratory-missing

- patch_id:                  fix-spo2-respiratory-missing
- status:                    upstream_candidate
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/services/providers/ultrahuman/data_247.py
- symbol:                    Ultrahuman247Data.normalize_activity_samples + Ultrahuman247Data.save_activity_samples + Ultrahuman247Data.load_and_save_all
- what_we_changed:           Map Ultrahuman intraday SpO2 (spo2/oxygen_saturation/blood_oxygen) and respiratory rate (respiratory_rate/breath_rate/breathing_rate/breath) tokens to SeriesType.oxygen_saturation and SeriesType.respiratory_rate. Fall back to the Sleep object's `spo2.value` (single nightly average emitted at sleep midpoint) when intraday samples aren't returned.
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
- what_we_changed:           Make Ultrahuman sleep-stage parsing robust to capitalization and key-name variants (deep / Deep Sleep / deep_sleep; stage_time / duration). Always emit the SleepStagesSummary object on SleepSummary responses (with null fields if the source doesn't track stages) so consumers can distinguish "source doesn't expose stages" from "feature not implemented".
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
- what_we_changed:           Added User.timezone (IANA, VARCHAR(50)) DB column + migration; added timezone, start_time_local, end_time_local fields to SleepSummary; populated them in get_sleep_summaries from the user's timezone. The DB column and migration are structural and not toggleable from apply.py — only the response population is. With the patch disabled, the columns/fields exist but contain None.
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
- what_we_changed:           Bucket the three daily activity aggregator queries by `(recorded_at AT TIME ZONE user.timezone)::date` instead of `cast(recorded_at, Date)` (UTC). Resolves the bug where workouts crossing a UTC midnight (e.g. a Sunday morning trail run in Brisbane that starts 21:14 UTC Saturday) split across two day-cards in the API and the user's "Sunday" card shows post-run HR (~81/128) instead of trail-run HR (~161/186). Falls back to UTC when user.timezone is null.
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
- symbol:                    EventRecordRepository.get_sleep_summaries
- what_we_changed:           When EventRecord.zone_offset is NULL (which is the common case for Garmin Connect / Ultrahuman sync paths), upstream falls back to UTC for the `local_sleep_date` bucketing. Replace the fallback with `(end_datetime AT TIME ZONE user.timezone)::date` so a Sunday-morning Brisbane wake doesn't land on the previous UTC day. When user.timezone is also unset, falls through to UTC (= upstream behaviour) so disabling the patch is safe.
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
- file:                      backend/app/schemas/responses/activity/summaries.py, backend/app/services/summaries_service.py (composed via fix-calories-total-mislabelled)
- symbol:                    SummariesService.get_activity_summaries (timezone field on each ActivitySummary)
- what_we_changed:           Echo `user.timezone` as a `timezone` field on each ActivitySummary so the frontend's display-tz selector knows which IANA zone the daily-bucket dates anchor to. Sleep already does this via fix-sleep-timezone. The schema field is added in source (structural); only the population is toggleable from apply.py.
- retire_when:               ActivitySummary response includes a non-null timezone hint when user.timezone is set (or upstream provides an equivalent way for the frontend to know what timezone the daily-bucket dates are anchored to).
- upstream_equivalent_check: backend/app/schemas/responses/activity/summaries.py::timezone: str | None
- local_patch_file:          ow-patches/local/fix-summary-timezone-echo.py

---

## fix-active-minutes-broken

- patch_id:                  fix-active-minutes-broken
- status:                    upstream_candidate
- upstream_url:              https://github.com/the-momentum/open-wearables
- upstream_issue_or_pr:      null
- file:                      backend/app/services/summaries_service.py
- symbol:                    SummariesService.get_activity_summaries
- what_we_changed:           Derive ActivitySummary.active_minutes from intensity_minutes.{light,moderate,vigorous} (HR-based, intraday) when present, instead of the per-minute step bucket which collapses to ~1 for providers that store steps as a single daily total.
- retire_when:               ActivitySummary.active_minutes equals intensity_minutes.light + moderate + vigorous when HR-based intensity is available, OR upstream uses a different active-minutes signal that doesn't collapse to 1 for daily-total step providers.
- upstream_equivalent_check: active_mins = light + moderate + vigorous
- local_patch_file:          ow-patches/local/fix-active-minutes-broken.py

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
