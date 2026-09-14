#!/usr/bin/env python3
"""Table-driven tests for block_upstream_pr.py, driven through the bash wrapper.

Run:  python3 .claude/hooks/test_block_upstream_pr.py        (or: make hook-test)
Exit code is non-zero on any mismatch. CI runs this in .github/workflows/ow-patches.yml.

Cases live in cases/*.txt so a new false positive or bypass is a one-line addition:
  single.txt     one case per line:            ALLOW|<command>   or   DENY|<command>
  multiline.txt  blocks separated by "====":   first line ALLOW/DENY, rest is the command
The hook is a hand-rolled shell parser (quote-aware splitter, heredoc stripper,
bash -c recursion). It guards the one incident that actually happened
(upstream PR #1611); it must not be edited without these passing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
HOOK = HERE / "block-upstream-pr.sh"
CASES = HERE / "cases"


def run(command: str) -> tuple[str, str]:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    res = subprocess.run([str(HOOK)], input=payload, capture_output=True, text=True, timeout=20)
    if res.returncode != 0:
        return "ERROR", f"exit {res.returncode}: {res.stderr.strip()[:200]}"
    out = res.stdout.strip()
    if not out:
        return "ALLOW", ""
    try:
        reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
    except Exception:  # noqa: BLE001
        return "ERROR", f"unparseable hook output: {out[:200]}"
    return "DENY", reason


def load_cases() -> list[tuple[str, str, str]]:
    cases: list[tuple[str, str, str]] = []
    single = CASES / "single.txt"
    for n, line in enumerate(single.read_text().splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        want, _, cmd = line.partition("|")
        cases.append((f"single.txt:{n}", want.strip(), cmd))
    multi = CASES / "multiline.txt"
    for n, block in enumerate(multi.read_text().split("====\n"), start=1):
        block = block.strip("\n")
        if not block:
            continue
        want, _, cmd = block.partition("\n")
        cases.append((f"multiline.txt#{n}", want.strip(), cmd))
    return cases


def main() -> int:
    cases = load_cases()
    fails = 0
    for label, want, cmd in cases:
        got, detail = run(cmd)
        if got != want:
            fails += 1
            first = cmd.splitlines()[0] if cmd else ""
            print(f"MISMATCH {label}: want {want}, got {got}  ::  {first[:90]}")
            if detail:
                print(f"           {detail[:160]}")
    # fail-closed contract: no python3 on PATH must deny, not allow
    res = subprocess.run(
        ["/bin/bash", str(HOOK)], input='{"tool_input":{"command":"gh pr create"}}',
        capture_output=True, text=True, env={"PATH": "/nonexistent"}, timeout=20,
    )
    if '"permissionDecision":"deny"' not in res.stdout.replace(" ", ""):
        fails += 1
        print("MISMATCH fail-closed: with python3 unavailable the wrapper must emit a deny")
    print(f"{len(cases) + 1} cases, {fails} mismatches")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
