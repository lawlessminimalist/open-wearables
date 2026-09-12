#!/usr/bin/env python3
"""Check whether upstream has caught up to — or silently diverged from — any of
our local fork patches.

Run from anywhere:
    python3 ow-patches/check_upstream.py                    # drift report (exit 1 on shadow risk)
    python3 ow-patches/check_upstream.py --lint             # registry lint (exit 1 on violations)
    python3 ow-patches/check_upstream.py --update-baseline  # after reconciling: SHA + symbol hashes
    python3 ow-patches/check_upstream.py --explain <patch>  # git commands to diff a drifted symbol

The script runs THREE independent checks per patch:

  1. EQUIVALENCE (heuristic) — greps upstream/main for the patch's
     `upstream_equivalent_check` marker. A hit *suggests* upstream may have
     shipped its own version of the fix. Weak signal.

  2. FILE DRIFT (deterministic, coarse) — for every file the patch depends on
     (parsed from the `file:` field), asks `git log <baseline>..upstream/main --
     <file>`: has upstream touched it since we last reconciled?

  3. SYMBOL DRIFT (deterministic, fine) — for every method a wholesale-replace or
     decorate patch names in `symbol:`, compares a hash of upstream's CURRENT
     method body (or signature, for decorate) against the hash recorded in
     `.upstream-symbols.json` at the last `--update-baseline`. Only a changed
     symbol is a SHADOW RISK; a file that upstream edited elsewhere is reported
     as "file touched, replaced methods unchanged" and needs no audit.

     Why: in the 2026-09-13 reconcile two of three file-drift flags were false
     positives (upstream edited other methods in the same file) and each cost a
     full body-diff audit. The symbol hash removes that noise. A patch with NO
     recorded hash is treated as drifted (conservative) until the baseline is
     refreshed.

         A monkey-patch that WHOLESALE-REPLACES an upstream method does NOT
         produce a git merge conflict (we never edit the upstream source file),
         so a `git merge` is silent. If upstream rewrites that method, our patch
         keeps shadowing it with a stale copy — silently dropping upstream's
         improvements (this is exactly how avg_hrv_rmssd_ms went null after
         upstream rewrote get_sleep_summaries, and how fix-pace-null returned
         name/entry_source/intensity as null after #1510).

`--lint` enforces the registry rules that have each been broken at least once:
  - an active non-structural patch must not target a fork-owned file
    (FORK.md section 2 — the file must exist at upstream/main); list fork-owned
    structural companions under `structural_files:` instead
  - `PATCHES_ENABLED` in apply.py must agree with `status:` (retired => False)
  - every `symbol:` of an active wholesale/decorate patch must resolve to a
    function at upstream/main
  - every active wholesale/decorate patch must have recorded symbol hashes

The baseline SHA lives in `ow-patches/.upstream-baseline`; symbol hashes in
`ow-patches/.upstream-symbols.json`. Refresh both with `--update-baseline` once
every flagged patch has been re-verified after a merge — and only then. The
pytest guard `backend/tests/test_ow_patches_shadow_drift.py` compares the same
hashes against the WORKING TREE, so a merged-but-unaudited upstream change fails
CI until the audit is done and the baseline refreshed.

Recommendations are advisory only — nothing is auto-retired. All retirements and
rebases are manual: see ../FORK.md and the `upstream-reconcile` skill.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from symbol_hash import hash_symbols_in_source, parse_symbols, source_at_ref  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
OW_DIR = Path(__file__).resolve().parent
PATCHES_MD = OW_DIR / "PATCHES.md"
APPLY_PY = OW_DIR / "apply.py"
BASELINE_FILE = OW_DIR / ".upstream-baseline"
SYMBOLS_FILE = OW_DIR / ".upstream-symbols.json"
UPSTREAM_REMOTE = "upstream"
UPSTREAM_BRANCH = "main"
UPSTREAM_REF = f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}"

# Extract repo-relative source paths from a free-text `file:` field, ignoring
# any prose / parentheticals (e.g. "foo.py (composed via bar)").
_PATH_RE = re.compile(r"(?:backend|frontend|mcp|\.github)/[\w./-]+\.\w+")

HASHED_KINDS = {"wholesale-replace", "decorate"}


@dataclass
class Patch:
    patch_id: str
    status: str
    file: str
    symbol: str
    retire_when: str
    upstream_equivalent_check: str
    replacement_kind: str
    structural_files: str = ""
    raw: dict[str, str] = field(default_factory=dict)

    def is_active(self) -> bool:
        return self.status in {"local_only", "upstream_candidate"}

    def is_wholesale_replace(self) -> bool:
        # Default-on: if a patch hasn't declared its kind, treat it as the
        # dangerous (shadow-prone) kind so it gets the loud warning.
        return self.replacement_kind not in {"decorate", "structural", "standalone"}

    def is_hashed(self) -> bool:
        return self.replacement_kind in HASHED_KINDS or self.is_wholesale_replace()

    def hash_kind(self) -> str:
        return "signature" if self.replacement_kind == "decorate" else "body"

    def target_paths(self) -> list[str]:
        return _PATH_RE.findall(self.file) if self.file else []

    def structural_paths(self) -> list[str]:
        return _PATH_RE.findall(self.structural_files) if self.structural_files else []

    def symbols(self) -> list[str]:
        return parse_symbols(self.symbol) if self.symbol and self.symbol != "<missing>" else []


def _run(cmd: list[str], *, cwd: Path = REPO_ROOT, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd), check=False, text=True, capture_output=capture)


def ensure_upstream_remote() -> None:
    res = _run(["git", "remote"])
    if UPSTREAM_REMOTE in set(res.stdout.split()):
        return
    print(f"ERROR: git remote '{UPSTREAM_REMOTE}' is not configured.", file=sys.stderr)
    print("", file=sys.stderr)
    print("Add it (replace the URL with your actual upstream):", file=sys.stderr)
    print(f"  git remote add {UPSTREAM_REMOTE} https://github.com/the-momentum/open-wearables.git", file=sys.stderr)
    print(f"  git fetch {UPSTREAM_REMOTE}", file=sys.stderr)
    sys.exit(2)


def fetch_upstream() -> None:
    print(f"Fetching {UPSTREAM_REMOTE}/{UPSTREAM_BRANCH} …")
    res = _run(["git", "fetch", UPSTREAM_REMOTE, UPSTREAM_BRANCH])
    if res.returncode != 0:
        print(f"ERROR: git fetch failed:\n{res.stderr}", file=sys.stderr)
        sys.exit(2)


# Each entry is `- key: value`, with a blank line between patches and `---` separators.
_FIELD_RE = re.compile(r"^-\s+([\w_]+):\s+(.*)$")


def parse_patches_md(path: Path) -> list[Patch]:
    if not path.exists():
        print(f"ERROR: PATCHES.md not found at {path}", file=sys.stderr)
        sys.exit(2)
    patches: list[Patch] = []
    current: dict[str, str] = {}
    for raw_line in path.read_text().splitlines():
        line = raw_line.rstrip()
        if line.startswith("---"):
            if current.get("patch_id"):
                patches.append(_make_patch(current))
            current = {}
            continue
        m = _FIELD_RE.match(line)
        if m:
            current[m.group(1).strip()] = m.group(2).strip()
    if current.get("patch_id"):
        patches.append(_make_patch(current))
    return patches


def _make_patch(d: dict[str, str]) -> Patch:
    return Patch(
        patch_id=d.get("patch_id", "<missing>"),
        status=d.get("status", "<missing>"),
        file=d.get("file", "<missing>"),
        symbol=d.get("symbol", "<missing>"),
        retire_when=d.get("retire_when", "<missing>"),
        upstream_equivalent_check=d.get("upstream_equivalent_check", "<missing>"),
        replacement_kind=d.get("replacement_kind", "wholesale-replace"),
        structural_files=d.get("structural_files", ""),
        raw=dict(d),
    )


def grep_upstream(pattern: str) -> list[tuple[str, str]]:
    """Return (file:line, content) hits for pattern in the upstream/main tree.

    `<path-substring>::<text>` restricts matches to files whose path contains the substring.
    """
    path_filter: str | None = None
    if "::" in pattern:
        path_filter, pattern = pattern.split("::", 1)
    cmd = ["git", "grep", "-n", "-F", pattern, UPSTREAM_REF]
    if path_filter:
        cmd.extend(["--", f"*{path_filter}*"])
    res = _run(cmd)
    if res.returncode not in (0, 1):  # 1 = no matches
        print(f"WARN: git grep failed for pattern {pattern!r}:\n{res.stderr}", file=sys.stderr)
        return []
    hits: list[tuple[str, str]] = []
    for raw in res.stdout.splitlines():
        parts = raw.split(":", 3)  # <ref>:<file>:<line>:<content>
        if len(parts) == 4:
            hits.append((f"{parts[1]}:{parts[2]}", parts[3].strip()))
    return hits


def read_baseline() -> str | None:
    if not BASELINE_FILE.exists():
        return None
    for raw in BASELINE_FILE.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            return line.split()[0]
    return None


def write_baseline(sha: str) -> None:
    header = (
        "# The upstream/main commit we last fully reconciled the patches against.\n"
        "# check_upstream.py compares this against the current upstream/main to detect\n"
        "# whether upstream has TOUCHED any file a patch depends on (drift), and\n"
        "# .upstream-symbols.json records the replaced methods' body hashes at that\n"
        "# commit. After you merge upstream and re-verify every flagged patch, refresh\n"
        "# both with:\n"
        "#\n"
        "#     python3 ow-patches/check_upstream.py --update-baseline\n"
        "#\n"
    )
    BASELINE_FILE.write_text(f"{header}{sha}\n")


def read_symbols() -> dict:
    if not SYMBOLS_FILE.exists():
        return {}
    try:
        return json.loads(SYMBOLS_FILE.read_text())
    except json.JSONDecodeError as exc:
        print(f"WARN: {SYMBOLS_FILE.name} is not valid JSON ({exc}); treating as empty.", file=sys.stderr)
        return {}


def commits_touching(baseline: str, paths: list[str]) -> list[tuple[str, str]]:
    if not baseline or not paths:
        return []
    res = _run(["git", "log", "--oneline", "--no-decorate", f"{baseline}..{UPSTREAM_REF}", "--", *paths])
    if res.returncode != 0:
        print(f"WARN: git log failed for paths {paths}:\n{res.stderr}", file=sys.stderr)
        return []
    out: list[tuple[str, str]] = []
    for line in res.stdout.splitlines():
        sha, _, subject = line.partition(" ")
        if sha:
            out.append((sha, subject))
    return out


def _exists_at(ref: str, path: str) -> bool:
    return _run(["git", "cat-file", "-e", f"{ref}:{path}"]).returncode == 0


def missing_upstream_paths(baseline: str | None, paths: list[str]) -> list[str]:
    """Paths upstream REMOVED since baseline (existed at baseline, gone now)."""
    if not baseline:
        return []
    return [p for p in paths if _exists_at(baseline, p) and not _exists_at(UPSTREAM_REF, p)]


# ---------------------------------------------------------------------------
# Symbol hashing
# ---------------------------------------------------------------------------


def resolve_symbols_at_ref(patch: Patch, ref: str) -> dict[str, dict]:
    """{symbol: {"file": path, "hash": h, "kind": body|signature}} for `patch` at `ref`.

    A symbol that no upstream file of the patch defines gets hash None (reported
    by --lint; treated as drift by the report).
    """
    kind = patch.hash_kind()
    out: dict[str, dict] = {}
    sources: dict[str, str | None] = {p: source_at_ref(REPO_ROOT, ref, p) for p in patch.target_paths()}
    for sym in patch.symbols():
        found = None
        for path, src in sources.items():
            if src is None:
                continue
            h = hash_symbols_in_source(src, [sym], signature_only=(kind == "signature"))[sym]
            if h is not None:
                found = {"file": path, "hash": h, "kind": kind}
                break
        out[sym] = found or {"file": None, "hash": None, "kind": kind}
    return out


def symbol_drift(patch: Patch, recorded: dict) -> tuple[list[str], list[str], list[str]]:
    """(changed, unhashed, unchanged) symbols for `patch` against upstream/main now."""
    current = resolve_symbols_at_ref(patch, UPSTREAM_REF)
    rec = recorded.get(patch.patch_id, {})
    changed, unhashed, unchanged = [], [], []
    for sym, info in current.items():
        before = (rec.get(sym) or {}).get("hash")
        if before is None:
            unhashed.append(sym)
        elif info["hash"] != before:
            changed.append(sym)
        else:
            unchanged.append(sym)
    return changed, unhashed, unchanged


# ---------------------------------------------------------------------------
# Evaluation / report
# ---------------------------------------------------------------------------


def evaluate_patch(patch: Patch, baseline: str | None, recorded: dict) -> dict:
    hits = grep_upstream(patch.upstream_equivalent_check)
    paths = patch.target_paths()
    drift = commits_touching(baseline, paths) if baseline else []
    gone = missing_upstream_paths(baseline, paths) if paths else []
    changed: list[str] = []
    unhashed: list[str] = []
    unchanged: list[str] = []
    if drift and patch.is_hashed():
        changed, unhashed, unchanged = symbol_drift(patch, recorded)
    return {
        "patch_id": patch.patch_id,
        "status": patch.status,
        "upstream_has_equivalent": bool(hits),
        "hits": hits,
        "retire_when": patch.retire_when,
        "paths": paths,
        "drift": drift,
        "gone": gone,
        "wholesale": patch.is_wholesale_replace(),
        "hashed": patch.is_hashed(),
        "sym_changed": changed,
        "sym_unhashed": unhashed,
        "sym_unchanged": unchanged,
    }


RECO_RETIRE = "review-for-retirement"
RECO_KEEP = "keep"
RECO_NOOP = "no-op (already retired)"
RECO_SHADOW = "RE-VERIFY (shadow risk)"
RECO_DRIFT = "re-verify (target moved)"
RECO_FILE_ONLY = "keep (methods unchanged)"
RECO_BROKEN = "BROKEN (target gone)"


def recommendation(result: dict) -> str:
    if result["status"] == "retired":
        return RECO_NOOP
    if result["gone"]:
        return RECO_BROKEN
    if result["drift"]:
        if result["hashed"]:
            if result["sym_changed"] or result["sym_unhashed"]:
                return RECO_SHADOW if result["wholesale"] else RECO_DRIFT
            return RECO_FILE_ONLY
        return RECO_SHADOW if result["wholesale"] else RECO_DRIFT
    if result["upstream_has_equivalent"]:
        return RECO_RETIRE
    return RECO_KEEP


def explain(patch: Patch, baseline: str | None, recorded: dict) -> None:
    """Print the exact git commands an auditor needs for each drifted symbol."""
    changed, unhashed, unchanged = symbol_drift(patch, recorded)
    current = resolve_symbols_at_ref(patch, UPSTREAM_REF)
    print(f"{patch.patch_id}  ({patch.replacement_kind}, hash={patch.hash_kind()})")
    for sym, info in current.items():
        state = "CHANGED" if sym in changed else ("UNHASHED" if sym in unhashed else "unchanged")
        print(f"  {state:9} {sym}  [{info['file']}]  {info['hash']}")
        if state != "unchanged" and info["file"] and baseline:
            print(f"     git diff {baseline} {UPSTREAM_REF} -- {info['file']}")
            print(f"     git show {UPSTREAM_REF}:{info['file']}   # current upstream body")
    lp = patch.raw.get("local_patch_file", "")
    if lp:
        print(f"  patch file: {lp}")


# ---------------------------------------------------------------------------
# Lint
# ---------------------------------------------------------------------------


def _patches_enabled_from_apply() -> dict[str, bool]:
    """Read PATCHES_ENABLED from apply.py without importing the app."""
    tree = ast.parse(APPLY_PY.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "PATCHES_ENABLED":
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "PATCHES_ENABLED":
                    return ast.literal_eval(node.value)
    return {}


def lint(patches: list[Patch], recorded: dict) -> list[str]:
    problems: list[str] = []
    flags = _patches_enabled_from_apply()
    for p in patches:
        if p.patch_id.startswith("frontend-") or p.raw.get("local_patch_file") is None and p.replacement_kind == "structural":
            pass  # structural entries have no flag
        elif p.patch_id in flags:
            enabled = flags[p.patch_id]
            if p.status == "retired" and enabled:
                problems.append(f"{p.patch_id}: status is retired but PATCHES_ENABLED is True")
            if p.is_active() and not enabled:
                problems.append(f"{p.patch_id}: status is {p.status} but PATCHES_ENABLED is False")
        elif p.replacement_kind != "structural":
            problems.append(f"{p.patch_id}: no PATCHES_ENABLED entry in apply.py")

        if not p.is_active() or p.replacement_kind == "structural":
            continue

        # FORK.md section 2: never patch a file the fork owns. Fork-owned structural
        # companions belong under `structural_files:`.
        for path in p.target_paths():
            if not _exists_at(UPSTREAM_REF, path):
                problems.append(
                    f"{p.patch_id}: target {path} does not exist at {UPSTREAM_REF} — a patch over a "
                    f"fork-owned file shadows the fork's own later edits (FORK.md section 2). Edit the file "
                    f"directly, or move it to `structural_files:` if it is only a structural companion."
                )

        if p.is_hashed():
            if not p.symbols():
                problems.append(f"{p.patch_id}: {p.replacement_kind} patch has no parseable `symbol:` entries")
            resolved = resolve_symbols_at_ref(p, UPSTREAM_REF)
            for sym, info in resolved.items():
                if info["hash"] is None:
                    problems.append(f"{p.patch_id}: symbol {sym} not found in any `file:` at {UPSTREAM_REF}")
                elif (recorded.get(p.patch_id, {}).get(sym) or {}).get("hash") is None:
                    problems.append(
                        f"{p.patch_id}: no recorded hash for {sym} in {SYMBOLS_FILE.name} — run --update-baseline "
                        f"after verifying the patch against upstream"
                    )
    return problems


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> int:
    args = sys.argv[1:]
    ensure_upstream_remote()
    fetch_upstream()
    patches = parse_patches_md(PATCHES_MD)
    if not patches:
        print("No patches parsed from PATCHES.md", file=sys.stderr)
        return 1
    recorded = read_symbols()
    baseline = read_baseline()

    if "--update-baseline" in args:
        sha = _run(["git", "rev-parse", UPSTREAM_REF]).stdout.strip()
        write_baseline(sha)
        symbols: dict[str, dict] = {"_upstream_sha": sha}
        for p in patches:
            if p.is_active() and p.is_hashed():
                symbols[p.patch_id] = resolve_symbols_at_ref(p, UPSTREAM_REF)
        SYMBOLS_FILE.write_text(json.dumps(symbols, indent=2, sort_keys=True) + "\n")
        n = sum(len(v) for k, v in symbols.items() if k != "_upstream_sha")
        print(f"Baseline updated to {UPSTREAM_REF} @ {sha}")
        print(f"Recorded {n} symbol hash(es) for {len(symbols) - 1} patch(es) in {SYMBOLS_FILE.name}")
        print("Drift will now be measured from this commit forward.")
        return 0

    if "--lint" in args:
        problems = lint(patches, recorded)
        if problems:
            print("Registry lint FAILED:")
            for pr in problems:
                print(f"  - {pr}")
            return 1
        print("Registry lint OK: flags agree with status, no fork-owned targets, all symbols resolve and are hashed.")
        return 0

    if "--explain" in args:
        idx = args.index("--explain")
        wanted = args[idx + 1] if idx + 1 < len(args) else ""
        for p in patches:
            if p.patch_id == wanted:
                explain(p, baseline, recorded)
                return 0
        print(f"unknown patch id {wanted!r}", file=sys.stderr)
        return 2

    print()
    print(f"Checking {len(patches)} patch(es) against {UPSTREAM_REF} …")
    if baseline:
        print(f"Drift baseline: {baseline}")
    else:
        print("WARNING: no .upstream-baseline found — drift check skipped.")
        print("         Set one with: python3 ow-patches/check_upstream.py --update-baseline")
    if not recorded:
        print(f"WARNING: no {SYMBOLS_FILE.name} — every drifted hashed patch is reported as a shadow risk.")
    print()

    rows: list[tuple[str, str, str, str, str]] = []
    shadow_risks = 0
    for patch in patches:
        if not patch.is_active():
            rows.append((patch.patch_id, patch.status, "-", "-", RECO_NOOP))
            continue
        result = evaluate_patch(patch, baseline, recorded)
        reco = recommendation(result)

        if result["gone"]:
            print(f"BROKEN — TARGET GONE UPSTREAM: {patch.patch_id}")
            for p in result["gone"]:
                print(f"   missing in {UPSTREAM_REF}: {p}")
            print("   the monkey-patch targets a file upstream renamed/removed — it will mis-apply or no-op.")
            print()
        elif result["drift"]:
            if reco == RECO_FILE_ONLY:
                print(f"FILE TOUCHED, REPLACED METHODS UNCHANGED: {patch.patch_id}  ({patch.replacement_kind})")
                for sym in result["sym_unchanged"]:
                    print(f"   unchanged: {sym}")
                print("   no audit needed — upstream edited other parts of the file.")
            else:
                label = (
                    "SHADOW RISK — UPSTREAM CHANGED A WHOLESALE-REPLACED METHOD"
                    if result["wholesale"]
                    else "UPSTREAM TOUCHED TARGET"
                )
                print(f"{label}: {patch.patch_id}  ({patch.replacement_kind})")
                for p in result["paths"]:
                    print(f"   target: {p}")
                for sym in result["sym_changed"]:
                    print(f"   CHANGED symbol: {sym}")
                for sym in result["sym_unhashed"]:
                    print(f"   UNHASHED symbol (no baseline hash, treated as changed): {sym}")
                for sym in result["sym_unchanged"]:
                    print(f"   unchanged symbol: {sym}")
                for sha, subject in result["drift"][:5]:
                    print(f"   {sha} {subject}")
                if len(result["drift"]) > 5:
                    print(f"   … {len(result['drift']) - 5} more commits")
                if reco == RECO_SHADOW:
                    shadow_risks += 1
                    print("   ACTION: diff upstream's new body against the patch and rebase/retire —")
                    print(f"           python3 ow-patches/check_upstream.py --explain {patch.patch_id}")
            print()

        if result["upstream_has_equivalent"]:
            print(f"CANDIDATE FOR RETIREMENT (marker hit): {patch.patch_id}")
            for loc, content in result["hits"][:5]:
                print(f"   {loc}: {content}")
            if len(result["hits"]) > 5:
                print(f"   … {len(result['hits']) - 5} more hits suppressed")
            print(f"   retire_when: {patch.retire_when}")
            print()
        elif not result["drift"] and not result["gone"]:
            print(f"STILL LOCAL: {patch.patch_id}")
            print("   no upstream marker match, no target drift since baseline")
            print()

        drift_cell = (
            f"{len(result['drift'])} commits" if result["drift"] else ("GONE" if result["gone"] else "no")
        )
        if result["drift"] and result["hashed"]:
            drift_cell += f" / {len(result['sym_changed']) + len(result['sym_unhashed'])} sym"
        rows.append((patch.patch_id, patch.status, "yes" if result["upstream_has_equivalent"] else "no", drift_cell, reco))

    print("=" * 118)
    print(f"{'patch_id':<38} {'status':<20} {'marker?':<9} {'drift (commits/syms)':<22} {'recommendation':<26}")
    print("-" * 118)
    for patch_id, status, has_upstream, drift_cell, reco in rows:
        print(f"{patch_id:<38} {status:<20} {has_upstream:<9} {drift_cell:<22} {reco:<26}")
    print("=" * 118)
    print()
    print("marker?  = heuristic: upstream contains our upstream_equivalent_check string (weak signal).")
    print("drift    = upstream commits touching the patch's files since baseline / replaced symbols whose body hash changed.")
    print("Only a CHANGED symbol on a wholesale-replace patch is a shadow risk; 'methods unchanged' needs no audit.")
    print("Verify, then refresh: python3 ow-patches/check_upstream.py --update-baseline   (also re-records the hashes)")
    print("Registry rules:      python3 ow-patches/check_upstream.py --lint")
    if shadow_risks:
        print()
        print(f"⚠  {shadow_risks} wholesale-replace patch(es) have a changed upstream method — audit before trusting them.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
