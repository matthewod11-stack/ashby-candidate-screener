"""Shared data structures for the Ashby candidate ranking pipeline.

These dataclasses define the data that flows between stages.
Each stage reads from the previous stage's JSON output and writes its own.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class RawCandidate:
    """Stage 1 output — fetched from Ashby."""

    candidate_id: str
    name: str
    email: str
    application_date: str
    profile_url: str
    source: str | None = None
    status: str | None = None
    ashby_stage: str | None = None
    resume_path: str | None = None
    file_handle: str | None = None
    phone_number: str | None = None
    location_summary: str | None = None
    location_country: str | None = None
    timezone: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> RawCandidate:
        # Migration shim: legacy stage1 JSONs use `ashby_profile_url`. Read it
        # transparently as `profile_url` so cached runs survive the rename.
        if "ashby_profile_url" in d and "profile_url" not in d:
            d = {**d, "profile_url": d["ashby_profile_url"]}
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class TriagedCandidate:
    """Stage 2 output — Sonnet triage result."""

    candidate: RawCandidate
    triage_score: int  # 1-5
    triage_rationale: str
    passed: bool

    @classmethod
    def from_dict(cls, d: dict) -> TriagedCandidate:
        return cls(
            candidate=RawCandidate.from_dict(d["candidate"]),
            triage_score=d["triage_score"],
            triage_rationale=d["triage_rationale"],
            passed=d["passed"],
        )


@dataclass
class EvaluatedCandidate:
    """Stage 3 output — Opus deep eval result."""

    candidate: RawCandidate
    overall_score: int  # 0-100
    tier: str
    recommendation: str
    current_role: str
    prior_role: str
    education: str
    years_experience: str
    key_strengths: str
    key_concerns: str
    culture_fit: str
    flags: str
    skills_matrix: dict[str, int] = field(default_factory=dict)  # dimension -> 1-10

    @classmethod
    def from_dict(cls, d: dict) -> EvaluatedCandidate:
        candidate = RawCandidate.from_dict(d["candidate"])
        remaining = {k: v for k, v in d.items() if k != "candidate" and k in cls.__dataclass_fields__}
        return cls(candidate=candidate, **remaining)


@dataclass
class SynthesizedCandidate:
    """Stage 4 output — final ranked candidate."""

    evaluated: EvaluatedCandidate
    final_rank: int
    final_score: int
    final_tier: str
    is_new: bool
    first_seen_date: str
    interview_questions: list[dict] | None = None  # only for top-tier

    @classmethod
    def from_dict(cls, d: dict) -> SynthesizedCandidate:
        evaluated = EvaluatedCandidate.from_dict(d["evaluated"])
        remaining = {k: v for k, v in d.items() if k != "evaluated" and k in cls.__dataclass_fields__}
        return cls(evaluated=evaluated, **remaining)


# --- Serialization helpers ---


def save_json(data: list | dict, path: Path) -> None:
    """Write data to a JSON file, converting dataclasses as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def serialize(obj):
        if hasattr(obj, "__dataclass_fields__"):
            return asdict(obj)
        raise TypeError(f"Object of type {type(obj)} is not JSON serializable")

    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=serialize)


def load_json(path: Path) -> list | dict:
    """Read JSON data from a file."""
    with open(path) as f:
        return json.load(f)
