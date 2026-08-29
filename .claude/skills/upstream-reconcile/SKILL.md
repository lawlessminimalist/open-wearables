---
name: upstream-reconcile
description: Merge upstream/main into this fork and re-verify the ow-patches. Use when asked to reconcile, merge, sync, catch up with, or rebase onto upstream, when check_upstream.py reports drift, or before refreshing ow-patches/.upstream-baseline. Covers conflict resolution for patched files and the shadow audit that check_upstream.py cannot perform.
---

# Reconciling this fork with upstream

`check_upstream.py` tells you **which files upstream touched**. It cannot tell you
whether our patched copy still matches theirs. That gap is where every silent
regression in this fork has come from, so the audit in Phase 4 is the part that
actually matters — do not skip it because the merge looked clean.

Read `FORK.md` first if you have not. Terms used here (`wholesale-replace`,
`_STANDALONE_PATCHES`, the guard tests) are defined there.

---

## Phase 1 — survey before merging

```bash
git fetch upstream
git log --oneline $(tail -1 ow-patches/.upstream-baseline)..upstream/main | wc -l
python ow-patches/check_upstream.py
```

Record which patches are flagged, and classify:

- **`wholesale-replace` + drift** → highest risk. Our copy shadows upstream and a
  merge will *not* conflict. These need a body diff in Phase 4.
- **`decorate` + drift** → lower risk; re-verify the response shape still has the
  attributes the decorator reads and writes.
- **`structural`** → will conflict normally at merge time.

**Compensate for two known blind spots:**

1. **Frontend and other `structural` patches are not tracked.** A "no drift /
   keep" row means "not checked", not "safe". Check them by hand:
   ```bash
   BASE=$(tail -1 ow-patches/.upstream-baseline)
   git rev-list --count $BASE..upstream/main -- frontend/src/lib/api/types.ts frontend/package.json
   ```
2. **Semantic drift is invisible to it.** It flags a touched file, not a changed
   method body.

## Phase 2 — branch and merge

```bash
git checkout main && git reset --hard origin/main
git checkout -b reconcile/upstream-$(date +%Y-%m-%d)
git merge upstream/main --no-edit
```

Expect conflicts. Resolve, then `git add` each file — files stay listed as
unmerged until staged, even with no markers left.

## Phase 3 — resolving conflicts

**Both sides added something different at the same spot** (imports, dependency
lists, a new method): keep both.

**Upstream renamed a type or symbol we reference:** adopt the rename and re-apply
our change on top. Never reintroduce the old name to make the conflict go away —
it compiles and then fails at runtime.

**Upstream rewrote a file wholesale** (common for frontend components): do *not*
hand-merge hunk by hunk. Take upstream's body and re-apply the fork's change onto
it — this is the rebase procedure `PATCHES.md` describes:

```bash
git checkout --theirs -- <file>
# then re-apply the fork's edits, guided by:
git diff $(git merge-base origin/main upstream/main) origin/main -- <file>
```

That diff is the authoritative statement of what the fork changed in that file.
Re-apply it deliberately rather than from memory.

**Upstream deleted a file we modified:** decide whether the fork's reason still
applies. If the change encoded a real requirement, that requirement needs a new
home — do not just accept the deletion and lose it.

**Upstream's tests may assert behaviour a patch deliberately changes.** Update the
case, add an inline `FORK DIVERGENCE` comment explaining why, and record it as
`structural` in PATCHES.md so it conflicts loudly next time instead of being
silently reverted.

## Phase 4 — the shadow audit (the important part)

For **every** `wholesale-replace` patch flagged in Phase 1, diff the patch's
replacement body against upstream's *current* body and answer:

1. Is the intended fork change the **only** difference?
2. What has upstream added that our copy lacks? Look specifically for:
   - new columns in a SELECT / GROUP BY / returned dict
   - new response fields
   - de-duplication flags (`is_daily_total`, `prefer_daily_sum`)
   - new joins or LATERALs, N+1 fixes, provider grouping
   - a constant lookup replacing an inline mapping
3. Verdict: **KEEP AS-IS** / **NEEDS REBASE** (say exactly what to re-apply) /
   **SHOULD RETIRE** (upstream now does it — check the patch's `retire_when`).

This is independent per patch, so it parallelises well across subagents. Give each
one the patch file, the upstream file and method, and ask for that verdict.

A dropped column is the classic outcome and is invisible at runtime: every
consumer reads it with `.get()`, so the API just returns `null` forever.

Then confirm the patches are not merely loadable but **actually installed**:

```bash
cd backend && uv run pytest tests/test_ow_patches_installed.py \
                           tests/test_ow_patches_column_drift.py \
                           tests/test_ow_patches_guard.py -q
```

If you changed which symbols are patched, `test_ow_patches_installed.py` will
fail — update `_EXPECTED_PATCHED` and record why, rather than deleting the case.

## Phase 5 — verify

```bash
cd backend
uv run ruff check && uv run ruff format --check && uv run ty check
uv run pytest -q

cd ../frontend
npx --yes pnpm@10.13.1 install --frozen-lockfile
npx --yes pnpm@10.13.1 run lint && npx --yes pnpm@10.13.1 run build
npx --yes prettier@3.9.4 --check "src/**/*.{ts,tsx,js,jsx,json,css,md}"
```

Use the **pinned** tool versions. The committed `node_modules` can resolve a
different prettier than `package.json` pins, and the two disagree about which
files need formatting — CI uses the pin.

Then confirm every patch still installs against the merged tree:

```bash
cd backend && uv run python -c "
import sys; sys.path.insert(0,'..')
import importlib.util
spec=importlib.util.spec_from_file_location('ap','../ow-patches/apply.py')
m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
for k,v in m.apply_patches().items(): print(f'{v!s:5} {k}')
"
```

## Phase 6 — document and land

- Add a `rebased_note` to every patch you touched in `PATCHES.md`, naming the
  upstream commit/PR and what you re-applied.
- Retire anything whose `retire_when` is now satisfied — set the flag `False`,
  set `status: retired`, and write a `retirement_note` covering accepted
  regressions.
- **Only now** refresh the baseline:
  ```bash
  python ow-patches/check_upstream.py --update-baseline
  ```
  Do not refresh it as part of the merge commit. A refreshed baseline on
  unverified patches hides the drift from the next reconcile.
- PR title must satisfy `.github/workflows/pr-validation.yml`: a conventional
  type, and if you use a scope it must be one of `backend, frontend, docs, api,
  mcp, auth, integrations, dashboard, settings, users`. An invented scope such as
  `garmin_connect` fails the check.

## Reporting

State plainly, per patch: kept / rebased (what was re-applied) / retired. Call out
anything you could not verify. A merge that compiles and passes tests is **not**
evidence the patches are still correct — say so explicitly rather than implying
green tests mean the audit passed.
