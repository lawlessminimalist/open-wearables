#!/usr/bin/env python3
"""Check whether upstream has caught up to — or silently diverged from — any of
our local fork patches.

Run from anywhere:
    python ow-patches/check_upstream.py
    python ow-patches/check_upstream.py --update-baseline   # after reconciling

The script runs TWO independent checks per patch:

  1. EQUIVALENCE (heuristic) — greps upstream/main for the patch's
     `upstream_equivalent_check` marker. A hit *suggests* upstream may have
     shipped its own version of the fix. This is a weak signal: the marker is a
     string unique to OUR implementation, so it only fires when upstream happens
     to use the same token. It CANNOT see an upstream change that achieves the
     same thing differently.

  2. DRIFT (deterministic) — for every file the patch depends on (parsed from
     the `file:` field), asks `git log <baseline>..upstream/main -- <file>`:
     has upstream touched it since we last reconciled? This is the check that
     catches the dangerous case the equivalence grep is blind to:

         A monkey-patch that WHOLESALE-REPLACES an upstream method does NOT
         produce a git merge conflict (we never edit the upstream source file),
         so a `git merge` is silent. If upstream rewrites that method, our patch
         keeps shadowing it with a stale copy — silently dropping upstream's
         improvements (this is exactly how avg_hrv_rmssd_ms went null after
         upstream rewrote get_sleep_summaries).

     Drift is escalated by `replacement_kind`: a wholesale-replace patch whose
     target drifted is a SHADOW RISK and must be re-verified/rebased; a decorate
     patch that merely wraps upstream is lower-risk. The drift check deliberately
     focuses on monkey-patch patches — source-edit patches (e.g. the frontend
     ones) surface upstream drift as ordinary git conflicts at merge time.

The baseline SHA lives in `ow-patches/.upstream-baseline`. Refresh it with
`--update-baseline` once every flagged patch has been re-verified after a merge.

Recommendations are advisory only — nothing is auto-retired. All retirements and
rebases are manual: see ../README.md#fork-patches for the procedure.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PATCHES_MD = Path(__file__).resolve().parent / "PATCHES.md"
BASELINE_FILE = Path(__file__).resolve().parent / ".upstream-baseline"
UPSTREAM_REMOTE = "upstream"
UPSTREAM_BRANCH = "main"
UPSTREAM_REF = f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}"

# Extract repo-relative source paths from a free-text `file:` field, ignoring
# any prose / parentheticals (e.g. "foo.py (composed via bar)").
_PATH_RE = re.compile(r"(?:backend|frontend|mcp)/[\w./-]+\.\w+")


@dataclass
class Patch:
    patch_id: str
    status: str
    file: str
    symbol: str
    retire_when: str
    upstream_equivalent_check: str
    replacement_kind: str

    def is_active(self) -> bool:
        return self.status in {"local_only", "upstream_candidate"}

    def is_wholesale_replace(self) -> bool:
        # Default-on: if a patch hasn't declared its kind, treat it as the
        # dangerous (shadow-prone) kind so it gets the loud warning. Being wrong
        # in the safe direction costs an extra manual check; being wrong the
        # other way is how avg_hrv_rmssd_ms went null.
        return self.replacement_kind not in {"decorate", "structural", "standalone"}

    def target_paths(self) -> list[str]:
        if not self.file:
            return []
        return _PATH_RE.findall(self.file)


def _run(
    cmd: list[str], *, cwd: Path = REPO_ROOT, capture: bool = True
) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        check=False,
        text=True,
        capture_output=capture,
    )


def ensure_upstream_remote() -> None:
    res = _run(["git", "remote"])
    remotes = set(res.stdout.split())
    if UPSTREAM_REMOTE in remotes:
        return
    print(f"ERROR: git remote '{UPSTREAM_REMOTE}' is not configured.", file=sys.stderr)
    print("", file=sys.stderr)
    print("Add it (replace the URL with your actual upstream):", file=sys.stderr)
    print("", file=sys.stderr)
    print(
        f"  git remote add {UPSTREAM_REMOTE} https://github.com/the-momentum/open-wearables.git",
        file=sys.stderr,
    )
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
    text = path.read_text()
    patches: list[Patch] = []
    current: dict[str, str] = {}

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("---"):
            if current.get("patch_id"):
                patches.append(_make_patch(current))
            current = {}
            continue
        m = _FIELD_RE.match(line)
        if m:
            key, value = m.group(1).strip(), m.group(2).strip()
            current[key] = value
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
    )


def grep_upstream(pattern: str) -> list[tuple[str, str]]:
    """Return list of (file:line, content) hits for pattern in upstream/main tree.

    A pattern may be path-qualified using `<path-substring>::<text>` syntax to
    restrict matches to files whose path contains <path-substring>. This is
    helpful when a pattern (e.g. `source=self.provider_name`) matches lots of
    files but we only care about a specific one.
    """
    path_filter: str | None = None
    if "::" in pattern:
        path_filter, pattern = pattern.split("::", 1)
    cmd = ["git", "grep", "-n", "-F", pattern, UPSTREAM_REF]
    if path_filter:
        cmd.extend(["--", f"*{path_filter}*"])
    res = _run(cmd)
    if res.returncode not in (0, 1):  # 1 = no matches
        print(
            f"WARN: git grep failed for pattern {pattern!r}:\n{res.stderr}",
            file=sys.stderr,
        )
        return []
    hits: list[tuple[str, str]] = []
    for raw in res.stdout.splitlines():
        # format: <ref>:<file>:<line>:<content>
        parts = raw.split(":", 3)
        if len(parts) < 4:
            continue
        _ref, fp, ln, content = parts
        hits.append((f"{fp}:{ln}", content.strip()))
    return hits


def read_baseline() -> str | None:
    """Read the reconciled upstream SHA from .upstream-baseline (ignores comments)."""
    if not BASELINE_FILE.exists():
        return None
    for raw in BASELINE_FILE.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            return line.split()[0]
    return None


def write_baseline(sha: str) -> None:
    """Rewrite .upstream-baseline with a new reconciled SHA, preserving the header."""
    header = (
        "# The upstream/main commit we last fully reconciled the patches against.\n"
        "# check_upstream.py compares this against the current upstream/main to detect\n"
        "# whether upstream has TOUCHED any file a patch depends on (drift). After you\n"
        "# merge upstream and re-verify every flagged patch, refresh this with:\n"
        "#\n"
        "#     python ow-patches/check_upstream.py --update-baseline\n"
        "#\n"
    )
    BASELINE_FILE.write_text(f"{header}{sha}\n")


def commits_touching(baseline: str, paths: list[str]) -> list[tuple[str, str]]:
    """Return (sha, subject) for upstream commits that touched any path since baseline."""
    if not baseline or not paths:
        return []
    cmd = [
        "git",
        "log",
        "--oneline",
        "--no-decorate",
        f"{baseline}..{UPSTREAM_REF}",
        "--",
        *paths,
    ]
    res = _run(cmd)
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
    """Return paths upstream REMOVED since baseline (renamed/deleted), not fork-only files.

    A monkey-patch whose target file was renamed/removed upstream is broken (or
    about to be). But many of our targets are fork-only files (e.g. the
    garmin_connect/ provider, our migrations) that never existed upstream —
    those are expected, not broken. So we only flag a path as "gone" when it
    existed at the baseline upstream ref AND no longer exists at current
    upstream. Without a baseline we cannot tell the two apart, so we flag
    nothing (drift-by-commits still covers the common case).
    """
    if not baseline:
        return []
    gone: list[str] = []
    for p in paths:
        if _exists_at(baseline, p) and not _exists_at(UPSTREAM_REF, p):
            gone.append(p)
    return gone


def evaluate_patch(patch: Patch, baseline: str | None) -> dict:
    """Run the equivalence grep AND the deterministic drift check for one patch."""
    pattern = patch.upstream_equivalent_check
    hits = grep_upstream(pattern)
    upstream_has_equivalent = bool(hits)

    paths = patch.target_paths()
    drift = commits_touching(baseline, paths) if baseline else []
    gone = missing_upstream_paths(baseline, paths) if paths else []

    # retire_when is a human-readable observable claim; we surface the same hits
    # to the operator so they can decide. We don't try to auto-evaluate the
    # natural-language sentence — the spec explicitly says no auto-retire.
    return {
        "patch_id": patch.patch_id,
        "status": patch.status,
        "upstream_has_equivalent": upstream_has_equivalent,
        "hits": hits,
        "retire_when": patch.retire_when,
        "paths": paths,
        "drift": drift,
        "gone": gone,
        "wholesale": patch.is_wholesale_replace(),
    }


RECO_RETIRE = "review-for-retirement"
RECO_KEEP = "keep"
RECO_NOOP = "no-op (already retired)"
RECO_SHADOW = "RE-VERIFY (shadow risk)"
RECO_DRIFT = "re-verify (target moved)"
RECO_BROKEN = "BROKEN (target gone)"


def recommendation(result: dict) -> str:
    if result["status"] == "retired":
        return RECO_NOOP
    if result["gone"]:
        return RECO_BROKEN
    # Deterministic drift dominates the heuristic equivalence grep. A drifted
    # wholesale-replace patch is shadowing upstream and must be re-verified.
    if result["drift"]:
        return RECO_SHADOW if result["wholesale"] else RECO_DRIFT
    if result["upstream_has_equivalent"]:
        return RECO_RETIRE
    return RECO_KEEP


def main() -> int:
    ensure_upstream_remote()
    fetch_upstream()

    if "--update-baseline" in sys.argv[1:]:
        sha = _run(["git", "rev-parse", UPSTREAM_REF]).stdout.strip()
        write_baseline(sha)
        print(f"Baseline updated to {UPSTREAM_REF} @ {sha}")
        print("Drift will now be measured from this commit forward.")
        return 0

    patches = parse_patches_md(PATCHES_MD)
    if not patches:
        print("No patches parsed from PATCHES.md", file=sys.stderr)
        return 1

    baseline = read_baseline()
    print()
    print(f"Checking {len(patches)} patch(es) against {UPSTREAM_REF} …")
    if baseline:
        print(f"Drift baseline: {baseline}")
    else:
        print("WARNING: no .upstream-baseline found — drift check skipped.")
        print(
            "         Set one with: python ow-patches/check_upstream.py --update-baseline"
        )
    print()

    rows: list[tuple[str, str, str, str, str]] = []
    shadow_risks = 0
    for patch in patches:
        if not patch.is_active():
            rows.append((patch.patch_id, patch.status, "-", "-", RECO_NOOP))
            continue

        result = evaluate_patch(patch, baseline)

        # DRIFT — the deterministic check. Loudest signal, printed first.
        if result["gone"]:
            print(f"BROKEN — TARGET GONE UPSTREAM: {patch.patch_id}")
            for p in result["gone"]:
                print(f"   missing in {UPSTREAM_REF}: {p}")
            print(
                "   the monkey-patch targets a file upstream renamed/removed — it will mis-apply or no-op."
            )
            print()
        elif result["drift"]:
            label = (
                "SHADOW RISK — UPSTREAM CHANGED A WHOLESALE-REPLACED TARGET"
                if result["wholesale"]
                else "UPSTREAM TOUCHED TARGET"
            )
            print(f"{label}: {patch.patch_id}  ({patch.replacement_kind})")
            for p in result["paths"]:
                print(f"   target: {p}")
            for sha, subject in result["drift"][:5]:
                print(f"   {sha} {subject}")
            if len(result["drift"]) > 5:
                print(f"   … {len(result['drift']) - 5} more commits")
            if result["wholesale"]:
                shadow_risks += 1
                print(
                    "   ACTION: this patch reimplements the method above. Diff upstream's new body against"
                )
                print(
                    "           the patch and rebase/retire — a silent merge will NOT have flagged this."
                )
            print()

        # EQUIVALENCE — the heuristic marker grep.
        if result["upstream_has_equivalent"]:
            print(f"CANDIDATE FOR RETIREMENT (marker hit): {patch.patch_id}")
            for loc, content in result["hits"][:5]:
                print(f"   {loc}: {content}")
            if len(result["hits"]) > 5:
                print(f"   … {len(result['hits']) - 5} more hits suppressed")
            print(f"   retire_when: {patch.retire_when}")
            print(
                "   verify the upstream impl matches: same provider, same field names, same units, nullability ≥ ours"
            )
            print()
        elif not result["drift"] and not result["gone"]:
            print(f"STILL LOCAL: {patch.patch_id}")
            print("   no upstream marker match, no target drift since baseline")
            print()

        drift_cell = (
            f"{len(result['drift'])} commits"
            if result["drift"]
            else ("GONE" if result["gone"] else "no")
        )
        rows.append(
            (
                patch.patch_id,
                patch.status,
                "yes" if result["upstream_has_equivalent"] else "no",
                drift_cell,
                recommendation(result),
            )
        )

    # Summary table
    print("=" * 108)
    print(
        f"{'patch_id':<35} {'status':<20} {'marker?':<9} {'upstream_drift':<16} {'recommendation':<24}"
    )
    print("-" * 108)
    for patch_id, status, has_upstream, drift_cell, reco in rows:
        print(
            f"{patch_id:<35} {status:<20} {has_upstream:<9} {drift_cell:<16} {reco:<24}"
        )
    print("=" * 108)
    print()
    print(
        "marker?        = heuristic: upstream contains our upstream_equivalent_check string (weak signal)."
    )
    print(
        "upstream_drift = deterministic: upstream commits touching this patch's target files since baseline."
    )
    print(
        "Drift on a wholesale-replace patch means it is SHADOWING upstream — re-verify before trusting it."
    )
    print(
        "Verify manually, then refresh the baseline: python ow-patches/check_upstream.py --update-baseline"
    )
    print("Retirement/rebase procedure: see README.md#fork-patches.")
    if shadow_risks:
        print()
        print(
            f"⚠  {shadow_risks} wholesale-replace patch(es) drifted — these are the silent-merge blind spots."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
