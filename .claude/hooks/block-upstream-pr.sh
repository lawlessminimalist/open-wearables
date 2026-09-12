#!/usr/bin/env bash
# PreToolUse(Bash) hook: make it impossible to open, edit or push a PR against
# the upstream repository from this fork checkout.
#
# Why: on 2026-09-13 `gh pr create` (no --repo) defaulted to the fork's PARENT and
# opened a fork-internal reconcile PR on the-momentum/open-wearables (#1611).
#
# Only the `gh ...` and `git push ...` SEGMENTS of a command are inspected (a
# segment = one simple command between newlines / ; / && / || / |). Mentioning the
# upstream slug elsewhere — a commit message, a heredoc writing docs, a grep — is
# fine. Rules, applied per inspected segment (deny = the tool call never runs):
#   1. A `gh` segment that names the upstream slug/owner, or a `git push` segment
#      whose remote is `upstream` or the upstream URL.
#   2. A `gh pr create|edit` segment without an explicit --repo/-R <fork slug>.
#      gh's default base for a fork is the parent, and `gh repo set-default` is
#      per-clone, so the flag is required regardless of local config.
# Known false positive: a `gh pr create --repo <fork> --body "...<upstream slug>..."`
# is denied by rule 1; put such text in --body-file instead.
set -uo pipefail

input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty')
[ -n "$cmd" ] || exit 0

# Cheap pre-filter so unrelated commands cost nothing.
case "$cmd" in
  *gh*|*git*) ;;
  *) exit 0 ;;
esac

slug_of() { # remote name -> owner/repo, or empty
  { git remote get-url "$1" 2>/dev/null || true; } \
    | sed -E 's#^(https?://[^/]+/|git@[^:]+:)##; s#\.git$##; s#/$##'
}
FORK_SLUG=$(slug_of origin);       FORK_SLUG=${FORK_SLUG:-lawlessminimalist/open-wearables}
UPSTREAM_SLUG=$(slug_of upstream); UPSTREAM_SLUG=${UPSTREAM_SLUG:-the-momentum/open-wearables}
UPSTREAM_OWNER=${UPSTREAM_SLUG%%/*}

deny() {
  jq -n --arg r "$1" \
    '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

# Split into simple-command segments and keep only those that invoke gh or git.
# Leading `cd x &&`, env assignments (`FOO=bar gh ...`) and `sudo`/`env` are tolerated.
segments=$(printf '%s\n' "$cmd" \
  | sed -E 's/(&&|\|\||;|\|)/\n/g' \
  | sed -E 's/^[[:space:]]+//; s/^([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]*[[:space:]]+)*//; s/^(env|sudo)[[:space:]]+//' \
  | grep -E '^(gh|git)([[:space:]]|$)' || true)
[ -n "$segments" ] || exit 0

while IFS= read -r seg; do
  [ -n "$seg" ] || continue

  if printf '%s' "$seg" | grep -Eq '^gh([[:space:]]|$)'; then
    # Rule 1 (gh): upstream slug, owner path, or API path.
    if printf '%s' "$seg" | grep -Eq "$UPSTREAM_SLUG|github\.com/$UPSTREAM_OWNER/|repos/$UPSTREAM_OWNER/"; then
      deny "Blocked: this gh command targets the UPSTREAM repository ($UPSTREAM_SLUG). Fork-internal work must never reach upstream's tracker. Use --repo $FORK_SLUG; if the slug is only in PR text, move it to --body-file."
    fi
    # Rule 2: pr create/edit must name the fork explicitly.
    if printf '%s' "$seg" | grep -Eq '^gh[[:space:]]+pr[[:space:]]+(create|edit)([[:space:]]|$)'; then
      if ! printf '%s' "$seg" | grep -Eq -- "(--repo|-R)[[:space:]=]+(https://github\.com/)?$FORK_SLUG([[:space:]]|$)"; then
        deny "Blocked: 'gh pr create/edit' without an explicit fork target. gh defaults a fork's PRs to the parent repo ($UPSTREAM_SLUG). Re-run with: --repo $FORK_SLUG"
      fi
    fi
  fi

  if printf '%s' "$seg" | grep -Eq '^git[[:space:]]+push([[:space:]]|$)'; then
    # Rule 1 (git push): the upstream remote by name or URL.
    if printf '%s' "$seg" | grep -Eq "[[:space:]]upstream([[:space:]]|$)|$UPSTREAM_SLUG|github\.com/$UPSTREAM_OWNER/"; then
      deny "Blocked: this pushes to $UPSTREAM_SLUG. Push to origin ($FORK_SLUG) instead."
    fi
  fi
done <<< "$segments"

exit 0
