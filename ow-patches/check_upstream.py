#!/usr/bin/env python3
"""Check whether upstream has caught up to any of our local fork patches.

Run from anywhere:
    python ow-patches/check_upstream.py

The script:
  1. git fetches the `upstream` remote (errors out if the remote is missing).
  2. For every patch in PATCHES.md whose status is `local_only` or
     `upstream_candidate`, greps upstream/main for the upstream_equivalent_check
     pattern.
  3. Prints CANDIDATE FOR RETIREMENT or STILL LOCAL for each patch.
  4. Prints a summary table at the end with a recommendation column.

Recommendations are advisory only — nothing is auto-retired. All retirements
are manual: see ../README.md#fork-patches for the procedure.
"""
from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PATCHES_MD = Path(__file__).resolve().parent / "PATCHES.md"
UPSTREAM_REMOTE = "upstream"
UPSTREAM_BRANCH = "main"
UPSTREAM_REF = f"{UPSTREAM_REMOTE}/{UPSTREAM_BRANCH}"


@dataclass
class Patch:
    patch_id: str
    status: str
    file: str
    symbol: str
    retire_when: str
    upstream_equivalent_check: str

    def is_active(self) -> bool:
        return self.status in {"local_only", "upstream_candidate"}


def _run(cmd: list[str], *, cwd: Path = REPO_ROOT, capture: bool = True) -> subprocess.CompletedProcess:
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
        print(f"WARN: git grep failed for pattern {pattern!r}:\n{res.stderr}", file=sys.stderr)
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


def evaluate_patch(patch: Patch) -> dict:
    """Run the equivalent_check and decide a recommendation for one patch."""
    pattern = patch.upstream_equivalent_check
    hits = grep_upstream(pattern)
    upstream_has_equivalent = bool(hits)

    # retire_when is a human-readable observable claim; we surface the same hits
    # to the operator so they can decide. We don't try to auto-evaluate the
    # natural-language sentence — the spec explicitly says no auto-retire.
    return {
        "patch_id": patch.patch_id,
        "status": patch.status,
        "upstream_has_equivalent": upstream_has_equivalent,
        "hits": hits,
        "retire_when": patch.retire_when,
    }


RECO_RETIRE = "review-for-retirement"
RECO_KEEP = "keep"
RECO_NOOP = "no-op (already retired)"


def recommendation(result: dict) -> str:
    if result["status"] == "retired":
        return RECO_NOOP
    if result["upstream_has_equivalent"]:
        return RECO_RETIRE
    return RECO_KEEP


def main() -> int:
    ensure_upstream_remote()
    fetch_upstream()
    patches = parse_patches_md(PATCHES_MD)
    if not patches:
        print("No patches parsed from PATCHES.md", file=sys.stderr)
        return 1

    print()
    print(f"Checking {len(patches)} patch(es) against {UPSTREAM_REF} …")
    print()

    rows: list[tuple[str, str, str, str, str]] = []
    for patch in patches:
        if not patch.is_active():
            rows.append((patch.patch_id, patch.status, "-", "-", RECO_NOOP))
            continue

        result = evaluate_patch(patch)
        if result["upstream_has_equivalent"]:
            print(f"CANDIDATE FOR RETIREMENT: {patch.patch_id}")
            for loc, content in result["hits"][:5]:
                print(f"   {loc}: {content}")
            if len(result["hits"]) > 5:
                print(f"   … {len(result['hits']) - 5} more hits suppressed")
            print(f"   retire_when: {patch.retire_when}")
            print(f"   verify the upstream impl matches: same provider, same field names, same units, nullability ≥ ours")
            print()
        else:
            print(f"STILL LOCAL: {patch.patch_id}")
            print(f"   no upstream match for: {patch.upstream_equivalent_check}")
            print()

        rows.append(
            (
                patch.patch_id,
                patch.status,
                "yes" if result["upstream_has_equivalent"] else "no",
                "manual",
                recommendation(result),
            )
        )

    # Summary table
    print("=" * 100)
    print(f"{'patch_id':<35} {'status':<22} {'upstream?':<12} {'retire_when_met?':<18} {'recommendation':<20}")
    print("-" * 100)
    for patch_id, status, has_upstream, retire_met, reco in rows:
        print(f"{patch_id:<35} {status:<22} {has_upstream:<12} {retire_met:<18} {reco:<20}")
    print("=" * 100)
    print()
    print("Note: retire_when conditions are observable claims (not pattern matches) — verify manually.")
    print("Retirement procedure: see README.md#fork-patches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
