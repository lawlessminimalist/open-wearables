# Claude Code harness notes

These are traps the harness itself sets, as distinct from the fork's own failure modes recorded in FORK.md section 3. Each one was hit in this repo or in a sibling repo on the same machine; none is theoretical.

## Hooks

Hooks hot-reload mid-session. An edit to `.claude/settings.json` or to a hook script applies to the very next tool call, so a broken or over-broad hook bites immediately. The first bash version of the upstream guard denied its own author's commit because the commit message mentioned the upstream slug.

Write any hook that inspects shell commands in Python from the first draft. The bash version went through three rewrites: quote-blind splitting turned a semicolon inside a commit message into a phantom `gh pr create` segment, and macOS's BSD `sed` rejects the GNU label syntax. Test the hook from its case files under `.claude/hooks/cases/` with `make hook-test` before relying on it.

A PreToolUse deny is the only fail-closed shape. Exit code 2 or a `permissionDecision` of deny blocks the call; any other non-zero exit is a non-blocking error, which is an allow. The wrapper therefore emits a deny when `python3` is missing rather than exiting non-zero.

`gh` defaults a fork to its parent repository. Without `--repo`, `gh pr create` from this checkout targets the-momentum/open-wearables, which happened once as their #1611. FORK.md section 5 describes the three layers that now prevent it.

## Telemetry

Never set `OTEL_EXPORTER_OTLP_*` or `CLAUDE_CODE_ENABLE_TELEMETRY` at project scope. The harness's own OpenTelemetry export is a user-level concern, and project settings would override and hijack it. This repo's agent metrics use a separate push path in `.claude/hooks/agent_telemetry.py`, configured only through `OW_AGENT_*` variables, and go to the homelab OpenTelemetry collector rather than to Mimir directly.

Series are written only at turn ends, so Grafana panels over `ow_agent_*` must use `last_over_time(...[range])`, and rate-style panels show nothing for a fresh session because `increase()` needs two samples. Mimir cannot delete a series, so never push a truncated or invented session id. A probe series named `ow_agent_probe_total` with `session_id="probe"` was pushed on 2026-09-13 while validating the endpoint and will remain until retention drops it.

Cost is an estimate priced per request from the transcript's usage block at the list prices in the hook's `PRICING` table, with cache writes priced by their real 5m and 1h split. The table is copied from dhlaw-explorations, which read platform.claude.com on 2026-09-03; when a new model appears, add its price there and nowhere else, because a model without an entry is deliberately reported as unpriced rather than guessed. Tool cost attributes the cost of the request that issued a tool call to that tool, split evenly when one request issued several, so it measures what a turn paid to decide on and carry the call, not the tool's runtime.

Subagent usage is invisible to the transcript-based counters because their transcripts are sidechains, so the hook reads the `<subagent_tokens>` figure from each Agent completion notice into `ow_agent_subagent_tokens_total`. It is a plain token count with no type split and is therefore not priced; treat it as a lower bound on what a fan-out cost.

## Shell quirks that cost turns

The Bash tool runs zsh. Three of its expansions turned into wasted round trips during the 2026-09-13 session, each showing up as an "Exit code 1" tool error that had to be read and retried. A bare word beginning with `=` such as `echo =====` is equals-expansion and fails with "not found", so quote separators or use `printf`. An unquoted glob that matches nothing, such as `--include=*.py` or `backend/app/core/*.py` for a directory that does not exist, aborts the whole command with "no matches found" before anything runs, so quote globs passed to `grep` and `find` and check a path exists before globbing under it. And `grep` returns 1 when it finds nothing, which is reported as a failure when it is the last command in a chain, so end exploratory chains with `|| true` or put the grep first.

Two read paths are refused by the permission classifier in this checkout, and retrying them wastes a turn each time: shell access into the production database with `kubectl exec` and `psql`, and reading Grafana's admin secret. The sanctioned routes are the two wrappers under `.claude/bin/`, which `.claude/settings.local.json` allows by prefix. `ow-sql` runs psql inside the postgres pod with `default_transaction_read_only` forced on by the server, so a write fails there rather than in the script, and refuses any statement that names the flag. `grafana get` and `grafana sql` issue GET requests and datasource queries only and never print the credential. For schema questions without touching production, migrate a throwaway Postgres with the backend's Alembic revisions, which is also how dashboard SQL is validated before commit.

## Running things

Background commands do not inherit a later `cd`. A background pytest launched from the repo root after the working directory had moved failed to spawn, and a trailing `| tail` masked the exit code, which produced a false "suite green" report. Put the `cd` inside the command and never pipe a test runner's output; assert on its summary line.

`python` is not on the macOS PATH, so skills and docs say `python3`. `cat -A` is GNU-only; use `cat -vet`. Chained `sleep` is blocked by the harness; wait on a background command or use a Monitor loop instead.

## Tokens

A subagent audit costs around ninety thousand tokens. Spawn one only for a patch whose method actually changed according to the symbol-level drift check, not for a patch whose file was merely touched; two of the three audits in the 2026-09-13 reconcile were file-level false positives. Never read `ow-patches/PATCHES.md` in full: `make patch-list` prints one line per patch and `make patch-show ID=<patch_id>` prints one entry, neither fetching upstream; the registry's two largest reads in the 2026-09-13 session were 20 KB and 17 KB. Dated notes older than the current reconcile belong in `PATCHES-archive.md`. Read conflict hunks with `grep -n '^<<<<<<<'` rather than the whole conflicted file.
