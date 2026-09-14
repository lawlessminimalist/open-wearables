#!/usr/bin/env python3
"""PreToolUse(Bash) hook: nothing from this fork checkout may reach upstream's tracker.

Why: on 2026-09-13 `gh pr create` (no --repo) defaulted to the fork's PARENT and opened a
fork-internal reconcile PR on the-momentum/open-wearables (#1611).

Defence in depth — this hook is the LAST layer, not the only one:
  * .claude/settings.json sets GH_REPO=<fork> for every command Claude runs, so gh's
    default target is the fork even when --repo is omitted.
  * `git remote set-url --push upstream no_push` is applied per clone (FORK.md section 5).

How it reads a command: backslash-newline continuations are joined, heredoc bodies are
removed, then the text is split into simple commands on UNQUOTED newline / ; / && / || / |
(quotes and backslashes are honoured, so a commit message containing ';' is one segment).
Each segment is unwrapped — `cd x &&` is already split off, leading env assignments,
env/sudo/time/nohup/nice/command/exec and their flags are dropped, and `bash -c "..."` /
`eval "..."` are analysed recursively — and only segments whose command is gh or git are
examined. Mentioning the upstream slug in a commit message or a documentation heredoc is
fine. Rules (deny = the tool call never runs):
  1. A gh segment that names the upstream slug or owner path anywhere (including an env
     prefix such as GH_REPO=<upstream>). Known false positive: a PR body citing upstream
     by full slug — use --body-file.
  2. A `git push` (any option form, `git -C dir push`) whose remote is `upstream`, a
     --repo=upstream, or the upstream URL.
  3. A mutating gh command — pr/issue/release write verbs, `gh repo` destructive verbs, or
     `gh api` with a mutating method or field — without an explicit fork target
     (--repo/-R <fork slug>, a positional <fork slug>, or repos/<fork slug> in the api path).
Fails CLOSED: any parse error denies rather than allows.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys

FORK_FALLBACK = "lawlessminimalist/open-wearables"
UPSTREAM_FALLBACK = "the-momentum/open-wearables"

WRITE_VERBS = {
    "create", "edit", "merge", "close", "reopen", "comment", "review", "ready", "lock", "unlock",
    "pin", "unpin", "transfer", "delete", "develop", "upload", "delete-asset", "update-branch",
}
REPO_WRITE_VERBS = {"delete", "edit", "rename", "archive", "unarchive", "sync"}
WRAPPERS = {"env", "sudo", "time", "nohup", "nice", "command", "exec", "builtin"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def slug_of(remote: str) -> str:
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", remote], capture_output=True, text=True, timeout=5
        ).stdout.strip()
    except Exception:
        return ""
    url = re.sub(r"^(https?://[^/]+/|git@[^:]+:)", "", url)
    return url.removesuffix(".git").rstrip("/")


def join_continuations(text: str) -> str:
    return re.sub(r"\\\n", " ", text)


HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def strip_heredocs(text: str) -> str:
    out: list[str] = []
    terminator: str | None = None
    for line in text.split("\n"):
        if terminator is not None:
            if line.strip() == terminator:
                terminator = None
            continue
        out.append(line)
        m = HEREDOC_RE.search(line)
        if m:
            terminator = m.group(2)
    return "\n".join(out)


def split_segments(text: str) -> list[str]:
    """Split on unquoted newline ; && || | ( ). Honour '...', "...", and backslash escapes."""
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == "\\" and quote == '"' and i + 1 < n:
                buf.append(text[i + 1])
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            buf.append(ch)
            buf.append(text[i + 1])
            i += 2
            continue
        if text.startswith("&&", i) or text.startswith("||", i):
            segments.append("".join(buf))
            buf = []
            i += 2
            continue
        if ch in ";|\n()":
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return [s.strip() for s in segments if s.strip()]


def tokens_of(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return segment.split()


def unwrap(tokens: list[str]) -> list[str]:
    """Drop env assignments and wrapper commands so tokens[0] is the real command."""
    changed = True
    while changed and tokens:
        changed = False
        if ENV_ASSIGN.match(tokens[0]):
            tokens = tokens[1:]
            changed = True
            continue
        if tokens[0] in WRAPPERS:
            tokens = tokens[1:]
            while tokens and tokens[0].startswith("-"):
                tokens = tokens[1:]
            changed = True
    return tokens


class Checker:
    def __init__(self) -> None:
        self.fork = slug_of("origin") or FORK_FALLBACK
        upstream = slug_of("upstream")
        self.upstream = upstream if upstream and upstream != "no_push" else UPSTREAM_FALLBACK
        self.upstream_owner = self.upstream.split("/")[0]
        self.upstream_re = re.compile(
            re.escape(self.upstream) + r"|github\.com/" + re.escape(self.upstream_owner) + r"/|repos/"
            + re.escape(self.upstream_owner) + r"/"
        )

    # -- helpers -------------------------------------------------------------
    def is_fork(self, value: str) -> bool:
        v = value.strip("\"'").removeprefix("https://github.com/").removesuffix(".git").rstrip("/")
        return v == self.fork

    def repo_flag_value(self, tokens: list[str]) -> str | None:
        for i, t in enumerate(tokens):
            if t in ("--repo", "-R"):
                return tokens[i + 1] if i + 1 < len(tokens) else ""
            if t.startswith("--repo="):
                return t[len("--repo="):]
            if t.startswith("-R") and len(t) > 2:
                return t[2:]
        return None

    # -- rules ---------------------------------------------------------------
    def check_gh(self, raw: str, tokens: list[str]) -> None:
        if self.upstream_re.search(raw):
            deny(
                f"Blocked: this gh command targets the UPSTREAM repository ({self.upstream}). "
                f"Fork-internal work must never reach upstream's tracker. Use --repo {self.fork}; "
                f"if the slug is only PR text, move it to --body-file."
            )
        args = tokens[1:]
        if not args:
            return
        sub = args[0]
        verb = args[1] if len(args) > 1 else ""
        mutating = False
        positional_ok = False
        if sub in ("pr", "issue", "release") and verb in WRITE_VERBS:
            mutating = True
        elif sub == "repo" and verb in REPO_WRITE_VERBS:
            mutating = True
            positional_ok = any(self.is_fork(t) for t in args[2:] if not t.startswith("-"))
        elif sub == "api":
            rest = args[1:]
            methods = {"POST", "PUT", "PATCH", "DELETE"}
            for i, t in enumerate(rest):
                if t in ("-X", "--method") and i + 1 < len(rest) and rest[i + 1].upper() in methods:
                    mutating = True
                if t.startswith("--method=") and t.split("=", 1)[1].upper() in methods:
                    mutating = True
                if t.startswith("-X") and len(t) > 2 and t[2:].upper() in methods:
                    mutating = True
                if t in ("-f", "-F", "--field", "--raw-field", "--input") or t.startswith(("--field=", "--raw-field=", "--input=")):
                    mutating = True
            positional_ok = any(
                re.search(r"(^|/)repos/" + re.escape(self.fork) + r"(/|$)", t) for t in rest
            )
        if not mutating:
            return
        repo = self.repo_flag_value(tokens)
        if (repo is not None and self.is_fork(repo)) or positional_ok:
            return
        deny(
            f"Blocked: mutating gh command without an explicit fork target. gh defaults a fork's "
            f"PRs/issues to the parent repo ({self.upstream}). Re-run with: --repo {self.fork}"
        )

    def check_git(self, raw: str, tokens: list[str]) -> None:
        # find the subcommand: skip global options like -C <dir>, -c k=v, --git-dir=...
        i = 1
        while i < len(tokens):
            t = tokens[i]
            if t in ("-C", "-c", "--git-dir", "--work-tree", "--namespace"):
                i += 2
                continue
            if t.startswith("-"):
                i += 1
                continue
            break
        if i >= len(tokens) or tokens[i] != "push":
            return
        rest = tokens[i + 1:]
        for t in rest:
            v = t.strip("\"'")
            if v == "upstream" or v == "--repo=upstream" or self.upstream_re.search(v) or v.rstrip("/").removesuffix(".git").endswith("/" + self.upstream):
                deny(f"Blocked: this pushes to {self.upstream}. Push to origin ({self.fork}) instead.")
            if t == "--repo" and rest.index(t) + 1 < len(rest) and rest[rest.index(t) + 1].strip("\"'") == "upstream":
                deny(f"Blocked: this pushes to {self.upstream}. Push to origin ({self.fork}) instead.")

    # -- driver --------------------------------------------------------------
    def check_command(self, text: str, depth: int = 0) -> None:
        if depth > 4:
            return
        text = strip_heredocs(join_continuations(text))
        for raw in split_segments(text):
            tokens = unwrap(tokens_of(raw))
            if not tokens:
                continue
            head = tokens[0]
            if head in SHELLS or head == "eval":
                # bash -c "<inner>" / eval "<inner>": analyse the inner string.
                inner = None
                if head == "eval":
                    inner = " ".join(tokens[1:])
                else:
                    for j, t in enumerate(tokens[1:], start=1):
                        if t == "-c" and j + 1 < len(tokens):
                            inner = tokens[j + 1]
                            break
                        if t.startswith("-") and "c" in t and j + 1 < len(tokens) and not t.startswith("--"):
                            inner = tokens[j + 1]
                            break
                if inner:
                    self.check_command(inner, depth + 1)
                continue
            if head == "gh":
                self.check_gh(raw, tokens)
            elif head == "git":
                self.check_git(raw, tokens)


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        cmd = (payload.get("tool_input") or {}).get("command") or ""
    except Exception as exc:  # noqa: BLE001
        deny(f"block_upstream_pr.py could not read the hook input ({exc}); failing closed.")
        return
    if not cmd or ("gh" not in cmd and "git" not in cmd):
        return
    try:
        Checker().check_command(cmd)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        deny(f"block_upstream_pr.py failed while inspecting the command ({exc!r}); failing closed.")


if __name__ == "__main__":
    main()
