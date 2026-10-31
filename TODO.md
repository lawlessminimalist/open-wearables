# TODO

Open work for this fork, kept current as work lands. Read it at session start. Finished items move to `CHANGELOG.md`; this file carries no history.

## Correctness

- Withings blood pressure never reaches the dashboard. Ingestion is fine (systolic and diastolic rows exist daily, paired by `external_id`), but the old React body section asked `/summaries/body` with `latest_window_hours: 4`, the route caps it at 24, and the cuff is used once a day. The React dashboard is gone since upstream 0.10.0; re-check the SvelteKit body view (`frontend/src/lib/body/`, `components/body/`) before fixing, then either fetch the systolic/diastolic timeseries over the selected period or add a windowless latest-pair lookup to `get_body_summary` as a registered patch.
- `User.timezone` is never validated as an IANA name, so a typo would make every summary request for that user fail through `func.timezone`. Add a `ZoneInfo` validator on the user write schemas or a UTC fallback in `_resolve_user_timezone`.
- `archival_repository` buckets archived days in UTC while live rows use the user-local date, so the archive and live merge keys will disagree once archival is enabled.
- A chained historical sync does not stop after a Garmin rate limit. Chunks two onwards each fail the cooldown pre-flight, emit their own failed run and `sync.failed` webhook, and the skipped windows are never re-queued.
- The synchronous sync route has no handler for `GarminConnectClientError`, so a cooldown surfaces as an unhandled 500.
- The outer retry in `_call_with_reauth` stacks on garminconnect's own three retries for transient errors, up to nine requests per pair during an outage.
- The `task_id` stamped by `_current_task_id()` in the sync task is consumed by nothing; wire it into cancel or liveness, or drop the delta.
- `create_workout_with_detail` in `event_record_service.py` is an unregistered direct edit to an upstream file; register it as structural or move it under `garmin_connect/`.

- Deploy blocker for the 2026-10-31 reconcile: `garmin_connect` daily totals (steps, active and basal energy, distance, floors, intensity minutes) written before that reconcile carry no `period`, and upstream's rollup and its sample backfill both skip `is_daily_total` samples, so that history vanishes from activity summaries on deploy. Before or straight after deploying, re-run the `garmin_connect` historical sync over the whole history, which rewrites those days with `period=` and mirrors them into `series_aggregate`, then confirm with `ow-sql` that `series_aggregate` holds provider day rows for old dates. A re-sync does restore them: `bulk_create` calls `_mirror_aggregates` on every creator regardless of the sample upsert and that upserts `series_aggregate` (read in code, not run). The old period-less samples stay in `series_sample`, where the rollup ignores them, so nothing double-counts.
- Local day for the aggregates rollup. Since upstream 8adb8d0b day sums and heart rate come from `series_aggregate`, keyed by `period_start + zone_offset` with a NULL offset read as UTC, and `garmin_connect` and `ultrahuman` intraday samples never set `zone_offset`, so the Brisbane UTC-midnight bug is back for day sums. The chosen fix is to stamp `zone_offset` from `User.timezone` at ingestion (a direct edit in `garmin_connect/`, a small decorate patch on Ultrahuman's sample builder), backfill it on existing rows and re-roll. Then retire the two minutes replacements in `fix-activity-summary-utc-bucketing`, drop the strict xfails in `tests/api/v1/test_timezone_bucketing.py`, and recheck `fix-summary-timezone-echo`, whose claim that dates are anchored to `User.timezone` is false for day sums until then.
- Sleep day keys still disagree for NULL-offset providers. `fix-sleep-summary-utc-bucketing` keys summaries by `User.timezone`, but `night_values` (scores) and `winning_sleep_record_ids` (the `/events/sleep` de-dup) key by UTC, so east of UTC a night's score and session winner sit one local day earlier than its summary. The ingestion fix above resolves it too.
- `HealthScoreRepository.get_recovery_summaries` partitions on a bare `cast(recorded_at, Date)`, which follows the session time zone, while `get_with_filters` uses `timezone('UTC', ...)`. Harmless while sessions run in UTC; worth aligning.
- Pre-#1484 health scores with a null `data_source_id` now rank last in upstream's per-day dedupe instead of by their sleep record's source (accepted when `fix-health-score-source-priority` retired). Not checked against production data.

## Frontend (SvelteKit, since upstream 0.10.0)

Upstream replaced the React dashboard; the fork's React edits were dropped at the 2026-10-19 reconcile (`frontend-display-timezone` retired, see `ow-patches/PATCHES.md`). To port onto the Svelte portal:

- Display timezone: the portal formats every datetime with `timeZone: 'UTC'` (`src/lib/utils/format.ts`, `src/lib/syncs/format.ts`, `src/lib/charts/ticks.ts`). Render in the viewed user's `User.timezone` (echoed on sleep and activity summaries by the backend patches), with an optional per-viewer override.
- `User.timezone` field on the user edit form (the API accepts it on PATCH `/users/{id}`).
- Credential-provider connect (superseded by the Garmin Personal API work below): the portal lists only `has_cloud_api` providers for pairing, so `garmin_connect` can only be connected with `POST /api/v1/users/{user_id}/connections/garmin_connect`. The old providers tab also showed credential providers in their own section.
- `basal_calories_kcal` on the activity view.

## Tooling

- `symbol_hash.py` hashes depend on the interpreter: the system `python3` (3.15) reports unchanged methods as CHANGED, while the baseline was recorded under the backend venv (3.14). Pin `check_upstream.py` and the shadow-drift test to the venv interpreter with a version assert, or normalise the AST dump.
- Compare each wholesale-replace patch's `inspect.signature` with upstream's at install. Two patches in the 2026-10-31 reconcile (`fix-health-score-source-priority`, `fix-spo2-respiratory-missing`) would have raised TypeError on every call after a caller gained arguments, with every guard green.
- Add a lint that imports each `ow-patches/local/*.py` against the merged tree without the fail-fast app import, and make a symbol deleted upstream fail `check_upstream.py` loudly (it printed `[None]` and reported CHANGED). `fix-sleep-summary-utc-bucketing`'s dead `DataPointSeries` import took the whole app down at import.
- `fix-hrv-source-unknown`'s `upstream_equivalent_check` (`data_247.py::source=self.provider_name`) already matches the fork's own source edit in `save_sleep_data`; scope it to `_build_activity_samples`.
- Extend `test_ow_patches_column_drift.py` to `self.model.*` and `SeriesType.*` references and result-dict keys, or retire it in favour of the shadow-drift hash.
- Wire `.pre-commit-config.yaml` per clone and add the language-agnostic gate rules for `.env` files, database files, size caps and binary magic bytes.
- The podman/krunkit fix recorded on 2026-09-20 (`brew trust libkrun/krun && brew install krunkit`) was never applied on this machine, yet `podman machine start` and the full backend suite ran clean on 2026-09-22. The root cause of the original `krunkit exited unexpectedly with exit code 2` is unconfirmed, so if it recurs here or on another clone, apply that fix rather than assuming it is permanently resolved.

## Garmin Personal API

Replace the `garminconnect` login with Garmin's Personal API (`personal.connectapi.garmin.com`: a per-user key exchanged by `POST /personalapitoken-service/personal-api/personal-api-key/token` with `Authorization: ApiKey <key>` for a 24-hour bearer JWT, no refresh token, keys last 30 days to a year). Store the key per user on `UserConnection` (key in `refresh_token`, bearer in `access_token`, expiry in `token_expires_at`), accept it from a key field on the pairing page and the credential route, and delete the login, session, token-store, re-auth and MFA code with the `garminconnect` pin. A probe on 2026-10-31 found these work with a bearer token on the scraped paths: `wellness-service/wellness/dailyHeartRate` (702 samples), `usersummary-service/usersummary/daily`, `hrv-service/hrv/{date}`, the activity search and `activity-service/activity/{id}/details`. Garmin's documented paths cover sleep (`sleep-service/sleep/dailySleep`), daily v2 summaries (stress, body battery, respiration and pulse-ox aggregates), SpO2, body battery, fitness age and weight. They 404: `maxmet` (VO2max), `device-service/.../mylastused` (device model; use the activity's device metadata), `dailyStress` (intraday stress and body battery), respiration and training readiness. Unverified: the FIT download (406 to an `application/json` Accept), whether `dailySleep` carries a stage timeline, and history depth (a two-year-old daily summary returned, its heart rate was empty). The probe script lived in a cleaned-up session scratchpad; rewrite it into `scripts/` when this work starts.

## Upstream candidates

These are opened from a clean clone of upstream, never from this checkout: a repository-owner gate on `publish-images.yml`, since forks inherit its nightly schedule.
