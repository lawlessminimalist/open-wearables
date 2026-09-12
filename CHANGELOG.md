# Fork changelog

Dated entries, newest first, describing what moved and why so the next session can read this before exploring. Upstream has no CHANGELOG.md, so this file is fork-owned and never conflicts. Entries stay short; the reasoning lives in the linked files.

## 2026-09-13

The agent tooling branch `chore/agent-tooling-improvements` made the drift checker symbol-aware. `check_upstream.py` now records a hash of every replaced method in `.upstream-symbols.json` when the baseline is refreshed, reports a shadow risk only when a method body changed rather than when its file was touched, and gained `--lint` and `--explain`. The new `test_ow_patches_shadow_drift.py` compares those hashes against the working tree so a merged but unaudited upstream change fails CI. The lint forbids patches over fork-owned files, which immediately retired `fix-garmin-connect-activity-hr-samples` into `garmin_connect/workouts.py`. The upstream guard hook gained a test table under `.claude/hooks/cases/`, a repo-scoped `agent_telemetry.py` hook pushes session metrics to the homelab OpenTelemetry collector, a fork-owned `ow-patches.yml` workflow runs the lint and hook tests in CI, older registry notes moved to `PATCHES-archive.md`, and this file, `TODO.md`, `.claude/HARNESS-NOTES.md` and a real `CLAUDE.md` map were added.

The homelab repo gained the `agent-efficiency` Grafana dashboard (`k8s/manifests/monitoring/dashboards/`): sessions in range, tokens per session by model, tool calls per prompt, tool error share, upstream-guard denials, cumulative token, request, tool-call and result-byte series, and a per-session table, all over the `ow_agent_*` counters this repo pushes. Every panel carries turn-end counters forward with `last_over_time`; none uses `rate()`.

The reconcile onto upstream 0.8.0 landed as PR #14 with 46 upstream commits. `fix-pace-null` was rebased because it had shadowed the new `name`, `entry_source` and `intensity` fields; `fix-garmin-connect-rate-limit-backoff` was retired into source after it had hidden the VO2max sync for two weeks; Garmin error classification became type- and status-based after an adversarial review found the library's typed 429 was never recognised; and merge migration `e2c0dda6e505` joined the two Alembic heads.

Two guardrails were added the same day. `block_upstream_pr.py` exists because a reconcile PR was opened on upstream's tracker by mistake (their #1611, closed within a minute). Upstream's `publish-images.yml` was gated to its repository owner and disabled on the fork, where it had failed nightly for weeks.
