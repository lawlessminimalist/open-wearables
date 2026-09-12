# CLAUDE.md

This is the entry point for any agent working in this fork. The three `AGENTS.md` files at the root and under `backend/` and `frontend/` are upstream's and describe the project generally. This file is the fork's map: where the fork-owned parts live, and the standing rules that exist because something broke. It is deliberately short; the detail lives in the files it points at.

## Read first

Before any backend change, a new provider, or anything under `ow-patches/`, read [FORK.md](./FORK.md). It explains the patch system, the hard rules, and the six silent failure modes the system has actually produced. Before merging upstream, use the `upstream-reconcile` skill under `.claude/skills/`; the audit in its fourth phase is the part that matters and must not be skipped because the merge looked clean. Before touching `.claude/` or a hook, read [.claude/HARNESS-NOTES.md](./.claude/HARNESS-NOTES.md) for the traps the harness itself sets. Before adding a health metric or a target, read the deployment's `LONGEVITY.md` in the homelab repository (`k8s/manifests/open-wearables/`), which records which signals carry outcome evidence; it is deliberately kept out of this public fork. At the start of a session, read [TODO.md](./TODO.md) for open work and [CHANGELOG.md](./CHANGELOG.md) for what recently moved, and keep both current as work lands rather than at the end.

## Layout of the fork-owned parts

The patch system lives in `ow-patches/`: the registry `PATCHES.md`, the installer `apply.py`, the drift checker `check_upstream.py` with its baseline and `.upstream-symbols.json`, and `PATCHES-archive.md` for dated notes from earlier reconciles. The credential-based Garmin provider under `backend/app/services/providers/garmin_connect/` is fork-only and is edited directly, never patched. The guard tests are `backend/tests/test_ow_patches_*.py`: installed, column drift, identity drift, shadow drift, and the image guard. Under `.claude/hooks/` sit `block_upstream_pr.py`, the fail-closed guard against touching upstream's tracker, `agent_telemetry.py`, the repo-scoped metrics push, and the hook test table in `cases/` with its runner. The fork-owned CI is `.github/workflows/publish-ghcr.yml` and `ow-patches.yml`; upstream's `ci.yml` is untouched and its `publish-images.yml` carries only a repository-owner gate. `Dockerfile.ow-patches` and `scripts/build-push.sh` build the image overlay that ships the patches.

## Standing rules

Never patch a file the fork owns. `check_upstream.py --lint` enforces this because two patches over `garmin_connect/` have already shadowed the fork's own later edits without any error. Treat a wholesale-replace patch as stale until its recorded method hash matches upstream: `test_ow_patches_shadow_drift.py` fails on a changed upstream method, and the response is to audit the patch and only then run `check_upstream.py --update-baseline`. Green tests are not evidence that a patch is correct.

Nothing reaches upstream's tracker from this checkout. The hook and the `GH_REPO` setting enforce it, and every `gh pr` command still passes `--repo lawlessminimalist/open-wearables` explicitly. Upstream contributions are opened from a clean clone of upstream.

Verify rather than assume. Run the command and read its summary line. Never pipe a test runner through `tail`, and put any `cd` inside a background command, because a background command does not inherit a later directory change. Big tool results never enter the main context: anything over about fifteen kilobytes goes to an Explore subagent or through `grep`, `head` and `cut`, and conflicts are read as hunks rather than whole files.

Fan out subagents only for independent read-only work such as the audit of a patch whose method actually changed. Never delegate mutations, synthesis, or anything destructive. Use the audit template in the reconcile skill so the token budget is spent on the diff rather than on the agent rediscovering the repo, and act on every "consider also" note or record why not.

Every reconcile ends with an adversarial review of the fork-authored commits at high effort once CI is green. On 2026-09-13 that review found five real bugs in code that had seven test classes. Claims carry evidence: say "not verified" when something is not.

Telemetry is repo-scoped. Never set `OTEL_EXPORTER_OTLP_*` or `CLAUDE_CODE_ENABLE_TELEMETRY` in project settings, since they would hijack the harness's own user-level export. The agent metrics path uses `OW_AGENT_*` variables only and posts to the homelab OpenTelemetry collector.

Commits use a conventional type with one of the allowed scopes: backend, frontend, docs, api, mcp, auth, integrations, dashboard, settings, users. Pull requests fill in the template's "AI usage" section honestly. For Cursor and other agents, `.cursor/rules/` points at upstream's `AGENTS.md`.
