"""Generate a Slack-ready summary from synthesis results.

Produces data/results/slack_summary.json with:
- formatted message text (Slack mrkdwn)
- path to HTML report for attachment
- metadata for the scheduled task prompt

This is NOT a pipeline stage — it's called by the orchestrator after Stage 5.
The actual Slack posting is handled by the Claude Desktop scheduled task
via the Slack MCP connector.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from models import load_json


def generate_slack_summary(
    synthesis_path: Path,
    report_path: Path,
    config: dict,
) -> Path:
    """Generate a Slack summary from synthesis results.

    Args:
        synthesis_path: Path to stage4_synthesis.json.
        report_path: Path to the generated HTML report.
        config: Pipeline config dict.

    Returns:
        Path to slack_summary.json.
    """
    today = config.get("today", "unknown")
    role_title = config.get("role", {}).get("title", "Unknown Role")
    profile_label = config.get("ats", {}).get("profile_label", "View Profile")

    # Load synthesis results
    raw = load_json(synthesis_path)
    candidates = raw if isinstance(raw, list) else raw.get("candidates", [])

    # Filter by min_score if configured
    min_score = config.get("report", {}).get("min_score", 0)
    if min_score > 0:
        candidates = [c for c in candidates if c.get("final_score", 0) >= min_score]

    # Build the Slack message
    total_qualified = len(candidates)
    top_5 = candidates[:5]

    # Stats
    tier_counts = {}
    for c in candidates:
        tier = c.get("final_tier", "Unknown")
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

    # Format message in Slack mrkdwn
    lines = []
    lines.append(f"*{role_title} — Weekly Candidate Report*")
    lines.append(f"_{today}_")
    lines.append("")

    if total_qualified == 0:
        lines.append("No candidates met the threshold this week.")
        lines.append("")
        lines.append("Pipeline ran successfully — no qualified candidates to report.")
    else:
        # Stats line
        tier_summary = ", ".join(
            f"{count} {tier}" for tier, count in sorted(tier_counts.items())
        )
        lines.append(f"*{total_qualified} qualified candidates* ({tier_summary})")
        lines.append("")

        # Top 3
        lines.append("*Top candidates:*")
        for c in top_5:
            ev = c.get("evaluated", {})
            cand = ev.get("candidate", {})
            name = cand.get("name", "Unknown")
            score = c.get("final_score", "?")
            tier = c.get("final_tier", "?")
            current_role = ev.get("current_role", "")
            strength = ev.get("key_strengths", "")
            # Truncate strength to first sentence
            if ". " in strength:
                strength = strength[: strength.index(". ") + 1]
            if len(strength) > 120:
                strength = strength[:117] + "..."
            profile_url = cand.get("profile_url") or cand.get("ashby_profile_url", "")

            rank = c.get("final_rank", "?")
            lines.append(f"  *#{rank} {name}* — {score}/100 ({tier})")
            if current_role:
                lines.append(f"  _{current_role}_")
            if strength:
                lines.append(f"  {strength}")
            if profile_url:
                lines.append(f"  <{profile_url}|{profile_label}>")
            lines.append("")

    lines.append("_Full report attached below._")

    message = "\n".join(lines)

    # Write summary JSON
    summary = {
        "message": message,
        "report_path": str(report_path.resolve()),
        "role_title": role_title,
        "date": today,
        "total_qualified": total_qualified,
        "top_candidates": [
            {
                "name": c.get("evaluated", {}).get("candidate", {}).get("name", "?"),
                "score": c.get("final_score", 0),
                "tier": c.get("final_tier", "?"),
            }
            for c in top_5
        ],
    }

    data_dir = config.get("data_dir", Path("data"))
    output_path = Path(data_dir) / "results" / "slack_summary.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2))

    print(f"  Slack summary: {output_path}")
    return output_path


def generate_combined_slack_summary(
    results: list[tuple[dict, Path]],
    combined_report_path: Path | None = None,
) -> Path | None:
    """Generate a combined Slack summary covering all roles.

    Args:
        results: List of (role_config, synthesis_path) tuples.
        combined_report_path: Optional path to the combined HTML report.

    Returns:
        Path to slack_summary.json, or None if no results.
    """
    today = date.today().isoformat()
    # All roles in a single run share one engagement; pull the company name
    # from the first available role config and fall back if missing.
    first_config = results[0][0] if results else {}
    company_name = first_config.get("company", {}).get("name", "Hiring")
    lines = [f"*{company_name} — Weekly Candidate Report*", f"_{today}_", ""]

    total_qualified = 0
    role_count = 0
    for config, synth_path in results:
        if not synth_path.exists():
            continue
        role_title = config.get("role", {}).get("title", "Unknown")
        raw = load_json(synth_path)
        candidates = raw if isinstance(raw, list) else raw.get("candidates", [])
        qualified = len(candidates)
        total_qualified += qualified
        role_count += 1
        top_3 = candidates[:3]

        lines.append(f"*{role_title}* — {qualified} qualified")
        for c in top_3:
            ev = c.get("evaluated", {})
            name = ev.get("candidate", {}).get("name", "?")
            score = c.get("final_score", "?")
            lines.append(f"  #{c.get('final_rank', '?')} {name} ({score}/100)")
        lines.append("")

    lines.append(f"*Total: {total_qualified} qualified candidates across {role_count} roles*")
    if combined_report_path:
        lines.append(f"\n_Full report: {combined_report_path.resolve()}_")

    message = "\n".join(lines)

    output_path = Path("data") / "results" / "slack_summary.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary = {
        "message": message,
        "report_path": str(combined_report_path.resolve()) if combined_report_path else None,
        "date": today,
        "total_qualified": total_qualified,
        "roles": role_count,
    }
    output_path.write_text(json.dumps(summary, indent=2))
    print(f"  Combined Slack summary: {output_path}")
    return output_path
