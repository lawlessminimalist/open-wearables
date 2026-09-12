#!/usr/bin/env bash
# Thin wrapper for block_upstream_pr.py (see that file for what and why).
# Exists only so the hook FAILS CLOSED when python3 is unavailable: a missing
# interpreter would otherwise be a non-blocking error, i.e. an allow.
set -u
here=$(cd "$(dirname "$0")" && pwd)
if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"block-upstream-pr: python3 is not installed, so the command cannot be inspected; the hook fails closed by design."}}'
  exit 0
fi
exec python3 "$here/block_upstream_pr.py"
