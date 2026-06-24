"""Track Anthropic API token usage and compute costs across pipeline runs.

A UsageTracker is created per role run by `run.py`, threaded through the
stages via `config['usage_tracker']`. Each successful API call records one
entry. The orchestrator prints a per-stage summary and appends to
`data/roles/<slug>/usage.json` (a list of historical runs).

Prices are USD per million tokens, validated against the Anthropic console
on 2026-04-16. Update the PRICES dict (and this comment) when refreshing.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


# USD per million tokens. cache_read = 10% of input; cache_write = 1.25x input.
PRICES: dict[str, dict[str, float]] = {
    "opus":   {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75},
    "sonnet": {"input":  3.00, "output": 15.00, "cache_read": 0.30, "cache_write":  3.75},
    "haiku":  {"input":  1.00, "output":  5.00, "cache_read": 0.10, "cache_write":  1.25},
}


def _family_for(model: str) -> str:
    """Map a model id to its pricing family. Falls back to sonnet."""
    m = (model or "").lower()
    if "opus" in m:
        return "opus"
    if "haiku" in m:
        return "haiku"
    return "sonnet"


def _entry_cost(entry: dict[str, Any]) -> float:
    p = PRICES[_family_for(entry["model"])]
    return (
        entry["input_tokens"]                  * p["input"]       / 1_000_000
        + entry["output_tokens"]               * p["output"]      / 1_000_000
        + entry["cache_read_input_tokens"]     * p["cache_read"]  / 1_000_000
        + entry["cache_creation_input_tokens"] * p["cache_write"] / 1_000_000
    )


def _fmt_tok(n: int) -> str:
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n/1_000:.0f}K"
    return str(n)


class UsageTracker:
    """Accumulates per-call usage records and produces stage/total summaries."""

    STAGE_ORDER = ("triage", "deep_eval", "synthesize")

    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    def add(self, stage: str, model: str, usage: Any) -> None:
        """Record one API call. `usage` is the SDK's response.usage object."""
        if usage is None:
            return
        self.entries.append({
            "stage": stage,
            "model": model,
            "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
            "cache_read_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            "cache_creation_input_tokens": int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        })

    def stage_summary(self, stage: str) -> dict[str, Any]:
        rows = [e for e in self.entries if e["stage"] == stage]
        if not rows:
            return {"calls": 0, "model": "", "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0}
        return {
            "calls": len(rows),
            "model": rows[0]["model"],
            "input_tokens": sum(e["input_tokens"] for e in rows),
            "output_tokens": sum(e["output_tokens"] for e in rows),
            "cache_read_tokens": sum(e["cache_read_input_tokens"] for e in rows),
            "cache_write_tokens": sum(e["cache_creation_input_tokens"] for e in rows),
            "cost_usd": round(sum(_entry_cost(e) for e in rows), 4),
        }

    def total(self) -> dict[str, Any]:
        stages = {s: self.stage_summary(s) for s in self.STAGE_ORDER if any(e["stage"] == s for e in self.entries)}
        return {"stages": stages, "total_cost_usd": round(sum(s["cost_usd"] for s in stages.values()), 4)}

    def format_summary(self) -> str:
        if not self.entries:
            return "=== Run Cost ===\n  (no API calls recorded)"
        lines = ["=== Run Cost ==="]
        for stage in self.STAGE_ORDER:
            s = self.stage_summary(stage)
            if s["calls"] == 0:
                continue
            family = _family_for(s["model"])
            lines.append(
                f"  {stage:11s} ({family:6s}): {s['calls']:>5} calls | "
                f"{_fmt_tok(s['input_tokens']):>7} in / {_fmt_tok(s['output_tokens']):>6} out | "
                f"${s['cost_usd']:>7.2f}"
            )
        total = self.total()
        lines.append(f"  {'Total':<24}: ${total['total_cost_usd']:.2f}")
        return "\n".join(lines)

    def write(self, data_dir: Path) -> Path:
        """Append this run's summary to data_dir/usage.json (history list)."""
        path = data_dir / "usage.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict] = []
        if path.exists():
            try:
                loaded = json.loads(path.read_text())
                if isinstance(loaded, list):
                    existing = loaded
            except (json.JSONDecodeError, OSError):
                existing = []
        existing.append({
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "summary": self.total(),
            "entries": self.entries,
        })
        path.write_text(json.dumps(existing, indent=2))
        return path
