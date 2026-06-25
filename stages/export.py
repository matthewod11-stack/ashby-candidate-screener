"""Shortlist exports — CSV + Markdown to hand to a hiring manager.

Writers take normalized rows (see stages._shared.shortlist_rows). The
combined_shortlist_rows helper merges those rows across roles for the
"who to meet this week" view. Composes _shared + models; not a pipeline stage.
"""

from __future__ import annotations

import csv
from pathlib import Path

from models import load_json
from stages._shared import shortlist_rows

SHORTLIST_COLUMNS = [
    "role",
    "name",
    "tier",
    "score",
    "recommendation",
    "current_role",
    "years_experience",
    "email",
    "profile_url",
]


def write_shortlist_csv(rows: list[dict], path: Path) -> Path:
    """Write shortlist rows to a CSV with a stable column order."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SHORTLIST_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def write_shortlist_md(rows: list[dict], path: Path, title: str = "Shortlist") -> Path:
    """Write shortlist rows as a Markdown table (paste into Slack/email/Linear)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# {title}",
        "",
        f"{len(rows)} candidate(s) worth a conversation.",
        "",
        "| # | Name | Role | Tier | Score | Recommendation | Current role |",
        "|---|------|------|------|-------|----------------|--------------|",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"| {i} | {r.get('name', '')} | {r.get('role', '')} | {r.get('tier', '')} | "
            f"{r.get('score', '')} | {r.get('recommendation', '')} | {r.get('current_role', '')} |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def combined_shortlist_rows(results: list[tuple[dict, Path]]) -> list[dict]:
    """Merge top-two-tier rows across roles, sorted by score descending.

    Args:
        results: list of (role_config, synthesis_path) tuples.
    """
    rows: list[dict] = []
    for cfg, synth_path in results:
        synth_path = Path(synth_path)
        if synth_path.exists():
            role_title = cfg.get("role", {}).get("title", "Unknown")
            rows.extend(shortlist_rows(load_json(synth_path), role_title))
    rows.sort(key=lambda r: r["score"], reverse=True)
    return rows
