#!/usr/bin/env python3
"""Offline checks for agent_telemetry.py: parse a synthetic transcript, price it, build the payload.

Run from anywhere: python3 .claude/hooks/test_agent_telemetry.py
Exit 0 on success, 1 with a message on the first failure. Never contacts the collector.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

os.environ["OW_AGENT_METRICS_DISABLE"] = "1"

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("agent_telemetry", HERE / "agent_telemetry.py")
assert spec is not None and spec.loader is not None
t = importlib.util.module_from_spec(spec)
spec.loader.exec_module(t)

FABLE = "claude-fable-5-1"
HAIKU = "claude-haiku-4-5-20251001"
UNKNOWN = "claude-mystery-9"


def usage(inp: int = 0, w5: int = 0, w1: int = 0, read: int = 0, out: int = 0) -> dict:
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_input_tokens": read,
        "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1},
    }


def assistant(rid: str, model: str, u: dict, *content: dict, ts: str = "2026-01-01T00:00:00Z") -> dict:
    return {"type": "assistant", "requestId": rid, "timestamp": ts, "message": {"model": model, "usage": u, "content": list(content)}}


def tool_use(tid: str, name: str) -> dict:
    return {"type": "tool_use", "id": tid, "name": name}


def user(*content, ts: str = "2026-01-01T00:00:01Z") -> dict:
    body = content[0] if len(content) == 1 and isinstance(content[0], str) else list(content)
    return {"type": "user", "timestamp": ts, "message": {"content": body}}


LINES = [
    user("first prompt"),
    # r1: one streamed request written as two lines with the same usage; issues Bash and Read
    assistant("r1", FABLE, usage(inp=1_000_000, w1=1_000_000, read=1_000_000, out=1_000_000), tool_use("t1", "Bash")),
    assistant("r1", FABLE, usage(inp=1_000_000, w1=1_000_000, read=1_000_000, out=1_000_000), tool_use("t2", "Read")),
    user({"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "boom"}, {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}),
    # r2: no tool issued, 5m cache write, priced at the haiku rate via prefix match
    assistant("r2", HAIKU, usage(inp=2_000_000, w5=1_000_000)),
    # r3: unknown model, must not be priced; follows a 2-hour pause, so its 4M cache write is idle recache
    assistant("r3", UNKNOWN, usage(inp=5, out=5, w1=4_000_000), tool_use("t3", "Bash"), ts="2026-01-01T02:00:00Z"),
    user({"type": "tool_result", "tool_use_id": "t3", "content": "x"}),
    # sidechain (subagent) line, ignored entirely
    {"type": "assistant", "isSidechain": True, "requestId": "r9", "message": {"model": FABLE, "usage": usage(inp=99), "content": [tool_use("t9", "Grep")]}},
    # two subagent completion notices, as the harness injects them: one as a plain string, one as a text block
    user("<task-notification><usage><subagent_tokens>87536</subagent_tokens><tool_uses>15</tool_uses></usage></task-notification>"),
    user({"type": "text", "text": "done <usage><subagent_tokens>1000</subagent_tokens></usage>"}),
]


def main() -> int:
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        for line in LINES:
            fh.write(json.dumps(line) + "\n")
        path = Path(fh.name)
    try:
        m = t.measure(path)
    finally:
        path.unlink(missing_ok=True)

    checks = [
        ("requests counted once per requestId", m["requests"] == {FABLE: 1, HAIKU: 1, UNKNOWN: 1}),
        ("tokens summed once per request", m["tokens"][f"{FABLE}|cache_write_1h"] == 1_000_000 and m["tokens"][f"{FABLE}|input"] == 1_000_000),
        ("tool calls across streamed lines", m["tool_calls"] == {"Bash": 2, "Read": 1}),
        ("tool errors and bytes by tool", m["tool_errors"] == {"Bash": 1} and m["tool_bytes"] == {"Bash": 5, "Read": 2}),
        ("prompts count the real prompt and the two notices", m["prompts"] == 3),
        ("subagent tokens summed from the notices", m["subagent_tokens"] == 88536),
        ("cache write after a 2h pause counted as idle recache", m["idle_recache"] == 4_000_000),
        # fable 5.1: 1M input $10 + 1M 1h write $20 + 1M read $0.25 + 1M output $50 = $80.25
        ("fable priced from the table", abs(m["cost"][FABLE] - 80.25) < 1e-9),
        # haiku 4.5 by prefix: 2M input $2 + 1M 5m write $1.25 = $3.25
        ("haiku priced via longest prefix", abs(m["cost"][HAIKU] - 3.25) < 1e-9),
        ("unknown model never priced", UNKNOWN not in m["cost"] and m["unpriced"] == {UNKNOWN: 1}),
        ("r1 cost split evenly across the two tools it issued", abs(m["tool_cost"][f"Bash|{FABLE}"] - 40.125) < 1e-9 and abs(m["tool_cost"][f"Read|{FABLE}"] - 40.125) < 1e-9),
        ("request issuing no tool lands on tool=none", abs(m["tool_cost"][f"none|{HAIKU}"] - 3.25) < 1e-9),
        ("unpriced request contributes no tool cost", f"Bash|{UNKNOWN}" not in m["tool_cost"]),
    ]

    p = t.build_payload("test-session", m, 3, "test-branch")
    metrics = {x["name"]: x for x in p["resourceMetrics"][0]["scopeMetrics"][0]["metrics"]}
    cost_pts = metrics.get("ow_agent_cost_usd", {}).get("sum", {}).get("dataPoints", [])
    tool_pts = metrics.get("ow_agent_tool_cost_usd", {}).get("sum", {}).get("dataPoints", [])
    checks += [
        ("payload carries the cost metrics", {"ow_agent_cost_usd", "ow_agent_tool_cost_usd", "ow_agent_unpriced_requests_total"} <= set(metrics)),
        ("cost points are doubles with model label", all("asDouble" in d and any(a["key"] == "model" for a in d["attributes"]) for d in cost_pts) and len(cost_pts) == 2),
        ("tool cost points carry tool and model", all({a["key"] for a in d["attributes"]} >= {"tool", "model", "session_id", "repo"} for d in tool_pts) and len(tool_pts) == 3),
        ("cost sums are cumulative monotonic", all(metrics[n]["sum"]["isMonotonic"] and metrics[n]["sum"]["aggregationTemporality"] == 2 for n in ("ow_agent_cost_usd", "ow_agent_tool_cost_usd"))),
        ("denials point present", metrics["ow_agent_upstream_guard_denials_total"]["sum"]["dataPoints"][0]["asInt"] == "3"),
        ("subagent tokens emitted", metrics["ow_agent_subagent_tokens_total"]["sum"]["dataPoints"][0]["asInt"] == "88536"),
    ]

    checks.append(("idle recache emitted", metrics["ow_agent_idle_recache_tokens_total"]["sum"]["dataPoints"][0]["asInt"] == "4000000"))

    # idle notice: the last request in LINES is r3 at 02:00 on an unknown model (no price) with a 4M-token context
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as fh:
        for line in LINES:
            fh.write(json.dumps(line) + "\n")
        path = Path(fh.name)
    try:
        t0 = t.parse_ts("2026-01-01T02:00:00Z")
        quiet = t.idle_notice(path, t0 + 30 * 60 * 10**9)
        loud = t.idle_notice(path, t0 + 3 * 3600 * 10**9)
        fable_lines = LINES[:2] + [user("x", ts="2026-01-01T00:00:02Z")]  # last request r1 on fable: 1M input + 1M read + 1M 1h write = 3M context, at the 1h write rate $20/M = $60
        fh2 = Path(tempfile.mkstemp(suffix=".jsonl")[1])
        fh2.write_text("".join(json.dumps(line) + "\n" for line in fable_lines))
        priced = t.idle_notice(fh2, t.parse_ts("2026-01-01T00:00:00Z") + 2 * 3600 * 10**9)
        fh2.unlink()
    finally:
        path.unlink(missing_ok=True)
    checks += [
        ("no notice under the idle threshold", quiet == ""),
        ("notice after a long pause names the gap and the context size", "180 min idle" in loud and "4000k tokens" in loud and "$" not in loud),
        ("notice prices the re-write on a priced model", "$60.00" in priced and "3000k tokens" in priced and "120 min idle" in priced),
    ]

    failed = [name for name, ok in checks if not ok]
    if failed:
        print("agent_telemetry: FAILED " + "; ".join(failed), file=sys.stderr)
        print(json.dumps({k: m[k] for k in ("requests", "cost", "tool_cost", "unpriced", "tool_calls")}, indent=1, default=str), file=sys.stderr)
        return 1
    print(f"agent_telemetry: {len(checks)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
