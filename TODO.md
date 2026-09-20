# TODO

Open work for this fork, kept current as work lands. Read it at session start. Finished items move to `CHANGELOG.md`; this file carries no history.

## Correctness

- Withings blood pressure never reaches the dashboard. Ingestion is fine (systolic and diastolic rows exist daily, paired by `external_id`), but `body-section.tsx` asks `/summaries/body` with `latest_window_hours: 4`, the route caps it at 24, and the cuff is used once a day, so `blood_pressure` is always null and the tile shows "-". Fix on the fork side: fetch `/timeseries?types=blood_pressure_systolic&types=blood_pressure_diastolic` over the selected period in the body section and render the latest pair with its date, or add a windowless latest-pair lookup to `get_body_summary` as a registered patch. Raising the window alone cannot work.
- The backend test suite needs a container runtime. On 2026-09-20 `podman machine start` failed with `krunkit exited unexpectedly with exit code 2` because Homebrew's podman 6.1.2 passes `--timesync` and the krunkit 1.1.1 under `/opt/podman/bin` (November installer) rejects it. Fix: `brew trust libkrun/krun && brew install krunkit`, then confirm `podman machine start` picks the new binary; or run the suite with `TEST_DATABASE_URL` and `TEST_REDIS_URL` pointing at local services.
- `User.timezone` is never validated as an IANA name, so a typo would make every summary request for that user fail through `func.timezone`. Add a `ZoneInfo` validator on the user write schemas or a UTC fallback in `_resolve_user_timezone`.
- `archival_repository` buckets archived days in UTC while live rows use the user-local date, so the archive and live merge keys will disagree once archival is enabled.
- A chained historical sync does not stop after a Garmin rate limit. Chunks two onwards each fail the cooldown pre-flight, emit their own failed run and `sync.failed` webhook, and the skipped windows are never re-queued.
- The synchronous sync route has no handler for `GarminConnectClientError`, so a cooldown surfaces as an unhandled 500.
- The outer retry in `_call_with_reauth` stacks on garminconnect's own three retries for transient errors, up to nine requests per pair during an outage.
- The `task_id` stamped by `_current_task_id()` in the sync task is consumed by nothing; wire it into cancel or liveness, or drop the delta.
- `create_workout_with_detail` in `event_record_service.py` is an unregistered direct edit to an upstream file; register it as structural or move it under `garmin_connect/`.
- The celery worker does not exit on SIGTERM and is force-killed at the end of its 600-second grace, leaving the app without a worker for ten minutes per deploy under the Recreate strategy. See the CHANGELOG entry for the diagnosis and fix once landed.

## Tooling

- Extend `test_ow_patches_column_drift.py` to `self.model.*` and `SeriesType.*` references and result-dict keys, or retire it in favour of the shadow-drift hash.
- Wire `.pre-commit-config.yaml` per clone and add the language-agnostic gate rules for `.env` files, database files, size caps and binary magic bytes.

## Upstream candidates

These are opened from a clean clone of upstream, never from this checkout: a repository-owner gate on `publish-images.yml`, since forks inherit its nightly schedule; and the stale docstring on `get_daily_activity_aggregates`, which omits `active_time_minutes`.
