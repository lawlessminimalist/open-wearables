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

- `winning_sleep_record_ids` (`event_record_repository.py`, used by the `/events/sleep` de-dup) still buckets null-offset rows by UTC date, so its per-night winner can be decided on a different date from `fix-sleep-summary-utc-bucketing`'s summaries. Widen that patch or add one.

## Frontend (SvelteKit, since upstream 0.10.0)

Upstream replaced the React dashboard; the fork's React edits were dropped at the 2026-10-19 reconcile (`frontend-display-timezone` retired, see `ow-patches/PATCHES.md`). To port onto the Svelte portal:

- Display timezone: the portal formats every datetime with `timeZone: 'UTC'` (`src/lib/utils/format.ts`, `src/lib/syncs/format.ts`, `src/lib/charts/ticks.ts`). Render in the viewed user's `User.timezone` (echoed on sleep and activity summaries by the backend patches), with an optional per-viewer override.
- `User.timezone` field on the user edit form (the API accepts it on PATCH `/users/{id}`).
- Credential-provider connect: the portal lists only `has_cloud_api` providers for pairing, so `garmin_connect` can only be connected with `POST /api/v1/users/{user_id}/connections/garmin_connect`. The old providers tab also showed credential providers in their own section.
- `basal_calories_kcal` on the activity view.

## Tooling

- Extend `test_ow_patches_column_drift.py` to `self.model.*` and `SeriesType.*` references and result-dict keys, or retire it in favour of the shadow-drift hash.
- Wire `.pre-commit-config.yaml` per clone and add the language-agnostic gate rules for `.env` files, database files, size caps and binary magic bytes.
- The podman/krunkit fix recorded on 2026-09-20 (`brew trust libkrun/krun && brew install krunkit`) was never applied on this machine, yet `podman machine start` and the full backend suite ran clean on 2026-09-22. The root cause of the original `krunkit exited unexpectedly with exit code 2` is unconfirmed, so if it recurs here or on another clone, apply that fix rather than assuming it is permanently resolved.

## Upstream candidates

These are opened from a clean clone of upstream, never from this checkout: a repository-owner gate on `publish-images.yml`, since forks inherit its nightly schedule; and the stale docstring on `get_daily_activity_aggregates`, which omits `active_time_minutes`.
