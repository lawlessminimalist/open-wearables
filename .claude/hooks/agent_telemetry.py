#!/usr/bin/env python3
"""Stop / SessionEnd hook: push this session's agent usage to the homelab OpenTelemetry collector.

Repo-scoped by design. This does NOT set OTEL_EXPORTER_OTLP_* or
CLAUDE_CODE_ENABLE_TELEMETRY: those are the harness's own export and live (if at
all) in user-level settings; project-scope values would override and hijack them
(dhlaw-explorations, tooling-and-quirks.md, observed 2026-09-03). This is a
second, independent path that reads the session transcript and posts OTLP/JSON to
the cluster's OpenTelemetry collector (addons/opentelemetry in the homelab repo),
which forwards to Mimir. Nothing here writes to Mimir directly.

Semantics (ported from dhlaw-explorations' `explore efficiency`):
  * Every metric is a CUMULATIVE monotonic sum recomputed from the WHOLE transcript
    on each push, with startTimeUnixNano = the session's first timestamp. A retry,
    a resumed session or a duplicate Stop therefore never double counts: the
    latest sample simply restates the total.
  * Usage is counted once per requestId (a streamed response is written as
    several assistant lines sharing one requestId).
  * Failed pushes are spooled (latest snapshot per session) under
    ~/.claude/ow-agent-telemetry/spool/ and drained on the next successful push.
  * Never fails the hook: any error is one stderr line and exit 0.

Metrics (labels: session_id, repo, plus per-metric ones):
  ow_agent_tokens_total{type=input|output|cache_read|cache_write_5m|cache_write_1h, model}
  ow_agent_requests_total{model}
  ow_agent_tool_calls_total{tool}
  ow_agent_tool_result_bytes_total{tool}
  ow_agent_tool_errors_total{tool}
  ow_agent_prompts_total
  ow_agent_upstream_guard_denials_total          (written by block_upstream_pr.py)
No cost metric: list prices for the Claude 5 family are not pinned anywhere in
this repo and a guessed price is worse than none (explorations rule).

Configuration (env, set in .claude/settings.json):
  OW_AGENT_METRICS_URL          default http://otel.lab.homelab-dhlaw.uk/v1/metrics (the collector's OTLP/HTTP receiver)
  OW_AGENT_METRICS_FALLBACK_IP  connect here with a Host header if the name does not
                                resolve (the tailnet name needs an /etc/hosts entry)
  OW_AGENT_METRICS_ORG          optional X-Scope-OrgID, only if the endpoint is ever a tenant-aware gateway
  OW_AGENT_METRICS_DISABLE=1    turn the push off without touching settings

Dashboard gotchas (from explorations): series are written only at turn ends, so
panels must use last_over_time(...[range]); Mimir cannot delete series, so never
push a truncated or made-up session id.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

DEFAULT_URL = "http://otel.lab.homelab-dhlaw.uk/v1/metrics"
SPOOL_DIR = Path.home() / ".claude" / "ow-agent-telemetry" / "spool"
DENIALS_DIR = Path.home() / ".claude" / "ow-agent-telemetry" / "denials"
REPO = "open-wearables"
SERVICE = "ow-agent-telemetry"


def log(msg: str) -> None:
    print(f"agent_telemetry: {msg}", file=sys.stderr)


# ---------------------------------------------------------------------------
# transcript -> counters
# ---------------------------------------------------------------------------


def parse_ts(ts: str | None) -> int | None:
    """ISO-8601 with Z -> unix ns."""
    if not ts:
        return None
    try:
        from datetime import datetime, timezone

        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1e9)
    except Exception:  # noqa: BLE001
        return None


def measure(transcript: Path) -> dict:
    tokens: dict[tuple[str, str], int] = defaultdict(int)  # (model, type) -> n
    requests: dict[str, int] = defaultdict(int)  # model -> n
    tool_calls: dict[str, int] = defaultdict(int)
    tool_bytes: dict[str, int] = defaultdict(int)
    tool_errors: dict[str, int] = defaultdict(int)
    tool_name_by_id: dict[str, str] = {}
    prompts = 0
    seen_requests: set[str] = set()
    first_ts: int | None = None
    last_ts: int | None = None

    with transcript.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if rec.get("isSidechain"):
                continue  # subagent transcripts are their own sessions
            ts = parse_ts(rec.get("timestamp"))
            if ts:
                first_ts = ts if first_ts is None or ts < first_ts else first_ts
                last_ts = ts if last_ts is None or ts > last_ts else last_ts
            rtype = rec.get("type")
            msg = rec.get("message") or {}
            content = msg.get("content")
            if rtype == "assistant":
                rid = rec.get("requestId")
                model = msg.get("model") or "unknown"
                usage = msg.get("usage") or {}
                if rid and rid not in seen_requests and usage:
                    seen_requests.add(rid)
                    requests[model] += 1
                    tokens[(model, "input")] += int(usage.get("input_tokens") or 0)
                    tokens[(model, "output")] += int(usage.get("output_tokens") or 0)
                    tokens[(model, "cache_read")] += int(usage.get("cache_read_input_tokens") or 0)
                    cc = usage.get("cache_creation") or {}
                    w5 = int(cc.get("ephemeral_5m_input_tokens") or 0)
                    w1 = int(cc.get("ephemeral_1h_input_tokens") or 0)
                    if not (w5 or w1):
                        w5 = int(usage.get("cache_creation_input_tokens") or 0)
                    tokens[(model, "cache_write_5m")] += w5
                    tokens[(model, "cache_write_1h")] += w1
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "tool_use":
                            name = item.get("name") or "unknown"
                            tool_calls[name] += 1
                            if item.get("id"):
                                tool_name_by_id[item["id"]] = name
            elif rtype == "user":
                if isinstance(content, str):
                    prompts += 1
                elif isinstance(content, list):
                    had_text = False
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        if item.get("type") == "tool_result":
                            name = tool_name_by_id.get(item.get("tool_use_id", ""), "unknown")
                            body = item.get("content")
                            size = len(body) if isinstance(body, str) else len(json.dumps(body)) if body else 0
                            tool_bytes[name] += size
                            if item.get("is_error"):
                                tool_errors[name] += 1
                        elif item.get("type") == "text":
                            had_text = True
                    if had_text:
                        prompts += 1

    return {
        "tokens": {f"{m}|{t}": n for (m, t), n in tokens.items()},
        "requests": dict(requests),
        "tool_calls": dict(tool_calls),
        "tool_bytes": dict(tool_bytes),
        "tool_errors": dict(tool_errors),
        "prompts": prompts,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def read_denials(session_id: str) -> int:
    f = DENIALS_DIR / f"{session_id}.count"
    try:
        return int(f.read_text().strip() or 0)
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# counters -> OTLP/JSON
# ---------------------------------------------------------------------------


def _attr(k: str, v: str) -> dict:
    return {"key": k, "value": {"stringValue": str(v)}}


def _sum(name: str, points: list[dict], description: str = "") -> dict:
    return {
        "name": name,
        "description": description,
        "sum": {"aggregationTemporality": 2, "isMonotonic": True, "dataPoints": points},
    }


def build_payload(session_id: str, m: dict, denials: int, branch: str) -> dict:
    now = time.time_ns()
    start = m.get("first_ts") or now - 1
    base = [_attr("session_id", session_id), _attr("repo", REPO)]

    def pt(value: int, *extra: dict) -> dict:
        return {"asInt": str(value), "startTimeUnixNano": str(start), "timeUnixNano": str(now), "attributes": base + list(extra)}

    metrics = []
    tok_pts = []
    for key, n in sorted(m["tokens"].items()):
        model, typ = key.split("|", 1)
        tok_pts.append(pt(n, _attr("model", model), _attr("type", typ)))
    if tok_pts:
        metrics.append(_sum("ow_agent_tokens_total", tok_pts, "tokens by model and type, cumulative for the session"))
    if m["requests"]:
        metrics.append(_sum("ow_agent_requests_total", [pt(n, _attr("model", k)) for k, n in sorted(m["requests"].items())]))
    if m["tool_calls"]:
        metrics.append(_sum("ow_agent_tool_calls_total", [pt(n, _attr("tool", k)) for k, n in sorted(m["tool_calls"].items())]))
    if m["tool_bytes"]:
        metrics.append(_sum("ow_agent_tool_result_bytes_total", [pt(n, _attr("tool", k)) for k, n in sorted(m["tool_bytes"].items())]))
    if m["tool_errors"]:
        metrics.append(_sum("ow_agent_tool_errors_total", [pt(n, _attr("tool", k)) for k, n in sorted(m["tool_errors"].items())]))
    metrics.append(_sum("ow_agent_prompts_total", [pt(m["prompts"])]))
    metrics.append(_sum("ow_agent_upstream_guard_denials_total", [pt(denials)], "Bash commands denied by block_upstream_pr.py"))

    return {
        "resourceMetrics": [
            {
                "resource": {
                    "attributes": [
                        _attr("service.name", SERVICE),
                        _attr("repo", REPO),
                        _attr("host.name", socket.gethostname()),
                        _attr("git.branch", branch),
                    ]
                },
                "scopeMetrics": [{"scope": {"name": SERVICE, "version": "1"}, "metrics": metrics}],
            }
        ]
    }


# ---------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------


def _target() -> tuple[str, dict[str, str]]:
    url = os.environ.get("OW_AGENT_METRICS_URL", DEFAULT_URL)
    headers = {"Content-Type": "application/json"}
    org = os.environ.get("OW_AGENT_METRICS_ORG", "")
    if org:
        headers["X-Scope-OrgID"] = org
    fallback = os.environ.get("OW_AGENT_METRICS_FALLBACK_IP", "")
    if fallback:
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(url)
        try:
            socket.getaddrinfo(parts.hostname or "", parts.port or 80)
        except socket.gaierror:
            headers["Host"] = parts.netloc
            url = urlunsplit((parts.scheme, fallback + (f":{parts.port}" if parts.port else ""), parts.path, parts.query, ""))
    return url, headers


def post(payload: dict) -> None:
    url, headers = _target()
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 - fixed scheme from config
        if resp.status >= 300:
            raise RuntimeError(f"HTTP {resp.status}")


def push_with_retry(payload: dict) -> bool:
    delay = 1.0
    for attempt in range(1, 4):
        try:
            post(payload)
            return True
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, RuntimeError) as exc:
            log(f"push attempt {attempt} failed: {exc}")
            if attempt < 3:
                time.sleep(delay)
                delay *= 2
    return False


def spool(session_id: str, payload: dict) -> None:
    SPOOL_DIR.mkdir(parents=True, exist_ok=True)
    (SPOOL_DIR / f"{session_id}.json").write_text(json.dumps(payload))


def drain_spool(current: str) -> None:
    if not SPOOL_DIR.exists():
        return
    for f in sorted(SPOOL_DIR.glob("*.json")):
        if f.stem == current:
            continue
        try:
            post(json.loads(f.read_text()))
            f.unlink()
        except Exception as exc:  # noqa: BLE001
            log(f"spool drain of {f.name} failed: {exc}")
            break


def git_branch() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, timeout=3).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def main() -> int:
    if os.environ.get("OW_AGENT_METRICS_DISABLE") == "1":
        return 0
    try:
        hook = json.load(sys.stdin)
    except Exception as exc:  # noqa: BLE001
        log(f"could not read hook input: {exc}")
        return 0
    session_id = hook.get("session_id") or ""
    transcript = Path(hook.get("transcript_path") or "")
    if not session_id or not transcript.is_file():
        return 0
    try:
        m = measure(transcript)
        payload = build_payload(session_id, m, read_denials(session_id), git_branch())
        if push_with_retry(payload):
            drain_spool(session_id)
            (SPOOL_DIR / f"{session_id}.json").unlink(missing_ok=True)
        else:
            spool(session_id, payload)
            log("spooled snapshot; will retry on the next push")
    except Exception as exc:  # noqa: BLE001
        log(f"unexpected error: {exc!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
