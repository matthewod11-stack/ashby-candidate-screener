"""Candidate state management — dedup + eval cache for returning candidates.

state.json schema:
{
    "candidates": {
        "<email>": {
            "name": "Jiwei Guo",
            "candidate_id": "abc123",
            "first_seen_date": "2026-04-07",
            "last_report_date": "2026-04-14",
            "cached_eval": { ... EvaluatedCandidate dict ... } | null,
            "cached_synthesis": { ... SynthesizedCandidate dict ... } | null
        }
    },
    "last_run": "2026-04-14"
}

Usage:
    state = load_state()
    new_candidates = filter_new_candidates(candidates, state)
    # ... run triage, deep eval on new_candidates ...
    merged = merge_with_cached(new_evals, state)
    # ... synthesize merged list ...
    update_state(state, synthesis_results, today)
    save_state(state)
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

def load_state(data_dir: Path = Path("data")) -> dict:
    """Load state from disk, or return empty state if no file exists.

    Args:
        data_dir: Directory containing state.json. Defaults to 'data/'.
    """
    state_path = data_dir / "state.json"
    if not state_path.exists():
        return {"candidates": {}, "last_run": None}
    try:
        data = json.loads(state_path.read_text())
        if "candidates" not in data:
            data["candidates"] = {}
        return data
    except (json.JSONDecodeError, KeyError):
        print("  WARNING: state.json is corrupt, starting fresh")
        return {"candidates": {}, "last_run": None}


def save_state(state: dict, data_dir: Path = Path("data")) -> None:
    """Write state to disk.

    Args:
        state: State dict to persist.
        data_dir: Directory to write state.json into. Defaults to 'data/'.
    """
    state_path = data_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2))


def filter_new_candidates(candidates: list[dict], state: dict) -> list[dict]:
    """Return only candidates not already in state (by email).

    Candidates already in state have cached eval results and don't need
    to go through triage + deep eval again.

    Args:
        candidates: List of RawCandidate dicts from Stage 1.
        state: Current state dict.

    Returns:
        List of RawCandidate dicts that are NOT in state.
    """
    known_emails = set(state["candidates"].keys())
    new = []
    cached = 0

    for c in candidates:
        email = c.get("email", "").lower().strip()
        if email and email in known_emails:
            cached += 1
        else:
            new.append(c)

    if cached > 0:
        print(f"  State: {cached} candidates cached from prior runs, {len(new)} new")

    return new


def get_cached_evals(state: dict) -> list[dict]:
    """Return cached EvaluatedCandidate dicts for returning candidates.

    Only returns entries that have a non-null cached_eval.
    """
    cached = []
    for email, entry in state["candidates"].items():
        if entry.get("cached_eval"):
            cached.append(entry["cached_eval"])
    return cached


def update_state(
    state: dict,
    synthesis_results: list[dict],
    deep_eval_results: list[dict],
    today: str,
) -> dict:
    """Update state with results from this run.

    - New candidates get added with their eval results cached
    - Existing candidates get their last_report_date updated
    - Synthesis results update cached_synthesis for report persistence

    Args:
        state: Current state dict (mutated in place).
        synthesis_results: List of SynthesizedCandidate dicts from Stage 4.
        deep_eval_results: List of EvaluatedCandidate dicts from Stage 3.
        today: ISO date string.

    Returns:
        Updated state dict.
    """
    state["last_run"] = today

    # Cache deep eval results by email
    for ev in deep_eval_results:
        candidate = ev.get("candidate", {})
        email = candidate.get("email", "").lower().strip()
        if not email:
            continue

        if email not in state["candidates"]:
            state["candidates"][email] = {
                "name": candidate.get("name", "Unknown"),
                "candidate_id": candidate.get("candidate_id", ""),
                "first_seen_date": today,
                "last_report_date": today,
                "cached_eval": ev,
                "cached_synthesis": None,
            }
        else:
            # Update existing entry with fresh eval
            state["candidates"][email]["cached_eval"] = ev
            state["candidates"][email]["last_report_date"] = today

    # Cache synthesis results by email
    for synth in synthesis_results:
        evaluated = synth.get("evaluated", {})
        candidate = evaluated.get("candidate", {})
        email = candidate.get("email", "").lower().strip()
        if email and email in state["candidates"]:
            state["candidates"][email]["cached_synthesis"] = synth
            state["candidates"][email]["last_report_date"] = today

    return state


def get_candidate_first_seen(state: dict, email: str) -> str | None:
    """Get the first_seen_date for a candidate, or None if not in state."""
    email = email.lower().strip()
    entry = state["candidates"].get(email)
    return entry["first_seen_date"] if entry else None


def state_summary(state: dict) -> str:
    """Return a one-line summary of the current state."""
    n = len(state["candidates"])
    cached = sum(1 for e in state["candidates"].values() if e.get("cached_eval"))
    last_run = state.get("last_run", "never")
    return f"State: {n} candidates tracked, {cached} with cached evals, last run: {last_run}"
