---
name: upstream-reconcile
description: Merge upstream/main into this fork and re-verify the ow-patches. Use when asked to reconcile, merge, sync, catch up with, or rebase onto upstream, when check_upstream.py reports drift, or before refreshing ow-patches/.upstream-baseline. Covers conflict resolution for patched files, the symbol-level shadow audit, and the adversarial review that closes a reconcile.
---

# Reconciling this fork with upstream

`check_upstream.py` now tells you which replaced methods upstream changed, not just which files it touched. A patch whose method hash is unchanged needs no audit; a patch whose method changed must be body-diffed before it is trusted, because a wholesale-replace patch never conflicts at merge time and silently keeps winning with a stale copy. Every silent regression in this fork has come from skipping that step, so do not skip it because the merge looked clean.

Read `FORK.md` first if you have not. It defines wholesale-replace, `_STANDALONE_PATCHES`, the guard tests and the failure modes referenced below. Use `python3` throughout; `python` is not on the macOS PATH.

## Phase 1: survey before merging

```bash
git fetch upstream
git log --oneline $(tail -1 ow-patches/.upstream-baseline)..upstream/main | wc -l
python3 ow-patches/check_upstream.py
```

Read the summary table. A row marked "RE-VERIFY (shadow risk)" names the symbols whose upstream body changed since the baseline; only those need a Phase 4 audit. A row marked "keep (methods unchanged)" means upstream edited the file elsewhere and the patch is safe. A row with an UNHASHED symbol means the baseline was refreshed before that symbol was registered; treat it as changed.

The checker has two blind spots to compensate for by hand. Structural patches, including everything under `frontend/`, are not hashed, so check their files directly:

```bash
BASE=$(tail -1 ow-patches/.upstream-baseline)
git rev-list --count $BASE..upstream/main -- frontend/src/lib/api/types.ts frontend/package.json
```

And the hash covers code, not behaviour: a renamed constant or a moved helper that a patch imports will not change the replaced method's hash but will break the patch at install time, which the installed guard test catches after the merge.

## Phase 2: branch and merge

```bash
git checkout main && git pull --ff-only origin main
git checkout -b reconcile/upstream-$(date +%Y-%m-%d)
git merge upstream/main --no-edit
```

Expect conflicts. Resolve them, then `git add` each file; a file stays listed as unmerged until staged even with no markers left. Read the conflict hunks with `grep -n '^<<<<<<<' <file>` and `sed -n` around them rather than the whole file.

For the lockfiles, take upstream's copy and re-pin the fork-only dependencies rather than hand-merging: `git checkout --theirs -- backend/uv.lock && cd backend && uv lock -P garminconnect==<version production runs>`. The frontend lockfile usually merges cleanly; `pnpm install --frozen-lockfile` in Phase 5 tells you if it did not.

Commit the merge, then immediately run the two cheap checks that catch what the merge tool cannot, before spending anything on audits:

```bash
cd backend && uv run ty check && uv run python scripts/check_migrations.py --base origin/main
```

`ty` found the only semantic merge regression of the 2026-09-13 reconcile in under a minute (a method that started returning a tuple). The migration guard fails on a two-head chain; the fix is an empty merge revision, never a re-pointed one, because the fork's previous head is already recorded on every production database.

## Phase 3: resolving conflicts

When both sides added something different at the same spot (imports, dependency lists, a new route), keep both. When upstream renamed a type or symbol the fork references, adopt the rename and re-apply the fork's change on top; never reintroduce the old name to make the conflict go away, because it compiles and then fails at runtime. When upstream rewrote a file wholesale, which is common for frontend components, do not hand-merge hunk by hunk: take upstream's body with `git checkout --theirs -- <file>` and re-apply the fork's change guided by `git diff $(git merge-base origin/main upstream/main) origin/main -- <file>`, which is the authoritative statement of what the fork changed there.

When upstream deleted a file the fork modified, decide whether the fork's reason still applies; a real requirement needs a new home rather than being lost with the deletion. When upstream's tests assert behaviour a patch deliberately changes, update the case, add an inline `FORK DIVERGENCE` comment, and record it as structural in `PATCHES.md` so it conflicts loudly next time instead of being silently reverted.

## Phase 4: the shadow audit

For every patch the Phase 1 report marked as a changed symbol, run `python3 ow-patches/check_upstream.py --explain <patch_id>`. It prints the exact `git diff` and `git show` commands for each changed method. Then either do the body diff yourself for a small method, or spawn one subagent per patch using the template in `audit-prompt.md` beside this file. The template exists so the roughly ninety thousand tokens an audit costs are spent on the diff and not on the agent rediscovering the repo. Do not spawn audits for patches whose methods are unchanged.

Each audit answers four questions. Is the intended fork change the only difference? What has upstream added that the patch lacks, looking specifically for new response fields and kwargs, new columns in a SELECT or GROUP BY or result dict, de-duplication flags, new joins or LATERALs, and constants replacing inline mappings? What did the fork add that upstream lacks, which is the reverse question and the easiest to skip because copying upstream's body wholesale looks like the careful thing to do? Watch for kwargs that vanish, such as `source=`, `device_model=`, `is_daily_total=` and `zone_offset=`; a persisted-row constructor missing `source=` mints a second `data_source` row and splits a provider's history with no error, which happened on 2026-08-29. And is `retire_when` now satisfied?

The verdict is KEEP AS-IS, NEEDS REBASE with the exact code to re-apply, or SHOULD RETIRE. When a subagent's report ends with a "consider also" note, act on it or write down in `PATCHES.md` why not; the `vo2_max` identity split was flagged in exactly such a note and left unactioned.

A dropped column is the classic outcome and is invisible at runtime because every consumer reads it with `.get()`, so the API returns null forever. Then confirm the patches are not merely loadable but installed:

```bash
cd backend && uv run pytest tests/test_ow_patches_installed.py tests/test_ow_patches_column_drift.py \
  tests/test_ow_patches_identity_drift.py tests/test_ow_patches_shadow_drift.py tests/test_ow_patches_guard.py -q
```

The shadow-drift test is expected to be red at this point for every patch whose method changed, and stays red until Phase 6 refreshes the baseline. If you changed which symbols are patched, the installed test fails; update `_EXPECTED_PATCHED` and record why rather than deleting the case.

## Phase 5: verify

```bash
cd backend
uv run ruff check && uv run ruff format --check && uv run ty check
uv run pytest -q
cd ../frontend
npx --yes pnpm@10.13.1 install --frozen-lockfile
npx --yes pnpm@10.13.1 run lint && npx --yes pnpm@10.13.1 run build
npx --yes prettier@3.9.4 --check "src/**/*.{ts,tsx,js,jsx,json,css,md}"
```

Use the pinned tool versions; the committed `node_modules` can resolve a different prettier than the pin and the two disagree about which files need formatting. Run the test suite in the foreground or with the `cd` inside the background command, and never pipe it through `tail`; a background run started from the wrong directory once failed to spawn pytest and the pipe reported success. Read the summary line.

Then confirm every patch installs against the merged tree:

```bash
cd backend && uv run python -c "
import sys; sys.path.insert(0,'..')
import importlib.util
spec=importlib.util.spec_from_file_location('ap','../ow-patches/apply.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
for k,v in m.apply_patches().items(): print(f'{v!s:5} {k}')
"
```

## Phase 6: document, refresh, land

Add an `audit_note` or `rebased_note` dated today to every patch you touched in `PATCHES.md`, naming the upstream commit and what you re-applied. Retire anything whose `retire_when` is now satisfied by setting its flag to False, its status to retired, and writing a `retirement_note` covering accepted regressions. Move dated notes from earlier reconciles to `PATCHES-archive.md` so the registry stays small. Update `CHANGELOG.md` and `TODO.md`.

Only now refresh the baseline. This also re-records every symbol hash, which is what turns the shadow-drift test green:

```bash
python3 ow-patches/check_upstream.py --update-baseline
python3 ow-patches/check_upstream.py --lint
```

Do not refresh it as part of the merge commit; a refreshed baseline on unverified patches hides the drift from the next reconcile. The lint must pass: it rejects a patch over a fork-owned file, a flag that disagrees with a status, and an unhashed symbol.

Open the pull request on the fork only. The upstream guard hook denies any `gh pr create` without an explicit fork target, so write `gh pr create --repo lawlessminimalist/open-wearables --base main --head <branch> ...` and confirm the printed URL is under the fork. The title must satisfy `.github/workflows/pr-validation.yml`: a conventional type, and if you use a scope it must be one of backend, frontend, docs, api, mcp, auth, integrations, dashboard, settings, users. A body that cites an upstream PR by full slug must go in `--body-file`.

## Phase 7: adversarial review

Once CI is green, run `/code-review <merge-commit>..HEAD high` against the fork-authored commits only, not the upstream content. On 2026-09-13 this found five real bugs in code that had seven test classes, because the tests were shaped around the implementation. Fix what is cheap and correct, record the rest in `TODO.md`, and add the outcome to the PR description.

## Reporting

State plainly, per patch: kept, rebased with what was re-applied, or retired. Call out anything you could not verify. A merge that compiles and passes tests is not evidence the patches are still correct; say so explicitly rather than implying green tests mean the audit passed.
