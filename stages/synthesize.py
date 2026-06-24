"""Stage 4: Opus synthesis — relative ranking, tier assignment, interview questions.

Single Opus call taking all deep eval results. Produces final ranking with
dynamic tier boundaries, recency weighting, and interview questions for top tier.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import anthropic
from dotenv import load_dotenv

from models import (
    EvaluatedCandidate,
    SynthesizedCandidate,
    load_json,
    save_json,
)
from usage import UsageTracker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

def _build_synthesis_system_prompt(config: dict) -> str:
    """Build the synthesis system prompt dynamically from config."""
    role_title = config.get("role", {}).get("title", "Staff Backend Engineer")
    company_name = config.get("company", {}).get("name", "the company")

    lines = [
        f"You are a VP of Engineering making final hiring decisions for a "
        f"{role_title} position at {company_name}. You have deep eval results for all "
        f"surviving candidates and must produce a final ranked list.",
        "",
        "## Your Task",
        "",
        "1. **Relative Ranking:** Compare candidates AGAINST EACH OTHER, not just "
        "against criteria. If two candidates both scored 85 overall, differentiate "
        "them based on their strongest differentiating skills, authenticity signal, "
        "startup fit, and recency.",
        "",
        "2. **Dynamic Tier Assignment:** Calibrate tier boundaries based on the actual "
        "score distribution in THIS candidate pool. Do NOT use fixed cutoffs. Example: "
        "if 8 candidates all score 85+, all 8 are Tier 1. If only 2 candidates score "
        "above 75, only 2 are Tier 1. Tiers should reflect natural clustering.",
        "   - Tier 1 – Top: Best candidates in this pool. Would confidently recommend.",
        "   - Tier 2 – Strong: Good candidates worth interviewing if Tier 1 doesn't work out.",
        "   - Tier 3 – Maybe: Qualified but notable gaps. Interview only if pool is thin.",
        "   - Tier 4 – Pass: Does not meet the bar for this role.",
        "",
        "3. **Recency Weighting:** When candidates are otherwise comparable, prefer "
        "more recent applications. A strong candidate who applied last week is slightly "
        "preferred over an equally strong candidate who applied 3 months ago.",
        "",
        "4. **No Hard Count Cutoff:** All qualified candidates should appear in the "
        "output. The quality threshold determines who's included, not an arbitrary "
        "number.",
        "",
        "5. **Interview Questions:** Generate 3-5 tailored probing questions ONLY for "
        "top-tier candidates. Questions should:",
        "   - Probe specific claims from their resume",
        "   - Test depth of knowledge in their strongest claimed areas",
        "   - Explore leadership and architecture decision-making",
        "   - Assess authenticity (ask about specific projects, metrics, decisions)",
        "   - Be unique to each candidate — not generic",
        "",
        "## Output Format",
        "",
        "Respond with a JSON object containing a single key \"candidates\" with a list. "
        "Each entry should have:",
        "",
        "{",
        '  "candidates": [',
        "    {",
        '      "candidate_id": "<id>",',
        '      "name": "<name>",',
        '      "final_rank": 1,',
        '      "final_score": 92,',
        '      "final_tier": "Tier 1 – Top",',
        '      "ranking_rationale": "<2-3 sentences explaining ranking relative to peers>",',
        '      "interview_questions": [',
        "        {",
        '          "question": "<specific probing question>",',
        '          "intent": "<what you\'re testing with this question>"',
        "        }",
        "      ]",
        "    }",
        "  ]",
        "}",
        "",
        "interview_questions should be null for candidates not in the top tier (only "
        "generate for the top N candidates as specified).",
        "",
        "Order the list by final_rank (1 = best).",
        "",
        "Respond ONLY with the JSON object. No markdown fences, no explanation.",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _build_synthesis_prompt(
    evals: list[EvaluatedCandidate],
    top_n: int,
    today: str,
) -> str:
    """Build the user message containing all eval results for synthesis."""
    parts = [
        f"## Synthesis Request\n",
        f"**Today's Date:** {today}",
        f"**Total Candidates:** {len(evals)}",
        f"**Generate interview questions for top:** {top_n} candidates\n",
        "## Candidate Evaluations\n",
        "Below are the deep evaluation results for all surviving candidates. ",
        "Review them holistically, compare against each other, and produce ",
        "a final ranking.\n",
    ]

    for i, ev in enumerate(evals, 1):
        entry = {
            "candidate_id": ev.candidate.candidate_id,
            "name": ev.candidate.name,
            "application_date": ev.candidate.application_date,
            "source": ev.candidate.source,
            "overall_score": ev.overall_score,
            "tier": ev.tier,
            "recommendation": ev.recommendation,
            "current_role": ev.current_role,
            "prior_role": ev.prior_role,
            "education": ev.education,
            "years_experience": ev.years_experience,
            "key_strengths": ev.key_strengths,
            "key_concerns": ev.key_concerns,
            "culture_fit": ev.culture_fit,
            "flags": ev.flags,
            "skills_matrix": ev.skills_matrix,
        }
        parts.append(f"### Candidate {i}: {ev.candidate.name}")
        parts.append(json.dumps(entry, indent=2))
        parts.append("")

    return "\n".join(parts)


def _parse_synthesis_response(text: str) -> list[dict]:
    """Parse the JSON synthesis response from Claude."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(cleaned[start:end])
        else:
            raise ValueError(
                f"Could not parse synthesis response: {text[:300]}"
            )

    # Handle both {"candidates": [...]} and bare [...]
    if isinstance(result, dict):
        candidates = result.get("candidates", [])
    elif isinstance(result, list):
        candidates = result
    else:
        raise ValueError(f"Unexpected synthesis response type: {type(result)}")

    return candidates


def _determine_is_new(application_date: str, today: str) -> bool:
    """Determine if a candidate is 'new' (applied within last 14 days)."""
    try:
        app_date = date.fromisoformat(application_date[:10])
        today_date = date.fromisoformat(today)
        delta = (today_date - app_date).days
        return delta <= 14
    except (ValueError, TypeError):
        return False


def _synthesized_to_dict(s: SynthesizedCandidate) -> dict:
    """Convert a SynthesizedCandidate to a serializable dict."""
    ev = s.evaluated
    return {
        "evaluated": {
            "candidate": ev.candidate.__dict__,
            "overall_score": ev.overall_score,
            "tier": ev.tier,
            "recommendation": ev.recommendation,
            "current_role": ev.current_role,
            "prior_role": ev.prior_role,
            "education": ev.education,
            "years_experience": ev.years_experience,
            "key_strengths": ev.key_strengths,
            "key_concerns": ev.key_concerns,
            "culture_fit": ev.culture_fit,
            "flags": ev.flags,
            "skills_matrix": ev.skills_matrix,
        },
        "final_rank": s.final_rank,
        "final_score": s.final_score,
        "final_tier": s.final_tier,
        "is_new": s.is_new,
        "first_seen_date": s.first_seen_date,
        "interview_questions": s.interview_questions,
    }


async def _async_run_synthesize(config: dict, evals_path: Path) -> Path:
    """Async core of run_synthesize."""
    load_dotenv()
    data_dir = Path(config.get("data_dir", "data"))
    output_path = data_dir / "results" / "stage4_synthesis.json"
    raw_path = data_dir / "results" / "stage4_synthesis_raw.txt"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    model = config.get("synthesis", {}).get("model", "claude-opus-4-6")
    system_prompt = _build_synthesis_system_prompt(config)

    # Load deep eval results
    eval_data = load_json(evals_path)
    evals = [EvaluatedCandidate.from_dict(d) for d in eval_data]

    # Filter out zero-score (error) entries
    valid_evals = [e for e in evals if e.overall_score > 0]
    print(
        f"Stage 4 (Synthesis): {len(valid_evals)} candidates to synthesize "
        f"({len(evals) - len(valid_evals)} filtered out with score=0)"
    )

    if not valid_evals:
        print("  No valid candidates to synthesize.")
        save_json([], output_path)
        return output_path

    # Cap synthesis input to bound the Opus context window. Default 50 was
    # chosen empirically — 181 candidates in one call triggered token limits
    # (see PROGRESS.md 2026-04-14). Override per role via synthesis.max_candidates.
    max_candidates = int(config.get("synthesis", {}).get("max_candidates", 50))
    valid_evals.sort(key=lambda e: e.overall_score, reverse=True)
    overflow_evals: list[EvaluatedCandidate] = []
    if len(valid_evals) > max_candidates:
        overflow_evals = valid_evals[max_candidates:]
        valid_evals = valid_evals[:max_candidates]
        print(
            f"  Sending top {len(valid_evals)} to Opus (score >= {valid_evals[-1].overall_score}). "
            f"{len(overflow_evals)} lower-scoring candidates auto-assigned Tier 4."
        )

    top_n = config.get("synthesis", {}).get("top_n_for_interview_questions", 8)
    today = config.get("today", date.today().isoformat())

    # Build prompt and call Opus
    user_message = _build_synthesis_prompt(valid_evals, top_n, today)

    # Resumable: if a prior run already got the API response but crashed
    # during parsing, reuse the cached raw text rather than re-paying for Opus.
    if raw_path.exists():
        print(f"  Found cached raw synthesis at {raw_path} — skipping Opus call.")
        response_text = raw_path.read_text()
    else:
        client = anthropic.AsyncAnthropic()

        print(f"  Sending {len(valid_evals)} evaluations to Opus for synthesis...")
        response = await client.messages.create(
            model=model,
            max_tokens=16384,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )

        tracker: UsageTracker | None = config.get("_usage_tracker")
        if tracker is not None:
            tracker.add("synthesize", model, response.usage)

        response_text = response.content[0].text
        # Persist raw response BEFORE parsing so a parse crash is recoverable.
        raw_path.write_text(response_text)

    ranked_candidates = _parse_synthesis_response(response_text)

    # Build lookup from candidate_id to EvaluatedCandidate
    eval_lookup: dict[str, EvaluatedCandidate] = {
        e.candidate.candidate_id: e for e in valid_evals
    }

    # Build SynthesizedCandidate list
    results: list[SynthesizedCandidate] = []
    tier_counts: dict[str, int] = {}

    for ranked in ranked_candidates:
        cid = ranked.get("candidate_id", "")
        if cid not in eval_lookup:
            print(f"  WARNING: Synthesis returned unknown candidate_id: {cid}")
            continue

        ev = eval_lookup[cid]
        final_tier = ranked.get("final_tier", "Tier 4 – Pass")

        # Track tier distribution
        tier_counts[final_tier] = tier_counts.get(final_tier, 0) + 1

        # Interview questions: only for top-tier
        interview_qs = ranked.get("interview_questions")
        if interview_qs and not isinstance(interview_qs, list):
            interview_qs = None

        app_date = ev.candidate.application_date
        is_new = _determine_is_new(app_date, today)

        synth = SynthesizedCandidate(
            evaluated=ev,
            final_rank=ranked.get("final_rank", 0),
            final_score=ranked.get("final_score", ev.overall_score),
            final_tier=final_tier,
            is_new=is_new,
            first_seen_date=app_date,
            interview_questions=interview_qs,
        )
        results.append(synth)

    # Sort by final_rank
    results.sort(key=lambda s: s.final_rank)

    # Append overflow candidates (auto Tier 4, sorted by score desc)
    if overflow_evals:
        next_rank = max((r.final_rank for r in results), default=0) + 1
        for e in overflow_evals:
            app_date = e.candidate.application_date
            synth = SynthesizedCandidate(
                evaluated=e,
                final_rank=next_rank,
                final_score=e.overall_score,
                final_tier="Tier 4 – Pass",
                is_new=_determine_is_new(app_date, today),
                first_seen_date=app_date,
                interview_questions=None,
            )
            results.append(synth)
            tier_counts["Tier 4 – Pass"] = tier_counts.get("Tier 4 – Pass", 0) + 1
            next_rank += 1

    # Check for candidates in eval_lookup that weren't ranked
    ranked_ids = {r.get("candidate_id") for r in ranked_candidates}
    overflow_ids = {e.candidate.candidate_id for e in overflow_evals}
    unranked = [
        e for cid, e in eval_lookup.items()
        if cid not in ranked_ids and cid not in overflow_ids
    ]
    if unranked:
        print(
            f"  WARNING: {len(unranked)} candidates were not ranked by "
            f"synthesis. Adding them at the end."
        )
        next_rank = max((r.final_rank for r in results), default=0) + 1
        for e in unranked:
            app_date = e.candidate.application_date
            synth = SynthesizedCandidate(
                evaluated=e,
                final_rank=next_rank,
                final_score=e.overall_score,
                final_tier="Tier 4 – Pass",
                is_new=_determine_is_new(app_date, today),
                first_seen_date=app_date,
                interview_questions=None,
            )
            results.append(synth)
            next_rank += 1

    # Save output
    save_json([_synthesized_to_dict(s) for s in results], output_path)

    # Summary
    tier_summary = ", ".join(
        f"{tier}: {count}" for tier, count in sorted(tier_counts.items())
    )
    top_candidate = results[0] if results else None
    top_name = top_candidate.evaluated.candidate.name if top_candidate else "N/A"
    top_score = top_candidate.final_score if top_candidate else 0

    print(
        f"  Synthesized {len(results)} candidates. {tier_summary}. "
        f"Top candidate: {top_name} (score: {top_score})"
    )

    return output_path


# ---------------------------------------------------------------------------
# Public entry point (sync wrapper)
# ---------------------------------------------------------------------------


def run_synthesize(config: dict, evals_path: Path) -> Path:
    """Run Opus synthesis on all deep eval results.

    Args:
        config: Parsed criteria.yaml + env vars.
        evals_path: Path to stage3_deep_evals.json.

    Returns:
        Path to stage4_synthesis.json output file.
    """
    return asyncio.run(_async_run_synthesize(config, evals_path))
